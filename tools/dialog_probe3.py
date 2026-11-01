"""弹窗探测 v3: 登录态验证 + 日期页签 + 遍历全部卡片找目标弹窗."""

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


def click_cards(page, api_reqs, label):
    """遍历 4核4G 卡片, 返回目标弹窗是否打开."""
    cards = page.get_by_text("4核4G", exact=False)
    n = cards.count()
    print(f"[{label}] 卡片数: {n}")
    for i in range(n):
        api_reqs.clear()
        try:
            cards.nth(i).scroll_into_view_if_needed(timeout=2000)
            cards.nth(i).click(timeout=2500)
        except Exception:
            continue
        page.wait_for_timeout(2600)
        acts = set()
        for r in api_reqs:
            if "query-price" in r["path"]:
                for g in (r["payload"] or {}).get("goods", []):
                    acts.add(str(g.get("act_id")))
        if acts:
            print(f"  卡片#{i} 弹窗 acts: {sorted(acts)[:6]}")
        if TARGET_ACT in acts:
            print(f"  🎯 卡片#{i} = 目标秒杀弹窗!")
            return i
        with contextlib.suppress(Exception):
            page.keyboard.press("Escape")
            page.wait_for_timeout(600)
    return None


def dump_dialog(page):
    return page.evaluate(
        """() => {
          const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
          const btns = Array.from(document.querySelectorAll('button, [role="button"]')).filter(vis)
            .map(b => ({txt: (b.textContent||'').replace(/\\s+/g,' ').trim().slice(0,30),
                        cls: (b.className||'').toString().slice(0,60)})).filter(b => b.txt);
          const tabs = Array.from(document.querySelectorAll('*')).filter(el =>
            vis(el) && el.children.length===0 && /(^|\\s)(上海|北京|广州)(\\s|$)/.test(el.textContent||''))
            .map(el => ({txt: el.textContent.trim(), cls: (el.className||'').toString().slice(0,60)}));
          const prices = Array.from(document.querySelectorAll('*')).filter(el =>
            vis(el) && el.children.length===0 && /(38|109)/.test(el.textContent||'') && /元|¥|￥/.test(el.textContent||''))
            .map(el => (el.textContent||'').replace(/\\s+/g,' ').trim().slice(0,40)).slice(0,10);
          const modal = document.querySelector('[class*="modal"][class*="wrap"], .uno3-modal, [class*="dialog"]');
          return {btns: btns.slice(-45), tabs, prices,
                  modalCls: modal ? modal.className.toString().slice(0,80) : null};
        }"""
    )


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
            if "query-price" in req.url or "check-available" in req.url:
                try:
                    pd = json.loads(req.post_data) if req.post_data else None
                except Exception:
                    pd = None
                api_reqs.append({"path": req.url.split("cloud.tencent.com")[-1].split("?")[0],
                                 "payload": pd})

        getinfo_bodies = []
        page.on("request", on_request)
        page.on("response", lambda r: getinfo_bodies.append(r.json())
                if "user/get-info" in r.url else None)
        page.goto(ACT_URL, timeout=60000, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
        page.wait_for_timeout(3500)

        login_state = page.evaluate(
            """() => { const nd = document.getElementById('__NEXT_DATA__');
                 try { return JSON.parse(nd.textContent).props.pageProps.activityData.isLogined; } catch(e){ return 'parse-fail'; } }"""
        )
        print(f"页面 SSR isLogined: {login_state}")
        page.wait_for_timeout(1500)
        if getinfo_bodies:
            b = getinfo_bodies[-1]
            u = ((b.get("data") or {}) if isinstance(b, dict) else {})
            print(f"user/get-info code={b.get('code') if isinstance(b, dict) else '?'} "
                  f"昵称={u.get('nick_name') if isinstance(u, dict) else u}")

        # 第一轮: 直接点卡片
        idx = click_cards(page, api_reqs, "默认日期页签")
        if idx is None:
            # 第二轮: 找日期页签 (11-02 / 明天) 切换后再点
            for tab_text in ("11-02", "11月2日", "明天", "明日", "02"):
                tab = page.get_by_text(tab_text, exact=False)
                with contextlib.suppress(Exception):
                    tab.first.click(timeout=2000)
                    page.wait_for_timeout(2000)
                    print(f"已点击页签[{tab_text}]")
                    break
            idx = click_cards(page, api_reqs, "切换页签后")

        if idx is not None:
            dom = dump_dialog(page)
            print("弹窗按钮(后45):")
            for b in dom["btns"]:
                print(f"  [{b['txt']}] {b['cls'][:50]}")
            print("地域页签:", dom["tabs"])
            print("价格文本:", dom["prices"])
            print("modal 容器类:", dom["modalCls"])
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
