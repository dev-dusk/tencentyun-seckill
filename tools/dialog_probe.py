"""弹窗与登录态综合探测.

A. python requests(完整cookie) 直连: get-facecheck-info / check-available / query-price
B. 完整cookie属性恢复 do-goods 重测(preview:1, 非场次时间安全)
C. 点击秒杀卡片 -> 用弹窗触发的 query-price act_id 验证目标卡 -> 转储弹窗 DOM
"""

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


def part_a_requests():
    print("=" * 20, "A. python requests 直连测试", "=" * 20)
    try:
        session, csrf, src = tc.build_session()
    except FileNotFoundError as e:
        print("⚠️", e)
        return
    headers = tc.make_headers(csrf, ACT_URL)
    r = session.post("https://act-api.cloud.tencent.com/user/get-facecheck-info",
                     json={}, headers=headers, timeout=10)
    print(f"人脸核身: {json.dumps(r.json(), ensure_ascii=False)[:200]}")
    r = session.post("https://act-api.cloud.tencent.com/dianshi/check-available",
                     json={"activity_id": PARAMS["ACTIVITY_ID"],
                           "goods": [{"act_id": PARAMS["CHECK_ACT_ID"], "region_id": [4, 8, 1]}],
                           "preview": 0},
                     headers=headers, timeout=10)
    print(f"check-available: {json.dumps(r.json(), ensure_ascii=False)[:300]}")
    qp = {"activity_id": PARAMS["ACTIVITY_ID"],
          "goods": [{"act_id": PARAMS["BUY_ACT_ID"], "type": "lighthouse_v5",
                     "goods_param": {**PARAMS["GOODS_PARAM"], "regionId": 4},
                     "page_from": "act_buy"}],
          "preview": 0}
    r = session.post("https://act-api.cloud.tencent.com/dianshi/query-price",
                     json=qp, headers=headers, timeout=10)
    print(f"query-price: {json.dumps(r.json(), ensure_ascii=False)[:300]}")


def main():
    part_a_requests()
    cookies = tc.load_json(tc.COOKIES_PATH) or []
    skey = next((c["value"] for c in cookies if c.get("name") == "skey"
                 and c.get("domain") == ".cloud.tencent.com"),
                next((c["value"] for c in cookies if c.get("name") == "skey"), ""))
    csrf = tc.compute_g_tk(skey)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=tc.USER_AGENT)
        # 完整属性恢复 cookie (sameSite/httpOnly/secure/expires 都保留)
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

        print("=" * 20, "B. do-goods 完整cookie重测 (preview:1)", "=" * 20)
        page.goto(ACT_URL, timeout=60000, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
        page.wait_for_timeout(2500)
        payload = {
            "activity_id": PARAMS["ACTIVITY_ID"],
            "goods": [{"act_id": PARAMS["BUY_ACT_ID"], "type": PARAMS["SKU"],
                       "goods_param": {**PARAMS["GOODS_PARAM"], "regionId": 4}}],
            "preview": 1,
            "agent_channel": {"fromChannel": "", "fromSales": "", "isAgentClient": False,
                              "fromUrl": ACT_URL},
        }
        r = page.evaluate(
            """async ([csrf, body]) => {
                const resp = await fetch('https://act-api.cloud.tencent.com/dianshi/do-goods', {
                    method: 'POST', credentials: 'include',
                    headers: {'content-type': 'application/json', 'x-csrf-token': csrf},
                    body: JSON.stringify(body)});
                return await resp.json();
            }""",
            [csrf, payload],
        )
        print(f"do-goods(preview:1, type=SKU): {json.dumps(r, ensure_ascii=False)[:300]}")

        print("=" * 20, "C. 秒杀卡片定位与弹窗 DOM", "=" * 20)
        qprice_acts = []
        page.on("request", lambda req: qprice_acts.append(req.url) if "query-price" in req.url else None)

        def dialogs_acts():
            return list(qprice_acts)

        cards = page.get_by_text("4核4G", exact=False)
        n = cards.count()
        print(f"含[4核4G]元素数: {n}")
        found = False
        for i in range(min(n, 6)):
            qprice_acts.clear()
            try:
                cards.nth(i).click(timeout=2500)
            except Exception:
                continue
            page.wait_for_timeout(2500)
            # 弹窗是否触发目标商品的 query-price?
            hit_target = any(True for _ in qprice_acts)  # 有 query-price 即视为弹窗打开
            print(f"卡片#{i}: 点击后 query-price 数={len(qprice_acts)}")
            if hit_target:
                # 读弹窗 DOM 文本
                try:
                    snap = page.evaluate(
                        """() => {
                          const dlg = document.querySelector('.uno3-modal, [class*="dialog"], [class*="modal"], [role="dialog"]');
                          if (!dlg) return null;
                          const btns = Array.from(dlg.querySelectorAll('button, [class*="btn"]')).map(b => (b.textContent||'').trim()).filter(Boolean);
                          const txt = (dlg.textContent||'').replace(/\\s+/g,' ').slice(0, 600);
                          return {btns, txt};
                        }"""
                    )
                    if snap:
                        print(f"  弹窗按钮: {snap['btns'][:12]}")
                        print(f"  弹窗文本: {snap['txt'][:300]}")
                        found = True
                except Exception as e:
                    print("  弹窗DOM读取失败:", e)
            with contextlib.suppress(Exception):
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
            if found and i >= 2:
                break
        if not found:
            print("⚠️ 未打开任何弹窗 (卡片点击可能需要先滚动可见)")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
