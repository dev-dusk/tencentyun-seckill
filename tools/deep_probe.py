"""深度探测: 用已保存的登录态(cookies.json)自动核实下单接口参数.

只读操作, 绝不下单:
  1. 打开活动页(登录态), 捕获关键 act-api 接口的请求+响应
  2. 用页面同源 fetch 复放只读的 query-price / check-available (页面自己也发的查询)
  3. 在页面 JS bundle 里定位 do-goods 请求构造代码 (business/type 字段来源)
结果落盘 logs/deep_probe.json
"""

import contextlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tcutil as tc  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
ACT_URL = tc.load_settings()["activity_url"]
TARGET_ACT = tc.load_activity_params().get("BUY_ACT_ID")
ALT_ACT = 2001194084080021  # 抓包中发现的同 SKU 另一商品入口

CAPTURE_PATHS = (
    "query-price", "check-available", "query-seckill-by-date", "query-seckill-subs",
    "query-template-page-goods", "query-act-info", "check-goods", "get-now", "query-goods",
)


def g_tk_js():
    """返回在页面上下文里计算 g_tk 的 JS 表达式源码."""
    return (
        "(() => { const skey = document.cookie.match(/(?:^|;\\s*)skey=([^;]+)/);"
        " if (!skey) return null; let h = 5381;"
        " for (const ch of decodeURIComponent(skey[1])) { h += (h << 5) + ch.codePointAt(0); }"
        " return String(h & 0x7FFFFFFF); })()"
    )


def main():
    cookies = tc.load_json(tc.COOKIES_PATH) or []
    if not cookies:
        print("❌ 无 cookies.json, 请先 login.bat")
        return 1
    out = {"responses": [], "own_queries": {}, "js_findings": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=tc.USER_AGENT)
        context.add_cookies([
            {k: c.get(k) for k in ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")}
            for c in cookies if all(c.get(k) is not None for k in ("name", "value", "domain", "path"))
        ])
        page = context.new_page()

        def on_response(resp):
            try:
                path = resp.url.split("cloud.tencent.com")[-1].split("?")[0]
                if not any(k in path for k in CAPTURE_PATHS):
                    return
                body = None
                with contextlib.suppress(Exception):
                    body = resp.json()
                out["responses"].append({
                    "path": path,
                    "status": resp.status,
                    "request_payload": None,
                    "response": body,
                })
            except Exception:
                pass

        page.on("response", on_response)
        print("打开活动页(登录态)...")
        page.goto(ACT_URL, timeout=60000, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(4000)

        # 请求体补录: on_response 拿不到 post body, 用 request 事件配对
        def on_request(req):
            try:
                path = req.url.split("cloud.tencent.com")[-1].split("?")[0]
                if not any(k in path for k in CAPTURE_PATHS):
                    return
                pd = req.post_data
                payload = json.loads(pd) if pd else None
                for rec in reversed(out["responses"]):
                    if rec["path"] == path and rec["request_payload"] is None:
                        rec["request_payload"] = payload
                        break
                else:
                    out["responses"].append({"path": path, "status": None,
                                             "request_payload": payload, "response": None})
            except Exception:
                pass

        page.on("request", on_request)
        page.wait_for_timeout(3000)

        # ---- 只读复放: query-price 两个 act_id x 三地域 (页面弹窗同款查询) ----
        csrf = page.evaluate(g_tk_js())
        print(f"csrf(g_tk): {tc.mask_secret(csrf) if csrf else None}")
        if csrf:
            for act in (TARGET_ACT, ALT_ACT):
                for region in (4, 1, 8):
                    goods_param = {
                        "zone": "", "timeSpanUnit": "12m", "scenario": "0",
                        "imageId": "lhbp-eqora508", "goodsNum": 1,
                        "ddocUnionConnect": 0, "area": 1,
                        "type": tc.load_activity_params()["SKU"],
                        "regionId": region, "BlueprintId": "LINUX_UNIX",
                    }
                    payload = {
                        "activity_id": tc.load_activity_params()["ACTIVITY_ID"],
                        "goods": [{"act_id": act, "type": "lighthouse_v5",
                                   "goods_param": goods_param, "page_from": "act_buy"}],
                        "preview": 0,
                    }
                    result = page.evaluate(
                        """async ([url, csrf, body]) => {
                            const r = await fetch(url, {method: 'POST',
                                headers: {'content-type': 'application/json', 'x-csrf-token': csrf},
                                body: JSON.stringify(body)});
                            return await r.json();
                        }""",
                        ["https://act-api.cloud.tencent.com/dianshi/query-price", csrf, payload],
                    )
                    key = f"query-price act={act} region={region}"
                    out["own_queries"][key] = result
                    price = None
                    try:
                        d = (result.get("data") or {})
                        if isinstance(d, dict) and d.get("list"):
                            price = d["list"][0].get("real_cost")
                        elif isinstance(d, list) and d:
                            price = d[0].get("real_cost")
                    except Exception:
                        pass
                    print(f"💰 {key}: real_cost={price} code={result.get('code')}")

        # ---- JS bundle 里找 do-goods 构造 ----
        print("搜索 JS bundle 中的 do-goods 构造...")
        srcs = page.evaluate(
            "Array.from(document.querySelectorAll('script[src]')).map(s => s.src)"
            " .filter(u => u.includes('_next/static'))"
        )
        print(f"静态 JS 数量: {len(srcs)}")
        for src in srcs:
            try:
                resp = page.request.get(src, timeout=30000)
                text = resp.text()
            except Exception:
                continue
            for m in re.finditer(r"do-goods", text):
                start = max(0, m.start() - 700)
                snippet = text[start:m.end() + 700]
                out["js_findings"].append({"bundle": src.split("/")[-1], "snippet": snippet})
                print(f"🔍 bundle {src.split('/')[-1]} 含 do-goods")
        browser.close()

    tc.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = tc.LOG_DIR / f"deep_probe_{tc.bj_now().strftime('%Y%m%d_%H%M%S')}.json"
    tc.save_json(path, out)
    print(f"✅ 深度探测完成: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
