"""深度探测 v2: 全自动核实下单参数 (无需任何人工点击购买).

只读操作:
  1. query-price 实时核价: 秒杀品/直购品 x 三地域
  2. query-seckill-by-date 场次结构 / get-facecheck-info 人脸状态
  3. 扫描全部已加载 JS 资源, 定位 do-goods 请求构造代码 (business/type 字段)
  4. 点击商品卡片打开购买弹窗 (非场次时间弹窗内只有[添加提醒], 无购买按钮,
     仅触发只读的 query-price/check-goods), 触发懒加载 chunk 后再扫一遍 JS
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
SECKILL_ACT = PARAMS.get("BUY_ACT_ID")
DIRECT_ACT = 2001194084080021  # openclaw页面4核4G3M限新 (同 SKU 直购入口)
REGIONS = (4, 1, 8)


def build_query_price(act, region):
    return {
        "activity_id": PARAMS["ACTIVITY_ID"],
        "goods": [{
            "act_id": act, "type": "lighthouse_v5",
            "goods_param": {**PARAMS["GOODS_PARAM"], "regionId": region},
            "page_from": "act_buy",
        }],
        "preview": 0,
    }


def main():
    cookies = tc.load_json(tc.COOKIES_PATH) or []
    skey = next((c["value"] for c in cookies if c.get("name") == "skey"
                 and c.get("domain") == ".cloud.tencent.com"), None) \
        or next((c["value"] for c in cookies if c.get("name") == "skey"), "")
    csrf = tc.compute_g_tk(skey)
    out = {"prices": {}, "seckill_by_date": None, "facecheck": None, "js_hits": [], "dialog_acts": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=tc.USER_AGENT)
        context.add_cookies([
            {k: c[k] for k in ("name", "value", "domain", "path")}
            for c in cookies
        ])
        page = context.new_page()
        api_seen = []
        page.on("request", lambda req: api_seen.append(req.url) if "act-api" in req.url else None)

        print("打开活动页(登录态)...")
        page.goto(ACT_URL, timeout=60000, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
        page.wait_for_timeout(4000)

        def fetch_api(path, payload):
            return page.evaluate(
                """async ([url, csrf, body]) => {
                    const r = await fetch('https://act-api.cloud.tencent.com' + url, {
                        method: 'POST',
                        headers: {'content-type': 'application/json', 'x-csrf-token': csrf},
                        body: JSON.stringify(body)});
                    return await r.json();
                }""",
                [path, csrf, payload],
            )

        # 1) 实时核价
        for act, tag in ((SECKILL_ACT, "秒杀品"), (DIRECT_ACT, "直购品")):
            for region in REGIONS:
                with contextlib.suppress(Exception):
                    r = fetch_api("/dianshi/query-price", build_query_price(act, region))
                    out["prices"][f"{tag} act={act} region={region}"] = r
                    try:
                        data = r.get("data")
                        d0 = data[0] if isinstance(data, list) and data else (data or {})
                        print(f"💰 {tag} region={region}: real_cost={d0.get('real_cost')} "
                              f"total={d0.get('total_cost')} code={r.get('code')}")
                    except Exception:
                        print(f"💰 {tag} region={region}: {json.dumps(r, ensure_ascii=False)[:120]}")

        # 2) 场次结构 + 人脸状态
        with contextlib.suppress(Exception):
            out["seckill_by_date"] = fetch_api(
                "/dianshi/query-seckill-by-date",
                {"activity_id": PARAMS["ACTIVITY_ID"], "preview": 0,
                 "seckill_type": "block", "goods_type": "goods", "days": 2})
            print(f"📅 query-seckill-by-date: code={out['seckill_by_date'].get('code')}")
        with contextlib.suppress(Exception):
            out["facecheck"] = fetch_api("/user/get-facecheck-info", {})
            fc = ((out["facecheck"].get("data") or {}).get("ever_pass"))
            print(f"🧑 人脸核身 ever_pass={fc} code={out['facecheck'].get('code')}")

        # 3) 扫描 JS 资源找 do-goods
        def scan_js(label):
            urls = page.evaluate(
                "Array.from(new Set(["
                " ...Array.from(document.querySelectorAll('script[src]')).map(s=>s.src),"
                " ...performance.getEntriesByType('resource').map(e=>e.name)"
                "])).filter(u => /\\.js($|\\?)/.test(u))"
            )
            print(f"[{label}] JS 资源数: {len(urls)}")
            for src in urls:
                try:
                    text = page.request.get(src, timeout=30000).text()
                except Exception:
                    continue
                for kw in ("do-goods", "agent_channel"):
                    for m in re.finditer(re.escape(kw), text):
                        start = max(0, m.start() - 800)
                        out["js_hits"].append({
                            "kw": kw, "bundle": src.split("/")[-1][:60],
                            "snippet": text[start:m.end() + 1200],
                        })
                        print(f"🔍 [{label}] {src.split('/')[-1][:50]} 命中 {kw}")
                        break  # 每个 bundle 每个 keyword 记一段

        scan_js("首轮")

        # 4) 点开商品卡片触发懒加载 (弹窗只有[添加提醒], 不会下单)
        for label in ("4核4G", "4核4G3M"):
            loc = page.get_by_text(label, exact=False)
            n = min(loc.count(), 3)
            for i in range(n):
                before = set(api_seen)
                with contextlib.suppress(Exception):
                    loc.nth(i).click(timeout=3000)
                    page.wait_for_timeout(2500)
                    new_api = [u for u in api_seen if u not in before and "query-price" in u]
                    for u in new_api:
                        out["dialog_acts"].append({"card_text": label, "index": i, "query_price_url": u})
                        print(f"🃏 卡片[{label}#{i}] 打开弹窗触发 query-price")
                with contextlib.suppress(Exception):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(800)

        scan_js("弹窗后")

        browser.close()

    tc.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = tc.LOG_DIR / f"deep_probe2_{tc.bj_now().strftime('%Y%m%d_%H%M%S')}.json"
    tc.save_json(path, out)
    print(f"✅ 探测完成: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
