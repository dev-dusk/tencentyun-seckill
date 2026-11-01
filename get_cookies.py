"""扫码登录腾讯云, 保存 cookies.json, 并核实/更新活动参数到 activity_params.json.

流程 (全部在可视化 Chromium 中):
  1. 打开腾讯云登录页 -> 用户扫码
  2. 跳到任意 act/pro/* 活动页即视为登录成功 -> 保存 cookies.json
  3. 人脸核身确认 (未通过则打开验证页等用户完成, 状态写入 config.json)
  4. 打开 settings.json 指定的活动页, 解析 __NEXT_DATA__ 核实商品/价格参数,
     更新 activity_params.json (页面可证实部分)
  5. 网络捕获 act-api 请求 (只记 URL + 请求体, 绝不记录 Cookie/请求头):
     - check-available: 页面加载秒杀区时自动发出, 被动捕获
     - do-goods: 需要用户在页面上手动点一次目标商品的【立即抢购】按钮
       (非场次时间服务器会拒绝, 不会产生订单); 捕获后校验 act_id/type
       与目标一致才写入, 防止抓到别的商品
"""

import contextlib
import json
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

import tcutil as tc

BASE_DIR = Path(__file__).resolve().parent
COOKIES_PATH = BASE_DIR / "cookies.json"
CONFIG_PATH = BASE_DIR / "config.json"
CAPTURE_DIR = tc.LOG_DIR

# 登录入口(稳定); 登录后跳到哪一期活动页则自动捕获
LOGIN_URL = "https://cloud.tencent.com/login"

FACE_URL = "https://cloud.tencent.com/act/pro/face_authentication"
FACE_POLL_S = 15
FACE_TIMEOUT_S = 600  # 验证页最多等 10 分钟


def query_face_pass(cookies):
    """查人脸核身状态, 返回 True/False/None(查不到). 永不抛异常."""
    try:
        session = requests.Session()
        skey = None
        for cookie in cookies:
            session.cookies.set(
                cookie.get("name", ""),
                cookie.get("value", ""),
                domain=cookie.get("domain", ""),
                path=cookie.get("path", "/"),
            )
            if (
                cookie.get("name") == "skey"
                and cookie.get("value")
                and (skey is None or cookie.get("domain") == ".cloud.tencent.com")
            ):
                skey = cookie["value"]
        if not skey:
            return None
        headers = {
            "x-csrf-token": tc.compute_g_tk(skey),
            "Content-Type": "application/json",
            "User-Agent": tc.USER_AGENT,
            "referer": FACE_URL,
        }
        resp = session.post(
            "https://act-api.cloud.tencent.com/user/get-facecheck-info",
            json={},
            headers=headers,
            timeout=10,
        )
        result = resp.json()
        if result.get("code") != 0:
            return None
        return bool((result.get("data") or {}).get("ever_pass"))
    except Exception as e:
        print(f"⚠️ 人脸状态查询失败: {e}")
        return None


def ensure_face_verified(page, cookies):
    """登录后人脸核身确认, 返回 (ever_pass, checked_at)."""
    checked_at = tc.bj_str()
    passed = query_face_pass(cookies)
    if passed is True:
        print("✅ 人脸核身已通过, 无需额外操作")
        return True, checked_at
    if passed is not False:
        print("⚠️ 未能确认人脸状态, 请开抢前手动确认是否需核身")
        return None, checked_at
    print("⛔ 账号需人脸核身校验, 否则抢购必报 1100202!")
    print("👉 已在当前浏览器打开验证页, 请按页完成验证 (微信扫一扫+人脸识别)")
    try:
        page.goto(FACE_URL)
    except Exception as e:
        print(f"⚠️ 验证页打开失败, 请手动访问: {FACE_URL} ({e})")
    deadline = time.time() + FACE_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(FACE_POLL_S)
        if query_face_pass(cookies) is True:
            print("✅ 人脸核身已通过!")
            return True, tc.bj_str()
        print("⌛ 等待人脸核身完成...")
    print(f"⚠️ 超时未检测到通过, 请开抢前手动确认: {FACE_URL}")
    return False, checked_at


class ApiCapture:
    """被动记录 act-api 请求与 do-goods 响应 (只存 URL/方法/请求体/返回码, 不碰 Cookie 与请求头)."""

    def __init__(self):
        self.records = []
        self.do_goods_responses = []

    def on_request(self, request):
        try:
            if "act-api.cloud.tencent.com" not in request.url:
                return
            post_data = request.post_data
            payload = None
            if post_data:
                with contextlib.suppress(Exception):
                    payload = json.loads(post_data)
            self.records.append(
                {"url": request.url, "method": request.method, "payload": payload}
            )
            short = request.url.split("cloud.tencent.com")[-1]
            print(f"📡 捕获 act-api 请求: {short} (累计 {len(self.records)})")
        except Exception:
            pass

    def on_response(self, response):
        """记录 do-goods 响应: 抓包引导期间万一真的建单, 必须立刻发现."""
        try:
            if "dianshi/do-goods" not in response.url:
                return
            body = None
            with contextlib.suppress(Exception):
                body = response.json()
            self.do_goods_responses.append({"url": response.url, "status": response.status, "body": body})
        except Exception:
            pass

    def check_no_real_order(self):
        """抓包期间若有 do-goods 返回 code=0 (真实建单), 返回该响应; 否则 None."""
        for rec in self.do_goods_responses:
            body = rec.get("body")
            if isinstance(body, dict) and body.get("code") == 0:
                return rec
        return None

    def find(self, keyword):
        return [r for r in self.records if keyword in r["url"] and r.get("payload")]


def extract_capture_into_params(capture, params, settings):
    """把捕获到的 check-available / do-goods 模板写入 activity_params (带校验)."""
    cap = params.setdefault("capture", {})
    checks = capture.find("/dianshi/check-available")
    if checks:
        # 优先选包含目标商品 act_id 且带 region_id 的模板 (秒杀卡的真实库存查询)
        want = str(params.get("BUY_ACT_ID"))
        preferred = [
            r for r in checks
            if want in json.dumps(r.get("payload") or {}) and "region_id" in json.dumps(r.get("payload") or {})
        ]
        chosen = (preferred or checks)[-1]
        cap["check_available"] = chosen
        params["verified_items"]["check_api"] = {
            "verified": True, "at": tc.bj_str(),
            "source": "登录时网络捕获" + ("(含目标商品+全量地域)" if preferred else "(通用模板)"),
        }
        print(f"✅ 已捕获 check-available 请求模板: {json.dumps(chosen['payload'], ensure_ascii=False)[:200]}")

    buys = capture.find("/dianshi/do-goods")
    target = settings.get("target", {})
    for rec in reversed(buys):
        payload = rec["payload"]
        goods = payload.get("goods") or []
        if not (isinstance(goods, list) and goods):
            continue
        g = goods[0] if isinstance(goods[0], dict) else {}
        gp = g.get("goods_param") or {}
        ok_act = str(g.get("act_id")) == str(params.get("BUY_ACT_ID"))
        ok_type = (g.get("type") == target.get("sku_type")
                   or gp.get("type") == target.get("sku_type"))
        if ok_act and ok_type:
            cap["do_goods"] = rec
            if isinstance(payload.get("business"), dict):
                params["BUSINESS"] = payload["business"]
            else:
                # 页面请求确实不带 business 字段: 记录为显式省略, 下单按捕获体原样复用
                params["BUSINESS"] = None
                cap["business_omitted"] = True
            params["verified_items"]["buy_api"] = {
                "verified": True, "at": tc.bj_str(), "source": "登录时网络捕获(非场次时间, 服务器已拒绝)"
            }
            print("✅ 已捕获 do-goods 请求模板 (act_id/type 与目标商品核对一致)")
            if params.get("BUSINESS"):
                print(f"   business = {params['BUSINESS']}")
            else:
                print("   页面请求未携带 business 字段 (将按下单体原样复用)")
            return True
        else:
            print(f"⚠️ 捕获到 do-goods 但与目标商品不符 (act_id={g.get('act_id')}, type={g.get('type')}), 已丢弃")
    return bool(cap.get("do_goods"))


def in_active_session_window(parsed, now_ms, margin_start_ms=300_000):
    """当前是否处于任一场次的进行窗口 (开始前 margin_start_ms 也算危险期)."""
    for s in parsed.get("sessions", []):
        start, end = s.get("start_ms"), s.get("end_ms")
        if start and end and (start - margin_start_ms) <= now_ms <= end:
            return True, s
    return False, None


def sync_page_params(page, settings, params):
    """在活动页上解析 __NEXT_DATA__, 核实并更新 activity_params 页面参数部分."""
    # EO 挑战页需要时间自动重载: 显式等 __NEXT_DATA__ 出现
    with contextlib.suppress(Exception):
        page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
    html = page.content()
    parsed = tc.parse_activity(html, settings.get("target", {}))
    if not parsed.get("ok"):
        print(f"⚠️ 活动页解析失败: {parsed.get('error')}")
        return parsed
    if not parsed.get("sale"):
        print("⚠️ 活动页未找到目标商品 (SKU 不匹配?), 页面参数未更新")
        return parsed
    sale = parsed["sale"]
    params["ename"] = parsed.get("ename", "")
    params["activity_url"] = settings.get("activity_url")
    params["ACTIVITY_ID"] = parsed["activity_id"]
    params["CHECK_ACT_ID"] = sale["act_id"]
    params["BUY_ACT_ID"] = sale["act_id"]
    params["SKU"] = sale["sku_type"]
    page_param = dict(sale.get("default_goods_param") or {})
    page_param.pop("regionId", None)
    params["GOODS_PARAM"] = {**(params.get("GOODS_PARAM") or {}), **page_param}
    params["PRICE_REAL_COST_FEN"] = sale.get("price_fen")
    params["REGION_MAP"] = {str(r["value"]): r["label"] for r in sale.get("region_map") or []}
    params["verified_items"]["page_params"] = {
        "verified": True, "at": tc.bj_str(), "source": "登录浏览器内活动页实测"
    }
    print(f"✅ 页面参数已核实并更新: activity_id={params['ACTIVITY_ID']} "
          f"act_id={params['BUY_ACT_ID']} SKU={params['SKU']} "
          f"实付={(sale.get('price_fen') or 0) / 100:.2f}元")
    rpt = tc.verify_target(parsed, settings.get("target", {}))
    rpt.dump()
    return parsed


def main():
    log_path = tc.setup_logging("login")
    print("🚀 启动扫码登录 (Playwright 可视化 Chromium)")
    print(f"📝 日志: {log_path}")

    settings = tc.load_settings()
    params = tc.load_activity_params()
    act_url = settings.get("activity_url")
    print(f"🎯 目标活动页: {act_url}")

    capture = ApiCapture()
    # activity_params 结构兜底 (缺文件/旧版本时保证必备键存在)
    params.setdefault("verified_items", {})
    params["verified_items"].setdefault("page_params", {"verified": False})
    params["verified_items"].setdefault("check_api", {"verified": False})
    params["verified_items"].setdefault("buy_api", {"verified": False})
    params.setdefault("capture", {})
    params.setdefault("GOODS_PARAM", {})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        # 统一 UA: 登录态疑似与 UA 绑定, 登录/恢复/请求必须同一个 User-Agent
        context = browser.new_context(user_agent=tc.USER_AGENT)
        page = context.new_page()
        context.on("request", capture.on_request)
        context.on("response", capture.on_response)

        # 1) 扫码登录
        # 注意: 等待模式必须锚定路径开头 —— 登录页 URL 的 query 里带着
        # s_url=...act/pro/..., 宽松通配会瞬间误匹配, 导致没等扫码就"成功"
        import re as _re

        page.goto(f"{LOGIN_URL}?s_url={act_url}")
        print("请扫描二维码登录腾讯云...")
        page.wait_for_url(_re.compile(r"^https://cloud\.tencent\.com/act/pro/[^?]+"), timeout=0)
        page.wait_for_timeout(2000)
        # 登录校验: 必须拿到 skey 才算登录成功 (匿名 cookie 一律拒收)
        pre_cookies = context.cookies()
        if not any(c["name"] == "skey" and c.get("value") for c in pre_cookies):
            print("⛔ 页面已跳转但未检测到 skey 登录态, cookie 不保存 (可能未完成扫码)")
            return 1
        print(f"登录成功! 当前页面: {page.url}")

        # 2) 保存登录态
        cookies = context.cookies()
        tc.save_json(COOKIES_PATH, cookies)
        skey_cookies = [c for c in cookies if c["name"] in ("skey", "uin", "qqcloud_key")]
        print(f"已保存 {len(cookies)} 个 Cookie (含登录态标记: {[c['name'] for c in skey_cookies]})")

        # 3) 人脸核身
        face_pass, face_checked = ensure_face_verified(page, cookies)

        # 4) 打开目标活动页, 核实参数 (顺带被动捕获 check-available)
        print(f"👉 正在打开活动页核实商品参数: {act_url}")
        page.goto(act_url)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(6000)  # 等秒杀区渲染 + 页面自动发 check-available
        sync_page_params(page, settings, params)

        # 5) 全自动提取已捕获模板 (check-available 页面加载秒杀区时会自动发)
        #    注: buy_api 的验证状态只允许实测结论更新, 这里绝不覆写
        #    (2026-11-01 实测: API 引擎请求形状合法, verified=true 已另行写入)
        extract_capture_into_params(capture, params, settings)
        params["verified_items"].setdefault(
            "buy_api",
            {
                "verified": False,
                "engine": "browser",
                "note": "do-goods 需页面请求层 withSign 签名; 未实测前默认不通过",
            },
        )

        # 6) 安全复核: 抓包引导期间若有 do-goods 真的建单 (code=0), 必须立刻告知
        real_order = capture.check_no_real_order()
        if real_order:
            print("🚨🚨🚨 警告: 刚才捕获引导期间的点击疑似已创建真实订单 (do-goods 返回 code=0)!")
            print(f"    返回: {json.dumps(real_order.get('body'), ensure_ascii=False)[:300]}")
            print("    若并非本意, 请立刻到订单中心取消: https://console.cloud.tencent.com/expense/order")

        # 7) 落盘: activity_params + config.json (向后兼容旧字段)
        tc.save_json(tc.ACTIVITY_PARAMS_PATH, params)
        auto_config = tc.load_auto_config()
        auto_config["act_url"] = act_url
        auto_config["updated"] = tc.bj_str()
        auto_config["facecheck"] = {"ever_pass": face_pass, "checked": face_checked}
        tc.save_json(CONFIG_PATH, auto_config)
        print("活动参数已写入 activity_params.json; act_url/人脸状态已写入 config.json")

        # 8) 抓包原始记录归档 (仅 URL+请求体/返回码, 无凭证)
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        tc.save_json(CAPTURE_DIR / f"capture_{tc.bj_now().strftime('%Y%m%d_%H%M%S')}.json",
                     {"requests": capture.records, "do_goods_responses": capture.do_goods_responses})

        with contextlib.suppress(Exception):
            browser.close()  # 用户可能已手动关窗
        print("浏览器已关闭")

    print("─" * 62)
    ok_page = params.get("verified_items", {}).get("page_params", {}).get("verified")
    ok_check = params.get("verified_items", {}).get("check_api", {}).get("verified")
    print(f"结果: 页面参数 {'✅已核实' if ok_page else '❌未核实'} | "
          f"库存模板 {'✅已捕获' if ok_check else '⏳未捕获'} | 下单引擎=浏览器驱动(官方页面)")
    print("下一步: 运行 check.bat 做抢购前总检查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
