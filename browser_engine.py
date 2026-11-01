"""浏览器兜底引擎: Playwright 驱动官方活动页, 在开抢时刻自动点击抢购按钮.

定位: API 引擎(snap_up_server.py 纯 HTTP)为主引擎; 当 API 请求被服务端
以签名/风控理由拒绝时, 由本引擎接管 —— 让真实页面自己发请求(页面自带
签名/设备指纹), 我们只负责点得快、判得准、抢到即停.

用法 (独立运行, 一般不需要手动调用):
  .venv/Scripts/python.exe browser_engine.py --at "2026-11-02 10:00:00"
  .venv/Scripts/python.exe browser_engine.py --at "2026-11-02 10:00:00" --real --yes
"""

import argparse
import contextlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tcutil as tc  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

ACT_URL_KEY = "activity_url"
BUY_TEXT_RE = re.compile(r"(立即抢购|立即购买|抢购|马上抢|立即秒杀)")


def restore_context(browser):
    cookies = tc.load_json(tc.COOKIES_PATH) or []
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
    return context


def find_buy_button(page):
    """找目标商品的抢购类按钮 (文本匹配 + 卡片归属校验).

    页面上有多个秒杀商品都有[抢购]按钮, 必须确认按钮所在卡片
    同时含"4核4G"与"38"(价格) —— 排除 109 元 openclaw 直购等同配置商品.
    """
    candidates = page.get_by_text(BUY_TEXT_RE).all()
    for loc in candidates:
        try:
            if not (loc.is_visible() and loc.is_enabled()):
                continue
            card_text = loc.evaluate(
                """el => {
                    let n = el;
                    for (let i = 0; i < 10 && n; i++) {
                        const t = (n.textContent || '');
                        if (t.includes('4核4G') && t.includes('38')) return t;
                        n = n.parentElement;
                    }
                    return '';
                }"""
            )
            if card_text:
                return loc
        except Exception:
            continue
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", required=True, help="开抢时间 'YYYY-MM-DD HH:MM:SS' (北京时间)")
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--max-wait-s", type=int, default=180, help="开抢后最多点击重试时长")
    args = parser.parse_args()

    log_path = tc.setup_logging("browser_engine")
    print("🚀 浏览器兜底引擎启动 (驱动官方页面, 页面自行签名)")
    print(f"📝 日志: {log_path}")
    settings = tc.load_settings()
    target = settings["target"]

    if not args.real:
        print("ℹ️ 未加 --real: 演练模式 —— 只定位按钮与倒计时, 不点击")
    if args.real and not args.yes:
        try:
            answer = input(f"🔥 浏览器引擎真实点击确认 (红线 {target['max_price_yuan']} 元), 输入 YES: ")
        except EOFError:
            answer = ""
        if answer.strip() != "YES":
            print("⛔ 未确认, 退出")
            return 2

    fire_ms = tc.bj_to_ms(tc.parse_bj(args.at))
    clock = tc.ServerClock(samples=5)
    ok, note = clock.sync(settings[ACT_URL_KEY])
    print(f"🕒 {note}; 开抢: {tc.ms_to_bj_str(fire_ms)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = restore_context(browser)
        page = context.new_page()
        do_goods_results = []

        def on_response(resp):
            if "dianshi/do-goods" in resp.url:
                with contextlib.suppress(Exception):
                    do_goods_results.append(resp.json())

        page.on("response", on_response)
        page.goto(settings[ACT_URL_KEY], timeout=60000, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
        page.wait_for_timeout(3000)

        logged = page.evaluate(
            "() => { try { return JSON.parse(document.getElementById('__NEXT_DATA__')"
            ".textContent).props.pageProps.activityData.isLogined; } catch(e){ return false; } }"
        )
        print(f"👤 页面登录态: {logged}")
        if not logged:
            print("⛔ 页面未登录 (cookies 失效?), 请先运行 login.bat")
            browser.close()
            return 1

        # 等到开抢前 5 秒, 滚到秒杀区让按钮就绪
        remain = fire_ms - clock.now_ms()
        if remain > 5000:
            print(f"💤 等待开抢 {(remain - 5000) / 1000:.0f}s ...")
            time.sleep(max(0, (remain - 5000) / 1000))
        with contextlib.suppress(Exception):
            page.get_by_text("4核4G", exact=False).first.scroll_into_view_if_needed(timeout=3000)
        # 秒杀时间未到前页面可能只显示[添加提醒], 到点自动切换为抢购按钮
        deadline = time.time() + args.max_wait_s
        clicked = 0
        while time.time() < deadline:
            if do_goods_results:
                last = do_goods_results[-1]
                code = last.get("code")
                print(f"📡 do-goods 返回: code={code} msg={str(last.get('msg'))[:80]}")
                if code == 0:
                    print("🎉 页面下单成功!")
                    print(json.dumps(last, ensure_ascii=False)[:600])
                    info_deal = json.dumps(last, ensure_ascii=False)
                    m = re.search(r"(?:bigDealId|big_deal_id|dealId)[\"':=]+(\d+)", info_deal)
                    if m:
                        url = f"https://console.cloud.tencent.com/expense/deal/info?bigDealId={m.group(1)}"
                        print(f"🧾 订单页: {url}")
                        import webbrowser

                        with contextlib.suppress(Exception):
                            webbrowser.open(url)
                    break
                if code in (1101411,) or last.get("msg") and "未支付" in str(last.get("msg")):
                    print("🛑 有未支付订单, 停止点击; 请到订单中心处理")
                    break
                if code in (1100202,) or last.get("msg") and "人脸" in str(last.get("msg")):
                    print("🛑 需要人脸核身, 停止点击")
                    break
            if clock.now_ms() >= fire_ms - 1500 and args.real:
                btn = find_buy_button(page)
                if btn:
                    with contextlib.suppress(Exception):
                        btn.click(timeout=2000)
                        clicked += 1
                        print(f"🖱️ 第 {clicked} 次点击抢购按钮")
                        time.sleep(0.8)
                else:
                    time.sleep(0.2)
            else:
                time.sleep(0.2)
        print(f"📊 引擎结束: 点击 {clicked} 次, do-goods 响应 {len(do_goods_results)} 个")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
