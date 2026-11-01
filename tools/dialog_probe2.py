"""弹窗 DOM 精准探测 v2: 确认秒杀卡 act_id + 弹窗按钮/地域结构 + check-goods 资格响应."""

import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tcutil as tc  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

ACT_URL = tc.load_settings()["activity_url"]
PARAMS = tc.load_activity_params()
TARGET_ACT = str(PARAMS["BUY_ACT_ID"])


def main():
    cookies = tc.load_json(tc.COOKIES_PATH) or []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=tc.USER_AGENT)
        clean = []
        for c in cookies:
            item = {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]}
            for k in ("expires", "httpOnly", "secure"):
                if c.get(k) is not None:
                    item[k] = c[k]
            if c.get("sameSite") in ("Strict", "Lax", "None"):
                item["sameSite"] = c["sameSite"]
            clean.append(item)
        context.add_cookies(clean)
        page = context.new_page()

        api_reqs = []

        def on_request(req):
            if "act-api" in req.url:
                try:
                    pd = json.loads(req.post_data) if req.post_data else None
                except Exception:
                    pd = None
                api_reqs.append({"path": req.url.split("cloud.tencent.com")[-1].split("?")[0],
                                 "payload": pd})

        api_resps = []

        def on_response(resp):
            if any(k in resp.url for k in ("check-goods", "check-available", "query-price")):
                try:
                    api_resps.append({"path": resp.url.split("cloud.tencent.com")[-1].split("?")[0],
                                      "body": resp.json()})
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)
        page.goto(ACT_URL, timeout=60000, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
        page.wait_for_timeout(3000)

        cards = page.get_by_text("4核4G", exact=False)
        n = cards.count()
        print(f"含[4核4G]元素: {n}")
        dialog_info = None
        for i in range(n):
            api_reqs.clear()
            try:
                cards.nth(i).scroll_into_view_if_needed(timeout=2000)
                cards.nth(i).click(timeout=2500)
            except Exception:
                continue
            page.wait_for_timeout(2800)
            qprice = [r for r in api_reqs if "query-price" in r["path"]]
            if not qprice:
                with contextlib.suppress(Exception):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                continue
            acts = set()
            for r in qprice:
                for g in (r["payload"] or {}).get("goods", []):
                    acts.add(str(g.get("act_id")))
            print(f"卡片#{i} 弹窗 query-price act_ids: {acts}")
            if TARGET_ACT not in acts:
                print("  -> 非目标商品弹窗, 关闭")
                with contextlib.suppress(Exception):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(600)
                continue

            # 目标弹窗: 全量可见按钮 + 地域结构 + 价格文本
            dom = page.evaluate(
                """() => {
                  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
                  const btns = Array.from(document.querySelectorAll('button, [role="button"], a')).filter(vis)
                    .map(b => ({tag: b.tagName, cls: (b.className||'').toString().slice(0,50),
                                txt: (b.textContent||'').replace(/\\s+/g,' ').trim().slice(0,30)}))
                    .filter(b => b.txt);
                  const regionLike = Array.from(document.querySelectorAll('*')).filter(el =>
                    vis(el) && el.children.length === 0 && /^(上海|北京|广州|中国香港)$/.test((el.textContent||'').trim()))
                    .map(el => ({tag: el.tagName, cls: (el.className||'').toString().slice(0,60),
                                 txt: el.textContent.trim()}));
                  const priceTexts = Array.from(document.querySelectorAll('*')).filter(el =>
                    vis(el) && el.children.length === 0 && /[¥￥]\\s*\\d/.test(el.textContent||''))
                    .map(el => (el.textContent||'').replace(/\\s+/g,' ').trim().slice(0,40)).slice(0, 15);
                  return {btns: btns.slice(-40), regionLike, priceTexts};
                }"""
            )
            dialog_info = {"card_index": i, "acts": sorted(acts), "dom": dom}
            print("  可见按钮(后40):")
            for b in dom["btns"]:
                print(f"    <{b['tag']}> [{b['txt']}] {b['cls'][:40]}")
            print("  地域元素:", dom["regionLike"])
            print("  价格文本:", dom["priceTexts"])
            break

        if not dialog_info:
            print("❌ 未找到目标秒杀弹窗")

        print()
        print("===== 关键响应 (check-goods / check-available) =====")
        seen = set()
        for r in api_resps:
            key = r["path"]
            if key in seen or r["path"] == "query-price":
                continue
            seen.add(key)
            print(f"-- {key}: {json.dumps(r['body'], ensure_ascii=False)[:400]}")

        browser.close()
    tc.LOG_DIR.mkdir(parents=True, exist_ok=True)
    tc.save_json(tc.LOG_DIR / f"dialog2_{tc.bj_now().strftime('%Y%m%d_%H%M%S')}.json",
                 {"dialog": dialog_info, "responses": api_resps})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
