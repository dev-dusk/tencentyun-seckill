"""JS 深挖 v4: do-goods 调用方参数 + 签名强制性验证(非场次时间安全窗口).

安全说明: 当前处于非场次时间(晚上), do-goods 的第一时间检查是抢购时间,
任何请求都会被 1100136 拒绝, 不会创建订单. 用 preview:1 双保险.
"""

import contextlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tcutil as tc  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

ACT_URL = tc.load_settings()["activity_url"]
PARAMS = tc.load_activity_params()


def main():
    cookies = tc.load_json(tc.COOKIES_PATH) or []
    skey = next((c["value"] for c in cookies if c.get("name") == "skey"
                 and c.get("domain") == ".cloud.tencent.com"),
                next((c["value"] for c in cookies if c.get("name") == "skey"), ""))
    csrf = tc.compute_g_tk(skey)
    out = {"do_goods_test": [], "caller_hits": []}

    goods_param = {**PARAMS["GOODS_PARAM"], "regionId": 4}
    payload_sku = {
        "activity_id": PARAMS["ACTIVITY_ID"],
        "goods": [{"act_id": PARAMS["BUY_ACT_ID"], "type": PARAMS["SKU"], "goods_param": goods_param}],
        "preview": 1,
        "agent_channel": {"fromChannel": "", "fromSales": "", "isAgentClient": False,
                          "fromUrl": ACT_URL},
    }
    payload_ptype = {
        "activity_id": PARAMS["ACTIVITY_ID"],
        "goods": [{"act_id": PARAMS["BUY_ACT_ID"], "type": "lighthouse_v5", "goods_param": goods_param}],
        "preview": 1,
        "agent_channel": {"fromChannel": "", "fromSales": "", "isAgentClient": False,
                          "fromUrl": ACT_URL},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=tc.USER_AGENT)
        context.add_cookies([{k: c[k] for k in ("name", "value", "domain", "path")} for c in cookies])
        page = context.new_page()
        page.goto(ACT_URL, timeout=60000, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
        page.wait_for_timeout(3000)

        for tag, payload in (("type=SKU", payload_sku), ("type=productType", payload_ptype)):
            r = page.evaluate(
                """async ([url, csrf, body]) => {
                    const resp = await fetch('https://act-api.cloud.tencent.com' + url, {
                        method: 'POST', credentials: 'include',
                        headers: {'content-type': 'application/json', 'x-csrf-token': csrf},
                        body: JSON.stringify(body)});
                    let text = await resp.text();
                    try { return {status: resp.status, headers: Object.fromEntries(resp.headers.entries()), body: JSON.parse(text)}; }
                    catch (e) { return {status: resp.status, bodyText: text.slice(0, 500)}; }
                }""",
                ["/dianshi/do-goods", csrf, payload],
            )
            out["do_goods_test"].append({"tag": tag, "result": r})
            print(f"🧪 do-goods[{tag}] preview=1: status={r.get('status')} "
                  f"code={((r.get('body') or {}).get('code'))} msg={str(((r.get('body') or {}).get('msg')))[:100]}")
            hdrs = r.get("headers") or {}
            signish = {k: v[:40] for k, v in hdrs.items() if "sign" in k.lower() or "csrf" in k.lower()}
            if signish:
                print("   相关响应头:", signish)

        # do-goods 调用方: 在 visual-v3 / qcact-base 里搜 isAutoPay/agentChannel 传参点
        urls = page.evaluate(
            "Array.from(new Set(["
            " ...Array.from(document.querySelectorAll('script[src]')).map(s=>s.src),"
            " ...performance.getEntriesByType('resource').map(e=>e.name)"
            "])).filter(u => /\\.js($|\\?)/.test(u))"
        )
        for src in urls:
            name = src.split("/")[-1]
            if not any(k in name for k in ("qcact", "visual")):
                continue
            try:
                text = page.request.get(src, timeout=30000).text()
            except Exception:
                continue
            for kw in ("isAutoPay", "agentChannel:", "user_token"):
                for m in re.finditer(re.escape(kw), text):
                    start = max(0, m.start() - 1000)
                    snippet = re.sub(r"\s+", " ", text[start:m.end() + 1000])
                    out["caller_hits"].append({"bundle": name[:50], "kw": kw, "snippet": snippet})
                    break
        browser.close()

    for h in out["caller_hits"]:
        print("=" * 12, h["bundle"][:40], h["kw"], "=" * 12)
        print(h["snippet"][:1400])
        print()
    tc.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = tc.LOG_DIR / f"js_dig4_{tc.bj_now().strftime('%Y%m%d_%H%M%S')}.json"
    tc.save_json(path, out)
    print(f"✅ -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
