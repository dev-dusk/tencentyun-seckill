"""腾讯云轻量服务器秒杀主程序 (Windows 适配版).

流程: 加载配置 -> 拉取当前活动页核实商品/价格 -> 对时 -> 等待开抢 ->
限频重试下单 -> 成功即停并弹官方付款页 (付款永远由用户手动完成).

三种模式:
  check  只读检查 (环境/登录/商品/价格/场次/资格), 绝不下单
  dry    模拟运行 (全流程演练, 不发真实下单请求, 不弹浏览器)
  real   真实下单 (需 settings.runtime.mode=real + --real + 交互确认 YES)

用法:
  python snap_up_server.py --mode check
  python snap_up_server.py --list-sessions
  python snap_up_server.py --mode dry
  python snap_up_server.py --mode real --real --yes
  python snap_up_server.py --mode dry --at "2026-11-02 15:00:00"
"""

import argparse
import copy
import json
import random
import re
import sys
import time
import webbrowser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import requests

import tcutil as tc

CHECK_API = "https://act-api.cloud.tencent.com/dianshi/check-available"
BUY_API = "https://act-api.cloud.tencent.com/dianshi/do-goods"
FACE_API = "https://act-api.cloud.tencent.com/user/get-facecheck-info"

# do-goods 返回码分类 (2026-09 两场实战沉淀 + 2026-11 沿用待复核)
FATAL_CODES = {1100202}  # 确定性失败(如人脸核身): 重试无意义, 直接停
AUTH_CODES = {"CSRF-ERROR", "NOT-LOGINED"}  # 会话失效: 直接停
RATE_CODES = {408}  # 触发限流: 指数退避后继续
UNPAID_CODES = {1101411}  # 有未支付订单: 停手弹订单页

CODE_TAGS = {
    0: "成功",
    1100202: "需人脸",
    408: "限流",
    1100205: "繁忙",
    1100136: "未开始",
    1101001: "未开抢",
    1101411: "有未付单",
    None: "异常",
}


def code_tag(code):
    return CODE_TAGS.get(code, str(code))


def hist_line(hist):
    return ", ".join(
        f"{code_tag(code)}×{n}"
        for code, n in sorted(hist.items(), key=lambda kv: (-kv[1], str(kv[0])))
    )


# ---------------- 活动页拉取与核实 ----------------
def fetch_activity(session, url):
    """拉取活动页 HTML (带登录态, SSR 的 isLogined 可作登录旁证).

    纯 HTTP 请求可能拿到 EO 反爬挑战页 (无 __NEXT_DATA__):
    此时回退用 Playwright 真实 Chromium 渲染 (与正常浏览器行为一致).
    """
    headers = {
        "User-Agent": tc.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
    resp = session.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    if "__NEXT_DATA__" in resp.text:
        return resp.text
    print("ℹ️ 直连请求被防护页拦截 (EO 挑战页), 改用 Playwright Chromium 渲染活动页...")
    return tc.fetch_html_via_playwright(url, with_cookies=tc.COOKIES_PATH.exists())


# ---------------- 库存/资格/人脸 (只读接口) ----------------
def check_available(session, headers, params, region_ids):
    """查库存, 返回 (接口正常, 有货地域列表, 原始返回).

    请求模板优先用登录时捕获的真实模板 (activity_params.capture.check_available),
    未捕获时用历史形状兜底. 绝不在此函数里下单.
    """
    captured = (params.get("capture") or {}).get("check_available")
    if captured and isinstance(captured, dict):
        payload = copy.deepcopy(captured.get("payload") or captured)
    else:
        payload = {
            "activity_id": params["ACTIVITY_ID"],
            "goods": [{"act_id": params["CHECK_ACT_ID"], "region_id": list(region_ids)}],
            "preview": 0,
        }
    try:
        resp = session.post(CHECK_API, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        print(f"❌ 库存检查接口调用失败: {e}")
        return False, [], None
    if result.get("code") != 0 or result.get("msg") not in ("ok", None, ""):
        print(f"❌ 库存检查接口返回异常: {json.dumps(result, ensure_ascii=False)[:300]}")
        if result.get("code") in AUTH_CODES or result.get("msg") in AUTH_CODES:
            print("   => 登录态失效, 请重新运行 login.bat 扫码")
        return False, [], result
    data = result.get("data") or []
    goods_data = data[0] if data and isinstance(data[0], dict) else {}
    quota = goods_data.get("quota") or {}
    sku = params.get("SKU")
    in_stock = [
        rid
        for rid in region_ids
        if (quota.get(str(rid)) or {}).get(sku, {}).get("available", 0) > 0
    ]
    # 资格信息 (2026-11-01 实测返回: user_available/idcard_available/total_num)
    for key in ("user_available", "idcard_available", "total_num"):
        if key in goods_data:
            print(f"   资格[{key}] = {goods_data.get(key)}")
    return True, in_stock, result


def face_from_config():
    """登录脚本写入的人脸状态兜底 (24 小时内有效, 北京时间). 查不到返回 None."""
    record = tc.load_auto_config().get("facecheck") or {}
    if record.get("ever_pass") is not True:
        return None
    try:
        checked = tc.parse_bj(record.get("checked", ""))
    except ValueError:
        return None
    if tc.bj_now() - checked < timedelta(hours=24):
        print(f"ℹ️ 沿用登录时的人脸状态 (已通过, {record['checked']} 记录)")
        return True
    return None


def check_face(session, headers):
    """查人脸核身状态, True(已通过)/False(需核身)/None(查不到)."""
    try:
        resp = session.post(FACE_API, json={}, headers=headers, timeout=10)
        result = resp.json()
    except Exception as e:
        print(f"⚠️ 人脸核身状态查询失败: {e}")
        return face_from_config()
    if result.get("code") != 0:
        print(f"⚠️ 人脸核身状态查询异常: {str(result)[:200]}")
        return face_from_config()
    return bool((result.get("data") or {}).get("ever_pass"))


# ---------------- 下单 ----------------
def build_buy_payload(params, region_id, act_url):
    """构造 do-goods 请求体. 优先用捕获模板, 其次按历史形状+参数拼装."""
    captured = (params.get("capture") or {}).get("do_goods")
    if captured and isinstance(captured, dict):
        data = copy.deepcopy(captured.get("payload") or captured)
        goods = data.get("goods")
        if isinstance(goods, list) and goods and isinstance(goods[0], dict):
            gp = goods[0].get("goods_param")
            if isinstance(gp, dict):
                gp["regionId"] = region_id
                gp["goodsNum"] = params["GOODS_PARAM"].get("goodsNum", 1)
                gp["timeSpanUnit"] = params["GOODS_PARAM"].get("timeSpanUnit", "12m")
        return data
    goods_param = copy.deepcopy(params["GOODS_PARAM"])
    goods_param["regionId"] = region_id
    data = {
        "activity_id": params["ACTIVITY_ID"],
        "agent_channel": {"fromChannel": "", "fromSales": "", "isAgentClient": False, "fromUrl": act_url},
        "goods": [{"act_id": params["BUY_ACT_ID"], "type": params["SKU"], "goods_param": goods_param}],
        "preview": 0,
    }
    business = params.get("BUSINESS")
    if business:
        data["business"] = dict(business)
    return data


def buy_now(session, headers, params, region_id, act_url, timeout_s, verbose=False):
    """对单个地域提交一次下单请求, 返回 (region_id, 结果dict/None)."""
    data = build_buy_payload(params, region_id, act_url)
    try:
        resp = session.post(BUY_API, json=data, headers=headers, timeout=timeout_s)
        if verbose:
            print(f"🎯 下单返回 [{region_id}]: {resp.text[:500]}")
        return region_id, resp.json()
    except Exception as e:
        if verbose:
            print(f"❌ 下单调用失败 [{region_id}]: {e}")
        return region_id, None


def result_code(result):
    return result.get("code") if isinstance(result, dict) else None


def next_backoff(consec_rate, cfg):
    """限流指数退避: 第 1 次用基础值, 之后翻倍, 封顶 backoff_max_s."""
    if consec_rate <= 1:
        return float(cfg["backoff_408_s"])
    return min(float(cfg["backoff_408_s"]) * (2 ** (consec_rate - 1)), float(cfg["backoff_max_s"]))


def extract_order_url(result):
    """从 1101411 等返回的 msg 里抠订单链接 (bigDealId 优先)."""
    msg = result.get("msg", "") if isinstance(result, dict) else ""
    if not isinstance(msg, str) or not msg:
        return None
    m = re.search(r"bigDealId=(\d+)", msg)
    if m:
        return f"https://console.cloud.tencent.com/expense/deal/info?bigDealId={m.group(1)}"
    m = re.search(r'href="(//console\.cloud\.tencent\.com[^"]+)"', msg)
    if m:
        return "https:" + m.group(1)
    return None


PAY_URL_KEYS = frozenset(
    ["pay_url", "payurl", "cashier_url", "cashierurl", "pay_link", "paylink",
     "redirect_url", "redirecturl", "pay_gate_url"]
)
ORDER_ID_KEYS = frozenset(
    ["order_id", "orderid", "order_no", "orderno", "deal_id", "dealid",
     "bill_id", "billid", "transaction_id", "transactionid", "bigdealsn"]
)
ORDER_KEYS = PAY_URL_KEYS | ORDER_ID_KEYS


def find_keys(obj, found):
    if isinstance(obj, dict):
        items = list(obj.items())
    elif isinstance(obj, list):
        items = list(enumerate(obj))
    else:
        return found
    for k, v in items:
        if isinstance(k, str) and k.lower() in ORDER_KEYS:
            found.setdefault(k.lower(), []).append(v)
        find_keys(v, found)
    return found


def extract_order_info(result):
    info = {"order_ids": [], "pay_urls": []}
    for key, vals in find_keys(result, {}).items():
        for v in vals:
            if key in PAY_URL_KEYS and isinstance(v, str) and v.startswith("http") and v not in info["pay_urls"]:
                info["pay_urls"].append(v)
            elif key in ORDER_ID_KEYS and v not in (None, "") and str(v) not in [str(x) for x in info["order_ids"]]:
                info["order_ids"].append(v)
    return info


def response_price_fen(result):
    """从成功返回体里找订单金额字段 (分). 找不到返回 None."""
    money = {}

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = k.lower()
                if isinstance(v, (int, float)) and any(
                    t in lk for t in ("totalcost", "realcost", "totalfee", "cost", "amount", "price")
                ):
                    money[lk] = v
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(result)
    for prefer in ("totalcost", "realcost", "totalfee"):
        for k, v in money.items():
            if prefer in k:
                return int(v)
    if money:
        return int(list(money.values())[0])
    return None


def open_url_safely(url, dry):
    """打开官方付款/订单页; 模拟模式只打印不打开."""
    if dry:
        print(f"🌐 [模拟] 已抑制打开浏览器: {url}")
        return True
    try:
        webbrowser.open(url)
        print("🌐 已弹出浏览器页面 (付款/处理请人工完成, 程序绝不自动支付)")
        return True
    except Exception as e:
        print(f"⚠️ 自动打开页面失败 ({e}), 请手动访问: {url}")
        return False


def handle_unpaid(order_url, dry):
    print("🛑 检测到未支付订单 (含下单超时但实际已建单的场景), 继续重试无意义, 收手")
    if order_url and order_url.startswith("http"):
        print(f"🧾 订单页: {order_url}")
        open_url_safely(order_url, dry)
    else:
        print("ℹ️ 请去控制台订单中心处理未支付订单: https://console.cloud.tencent.com/expense/order")


def buy_round(session, headers, params, region_ids, round_no, act_url, cfg, dry):
    """一轮并发下单 (dry 模式由 SimulationProvider 出结果).

    返回 (outcome, detail, hist, payload):
    success=抢到 / fatal=确定性失败 / unpaid=有未付单 / rate_limited=限流 / retry=继续
    """
    if not region_ids:
        return "retry", "空地域列表", {}, None
    hist = Counter()
    hits = []
    if dry:
        results = SIM.provide(round_no, region_ids)
    else:
        with ThreadPoolExecutor(max_workers=len(region_ids)) as executor:
            futures = {executor.submit(buy_now, session, headers, params, rid, act_url,
                                       cfg["buy_timeout_s"], cfg["verbose"]): rid for rid in region_ids}
            results = [f.result() for f in as_completed(futures)]
    for rid, result in results:
        code = result_code(result)
        if code == 0:
            print(f"—— 第 {round_no} 轮 —— 🎉 下单成功! region_id={rid}")
            return "success", rid, dict(hist), result
        hist[code] += 1
        hits.append((code, result))
    print(f"—— 第 {round_no} 轮 —— {hist_line(hist)}")
    for code, result in hits:
        if code in UNPAID_CODES:
            return "unpaid", extract_order_url(result), dict(hist), None
    for code, result in hits:
        if code in FATAL_CODES or code in AUTH_CODES:
            msg = result.get("msg", "") if isinstance(result, dict) else result
            return "fatal", f"code={code} {str(msg)[:150]}", dict(hist), None
    if any(code in RATE_CODES for code, _ in hits):
        return "rate_limited", "触发限流", dict(hist), None
    return "retry", "继续", dict(hist), None


class SimulationProvider:
    """dry 模式的模拟响应序列: 未开始 -> 限流(退避) -> 超时(不算失败) -> 繁忙 -> 成功.

    绝不发真实请求; 结果为本地构造的 dict, 与真实接口字段形状一致.
    (未付单 1101411 停止路径由 tests/test_buy_flow.py 覆盖)
    """

    def __init__(self):
        self.seq = [
            ("not_start", 1100136),
            ("rate", 408),
            ("timeout", None),
            ("busy", 1100205),
            ("success", 0),
        ]
        self.i = 0

    def provide(self, round_no, region_ids):
        kind, code = self.seq[min(self.i, len(self.seq) - 1)]
        out = []
        for rid in region_ids:
            if kind == "not_start":
                out.append((rid, {"code": 1100136, "msg": "不在抢购时间"}))
            elif kind == "rate":
                out.append((rid, {"code": 408, "msg": "请求过于频繁"}))
            elif kind == "timeout":
                out.append((rid, None))  # 模拟网络超时 (服务端可能已建单, 下一轮由 1101411 揭示)
            elif kind == "busy":
                out.append((rid, {"code": 1100205, "msg": "系统繁忙"}))
            elif kind == "success":
                out.append((rid, {
                    "code": 0, "msg": "ok",
                    "data": {"dealName": "轻量应用服务器Lighthouse", "bigDealId": "38000261111000002",
                             "totalCost": 3800, "payUrl": "https://console.cloud.tencent.com/expense/deal/info?bigDealId=38000261111000002"},
                }))
        self.i += 1
        return out


SIM = SimulationProvider()


def handle_success(payload, max_price_yuan, dry, open_browser):
    print("🎉 下单成功! 返回体摘要:")
    if isinstance(payload, dict):
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
    else:
        print(payload)
        return
    info = extract_order_info(payload)
    if info["order_ids"]:
        print(f"🧾 订单号: {', '.join(str(x) for x in info['order_ids'])}")
    # 成功后的价格复核: 返回体金额若可解析且超红线, 大声警告 (单已建, 提示不要支付)
    fen = response_price_fen(payload)
    if fen is not None:
        yuan = fen / 100.0 if fen > 1000 else fen
        if yuan <= max_price_yuan + 1e-9:
            print(f"✅ 订单金额复核通过: {yuan:.2f} 元 <= 红线 {max_price_yuan} 元")
        else:
            print(f"🚨🚨 订单金额 {yuan:.2f} 元超出红线 {max_price_yuan} 元! 请勿支付, 立即去订单中心取消该订单!")
    else:
        print("ℹ️ 返回体未含金额字段, 请在付款页人工核对金额后再支付")
    if info["pay_urls"]:
        print(f"💳 付款链接: {info['pay_urls'][0]}")
        if open_browser:
            open_url_safely(info["pay_urls"][0], dry)
        else:
            print("ℹ️ 已按配置不自动打开浏览器, 请手动打开上面链接付款")
    else:
        print("ℹ️ 未解析到直接付款链接, 请去控制台订单中心付款: https://console.cloud.tencent.com/expense/order")


# ---------------- 等待 ----------------
def wait_until(target_ms, clock, tag=""):
    """睡到目标时刻 (校准钟), 临近 5 秒加密轮询."""
    while True:
        remain = target_ms - clock.now_ms()
        if remain <= 0:
            return
        if remain > 5000:
            time.sleep(min(1.0, remain / 2000.0))
        else:
            print(f"⏳ 临近开抢{('[' + tag + ']') if tag else ''}, 剩余 {remain}ms")
            time.sleep(min(0.2, remain / 2000.0))


# ---------------- 预检汇总 ----------------
COOKIES_OK = False  # preflight 步骤 0 置位: cookies.json 成功加载


def preflight(settings, params, mode):
    """开抢前总自检, 返回 (report, parsed, session, headers, face)."""
    global COOKIES_OK
    print("═" * 62)
    print(f"[预检] 模式: {mode.upper()}{' (模拟, 不发真实下单请求)' if mode == 'dry' else ''}")
    print(f"[预检] 北京时间: {tc.bj_str()}")
    act_url = settings.get("activity_url") or params.get("activity_url")
    print(f"[预检] 活动页: {act_url}")
    target = settings.get("target", {})
    print(f"[预检] 目标: SKU={target.get('sku_type')} 价格红线 {target.get('max_price_yuan')} 元 "
          f"时长 {target.get('time_span_unit')} 地域优先 {target.get('region_priority')}")
    print("═" * 62)

    # 0) 登录 Cookie (失败则后续接口检查全部受限, 但页面公开数据仍可查)
    session = requests.Session()
    csrf_token = None
    try:
        session, csrf_token, token_source = tc.build_session()
        COOKIES_OK = True
        print(f"🔑 x-csrf-token 来源: {token_source}")
    except FileNotFoundError as e:
        print(f"⚠️ {e}")

    # 1) 活动页解析 + 规格价格校验 (价格红线含在内)
    try:
        html = fetch_activity(session, act_url)
    except Exception as e:
        print(f"❌ 活动页拉取失败: {e}")
        rpt = tc.CheckReport()
        rpt.add("活动页可达", "fail", str(e)[:200])
        return rpt, None, session, None, None
    parsed = tc.parse_activity(html, target)
    rpt = tc.verify_target(parsed, target)
    if parsed.get("ok"):
        logined = parsed.get("is_logined")
        if logined:
            rpt.add("登录状态(SSR旁证)", "pass", "活动页识别为已登录")
        else:
            rpt.add("登录状态(SSR旁证)", "pending", "活动页显示未登录 (cookies 可能缺失/过期), 以接口实测为准")

    # 2) 活动参数与页面实测一致性
    if parsed.get("ok") and parsed.get("sale"):
        sale = parsed["sale"]
        if int(parsed["activity_id"]) == int(params.get("ACTIVITY_ID", -1)):
            rpt.add("ACTIVITY_ID 一致", "pass", f"{parsed['activity_id']}")
        else:
            rpt.add("ACTIVITY_ID 一致", "fail",
                    f"页面 {parsed['activity_id']} != 配置 {params.get('ACTIVITY_ID')} (活动已换期, 请重跑 login.bat 更新)")
        if str(sale.get("act_id")) == str(params.get("BUY_ACT_ID")):
            rpt.add("BUY_ACT_ID 一致", "pass", f"{sale.get('act_id')}")
        else:
            rpt.add("BUY_ACT_ID 一致", "fail",
                    f"页面 {sale.get('act_id')} != 配置 {params.get('BUY_ACT_ID')}")
        # 商品参数模板同步 (imageId 等可能随期变化, 优先采用页面值)
        page_param = sale.get("default_goods_param") or {}
        cfg_param = params.get("GOODS_PARAM") or {}
        drift = {k: {"page": page_param.get(k), "cfg": cfg_param.get(k)}
                 for k in set(page_param) | set(cfg_param)
                 if k not in ("regionId",) and page_param.get(k) != cfg_param.get(k)}
        if drift:
            print(f"ℹ️ 页面商品参数与配置存在差异 (将优先采用页面值): {drift}")
            params["GOODS_PARAM"] = {**cfg_param, **page_param}
        # 页面实测价格写回活动参数留档
        if sale.get("price_fen") is not None:
            params["PRICE_REAL_COST_FEN"] = sale["price_fen"]

    # 3) 人脸核身
    headers = tc.make_headers(csrf_token, act_url)
    face = check_face(session, headers) if csrf_token else None
    if face is True:
        rpt.add("人脸核身", "pass", "ever_pass=true (1100202 风险解除)")
    elif face is False:
        rpt.add("人脸核身", "fail",
                "账号需人脸核身, 开抢必报 1100202! 请先完成: https://cloud.tencent.com/act/pro/face_authentication")
    else:
        rpt.add("人脸核身", "pending", "未能确认 (查询失败且无本地记录)")

    # 4) 库存接口连通 (只读)
    regions_all = [r["value"] for r in ((parsed.get("sale") or {}).get("region_map") or [])]
    if not regions_all:
        regions_all = list(settings["target"]["region_priority"])
    if csrf_token:
        ok, in_stock, raw = check_available(session, headers, params, regions_all)
        if ok:
            g0 = ((raw or {}).get("data") or [{}])[0] if isinstance((raw or {}).get("data"), list) else {}
            elig = []
            if g0.get("user_available") == 1:
                elig.append("账号有购买资格")
            elif g0.get("user_available") == 0:
                elig.append("账号无购买资格(可能不满足新用户限制)")
            if g0.get("idcard_available") == 1:
                elig.append("证件可购")
            rpt.add("库存接口(check-available)", "pass",
                    f"连通正常; {'; '.join(elig) if elig else '资格字段未返回'}; "
                    f"当前有货地域: {in_stock if in_stock else '无 (未开抢属正常)'}")
            if g0.get("user_available") == 0 and mode == "real":
                rpt.add("购买资格(接口)", "fail", "check-available 返回 user_available=0, 真实下单一律停止")
        else:
            msg = ""
            if isinstance(raw, dict):
                msg = f"code={raw.get('code')} msg={str(raw.get('msg'))[:80]}"
            rpt.add("库存接口(check-available)", "pending",
                    f"未通过 ({msg or '网络异常'}); 登录态或请求模板待登录捕获核实")
    else:
        rpt.add("库存接口(check-available)", "pending", "无登录 Cookie, 未调用 (请先 login.bat)")

    # 4.5) query-price 实时核价 (免登录接口, 价格红线第二重复核)
    try:
        qp_payload = {
            "activity_id": params["ACTIVITY_ID"],
            "goods": [{"act_id": params["BUY_ACT_ID"], "type": "lighthouse_v5",
                       "goods_param": {**params["GOODS_PARAM"], "regionId": settings["target"]["region_priority"][0]},
                       "page_from": "act_buy"}],
            "preview": 0,
        }
        resp = session.post("https://act-api.cloud.tencent.com/dianshi/query-price",
                            json=qp_payload, headers=headers, timeout=10)
        qd = resp.json()
        if qd.get("code") == 0 and isinstance(qd.get("data"), list) and qd["data"]:
            fen = qd["data"][0].get("real_cost")
            yuan = (fen or 0) / 100.0
            if yuan <= float(target.get("max_price_yuan", 0)) + 1e-9:
                rpt.add("实时核价(query-price)", "pass", f"接口实时实付 {yuan:.2f} 元/年 (real_cost={fen}分)")
            else:
                rpt.add("实时核价(query-price)", "fail", f"接口实时实付 {yuan:.2f} 元 > 红线, 一律拒绝下单")
        else:
            rpt.add("实时核价(query-price)", "pending", f"接口返回异常: {str(qd)[:100]}")
    except Exception as e:
        rpt.add("实时核价(query-price)", "pending", f"调用失败: {e}")

    # 5) 真实下单额外门槛 (缺一即禁止 real)
    if mode == "real":
        if face is not True:
            rpt.add("真实下单门槛", "fail", "人脸核身未确认为已通过, 按规则禁止真实下单")
        if not COOKIES_OK:
            rpt.add("登录 Cookie", "fail", "cookies.json 缺失, 真实下单前必须先扫码登录")
        # 实时登录校验: check-available 必须实测通过 (防 cookie 过夜失效;
        # 人脸状态有 24h 本地兜底记录, 不能作为登录有效的证据)
        chk = next((i for i in rpt.items if i.name.startswith("库存接口")), None)
        if chk is not None and chk.status != "pass":
            chk.status = "fail"
            chk.detail += " [real 门槛: 必须实时确认登录态有效, 请重新 login.bat]"
        price_item = next((i for i in rpt.items if i.name.startswith("价格红线")), None)
        if price_item is not None and price_item.status != "pass":
            # 价格是最高优先级约束: 无法确认价格同样禁止真实下单, 不许带糊下单
            price_item.status = "fail"
            price_item.detail += " [real 门槛: 价格必须确认为 pass 才允许真实下单]"
        buy_info = params.get("verified_items", {}).get("buy_api", {})
        if buy_info.get("verified"):
            rpt.add("下单接口(do-goods)", "pass",
                    f"请求形状已实测通过 ({buy_info.get('source', '')}); 引擎={buy_info.get('engine', 'api')}")
        else:
            rpt.add("下单接口(do-goods)", "fail", str(buy_info.get("note", "未验证"))[:150])
    if rpt.pending:
        print("⏳ 待确认项 (不阻断 check/dry, 真实下单前须人工核实):")
        for item in rpt.pending:
            print(f"   {item}")
    return rpt, parsed, session, headers, face


def pick_regions(parsed, settings):
    """实际开打地域: 页面可售 ∩ 用户优先级, 按优先级排序."""
    page_regions = [r["value"] for r in ((parsed.get("sale") or {}).get("region_map") or [])]
    priority = settings["target"]["region_priority"]
    if page_regions:
        return [r for r in priority if r in page_regions] or page_regions
    return list(priority)


def main():
    parser = argparse.ArgumentParser(description="腾讯云轻量服务器秒杀 (check/dry/real)")
    parser.add_argument("--mode", choices=["check", "dry", "real"], default=None,
                        help="check=只读检查 dry=模拟 real=真实下单(需确认)")
    parser.add_argument("--at", default=None, help="手动指定秒杀时间 'YYYY-MM-DD HH:MM:SS' (北京时间)")
    parser.add_argument("--list-sessions", action="store_true", help="列出页面所有场次后退出")
    parser.add_argument("--real", action="store_true", help="真实下单二次开关 (与 mode=real 同时需要)")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认 (慎用)")
    args = parser.parse_args()

    log_path = tc.setup_logging("snap")
    print("🚀 腾讯云秒杀脚本启动 (Windows 适配版)")
    print(f"📝 日志: {log_path}")

    settings = tc.load_settings()
    params = tc.load_activity_params()
    mode = args.mode or settings["runtime"]["mode"]
    if args.at:
        settings["seckill"]["manual_datetime"] = args.at
        settings["seckill"]["auto_detect_session"] = False

    # 真实模式双保险: mode=real + --real (缺一即降级退出), 再加交互确认
    if mode == "real" and not args.real:
        print("⛔ mode=real 还需要显式加 --real 才会真实下单 (防止误触), 本次退出")
        return 2

    session = requests.Session()
    rpt, parsed, session, headers, face = preflight(settings, params, mode)

    if args.list_sessions and parsed and parsed.get("ok"):
        print("─" * 62)
        print("页面场次 (北京时间):")
        for s in parsed.get("sessions", []):
            mark = "🎯含目标商品" if s.get("sale_found") else "  "
            st = tc.ms_to_bj_str(s.get("start_ms"))
            print(f"  {mark} {s.get('date')} {s.get('start_time')}~{s.get('end_time')} "
                  f"[{s.get('dynamic_status_cn')}] start={st}")
        return 0

    print("─" * 62)
    print(f"[预检结果] {rpt.summary()}")
    rpt.dump()
    print("─" * 62)
    if rpt.failed:
        print("⛔ 存在失败项, 任何模式都不继续下单流程 (检查项见上)")
        if mode != "check":
            return 1
        return 1

    if mode == "check":
        print("✅ 检查模式结束 (未发出任何下单请求)")
        return 0

    # ---- dry / real 共同: 场次选择与对时 ----
    sess, note = tc.pick_session(parsed, settings)
    if not sess:
        print(f"⛔ 场次选择失败: {note}")
        return 1
    print(f"🕐 场次: {note}")
    seckill_ms = sess["start_ms"]

    if mode == "real":
        if not args.yes:
            try:
                answer = input(f"🔥 即将真实下单 (金额上限 {settings['target']['max_price_yuan']} 元), 输入 YES 确认: ")
            except EOFError:
                answer = ""
            if answer.strip() != "YES":
                print("⛔ 未确认, 退出 (未发出任何下单请求)")
                return 2
        print("✔️ 已确认真实下单模式")

    clock = tc.ServerClock(samples=settings.get("runtime", {}).get("sync_samples", 5))
    ok_sync, sync_note = clock.sync(settings.get("activity_url"))
    print(f"🕒 {sync_note}")
    if parsed.get("page_now_ms"):
        page_skew = parsed["page_now_ms"] - clock.now_ms()
        print(f"🕒 页面 SSR 时间样本与本地差: {page_skew}ms (参考)")

    lead = settings["seckill"]["lead_ms"]
    window = settings["seckill"]["retry_window_s"]
    print(f"🎯 开抢: {tc.ms_to_bj_str(seckill_ms)} (提前 {lead}ms 开打, 重试窗口 {window}s)")

    if mode == "dry":
        # 模拟模式不等待真实场次 (远场/进行中场都会让演练跑不完), 一律 5 秒后按演练时间轴开打
        seckill_ms = clock.now_ms() + 5000
        print("🧪 [模拟] 5 秒后按演练时间轴开打 (不等待真实场次, 不发出任何真实下单请求)")
    else:
        sync_point = seckill_ms - lead - 60_000
        if clock.now_ms() < sync_point:
            print("💤 距离开抢还远, 休眠至校准点...")
            wait_until(sync_point, clock, "校准点")
            ok_sync, sync_note = clock.sync(settings.get("activity_url"))
            print(f"🕒 复校: {sync_note}")
    wait_until(seckill_ms - lead, clock, "开打")
    print(f"🔥 开打! ({'模拟' if mode == 'dry' else '真实'}模式)")

    regions = pick_regions(parsed, settings)
    print(f"🗺️ 开打地域: {regions}")
    act_url = settings.get("activity_url")
    cfg = settings["runtime"]
    deadline_ms = seckill_ms + window * 1000
    round_no = 0
    totals = Counter()
    consec_rate = 0
    backoff = cfg["backoff_408_s"]

    try:
        while True:
            round_no += 1
            if round_no > cfg["max_rounds"]:
                print(f"🛑 达到最大轮数 {cfg['max_rounds']}, 收工 (安全停止)")
                break
            # 可选: 每轮先查库存再打 (默认关闭, 直接打靠返回码反馈)
            fire_regions = regions
            if cfg["check_before_buy"] and mode != "dry":
                ok2, in_stock, _ = check_available(session, headers, params, regions)
                if ok2:
                    fire_regions = [r for r in regions if r in in_stock] or regions
                    if not in_stock:
                        print(f"⏳ 第 {round_no} 轮前查库存: 暂无货")
            outcome, detail, hist, payload = buy_round(
                session, headers, params, fire_regions, round_no, act_url, cfg, mode == "dry"
            )
            totals.update(hist)
            if outcome == "success":
                handle_success(payload, settings["target"]["max_price_yuan"], mode == "dry",
                               settings["log"]["open_browser_on_success"])
                break
            if outcome == "unpaid":
                handle_unpaid(detail, mode == "dry")
                break
            if outcome == "fatal":
                print(f"🛑 {detail}, 收工 (重试无意义)")
                break
            if outcome == "rate_limited":
                consec_rate += 1
                if consec_rate >= cfg["max_consecutive_rate_limit"]:
                    print(f"🛑 连续 {consec_rate} 轮限流 (疑似 IP 级限流), 安全停止; 请稍后冷却再试")
                    break
                backoff = next_backoff(consec_rate, cfg)
                print(f"🐢 触发限流, 指数退避 {backoff:.1f}s 后继续 (连续第 {consec_rate} 次)")
                time.sleep(backoff)
            else:
                consec_rate = 0
                backoff = cfg["backoff_408_s"]
            gap = cfg["round_gap_s"] + random.uniform(0, cfg["round_jitter_s"])
            time.sleep(gap)
            if clock.now_ms() > deadline_ms:
                print(f"🛑 超过重试窗口 ({window}s), 收工")
                break
    except KeyboardInterrupt:
        print("\n🖐 收到 Ctrl+C, 安全退出 (未完成的流程已停止)")
        if totals:
            print(f"📊 中断前统计 (共{round_no}轮): {hist_line(totals)}")
        return 130

    if totals:
        print(f"📊 本场统计 (共{round_no}轮): {hist_line(totals)}")
    print("✅ 脚本结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
