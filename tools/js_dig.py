"""JS 深挖 v3: 定位 withSign 签名算法 / prepare-do 调用链 / do-goods 调用点."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tcutil as tc  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

ACT_URL = tc.load_settings()["activity_url"]
KEYWORDS = ("withSign", "prepare-do", "x-csrf-token", "x-sign", "sign(", "getSign", "nowTime")


def main():
    cookies = tc.load_json(tc.COOKIES_PATH) or []
    hits = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=tc.USER_AGENT)
        context.add_cookies([{k: c[k] for k in ("name", "value", "domain", "path")} for c in cookies])
        page = context.new_page()
        page.goto(ACT_URL, timeout=60000, wait_until="domcontentloaded")
        with contextlib_suppress():
            page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
        page.wait_for_timeout(3000)
        urls = page.evaluate(
            "Array.from(new Set(["
            " ...Array.from(document.querySelectorAll('script[src]')).map(s=>s.src),"
            " ...performance.getEntriesByType('resource').map(e=>e.name)"
            "])).filter(u => /\\.js($|\\?)/.test(u))"
        )
        print(f"JS 资源: {len(urls)}")
        for src in urls:
            name = src.split("/")[-1]
            if not any(k in name for k in ("qcact", "visual", "common", "vendor", "main", "app", "framework", "chunks")):
                continue
            try:
                text = page.request.get(src, timeout=30000).text()
            except Exception:
                continue
            for kw in KEYWORDS:
                for m in re.finditer(re.escape(kw), text):
                    start = max(0, m.start() - 900)
                    snippet = re.sub(r"\s+", " ", text[start:m.end() + 1300])
                    hits.append({"bundle": name[:60], "kw": kw, "pos": m.start(), "snippet": snippet})
                    break
        browser.close()

    out = tc.LOG_DIR / f"js_dig_{tc.bj_now().strftime('%Y%m%d_%H%M%S')}.json"
    tc.save_json(out, hits)
    print(f"命中 {len(hits)} 段 -> {out}")
    for h in hits:
        print("=" * 15, h["bundle"][:40], "|", h["kw"], "=" * 15)
        print(h["snippet"][:1600])
        print()
    return 0


def contextlib_suppress():
    import contextlib

    return contextlib.suppress(Exception)


if __name__ == "__main__":
    raise SystemExit(main())
