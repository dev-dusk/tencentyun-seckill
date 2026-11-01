"""环境验证脚本 (install.bat 最后一步调用): 依赖导入 / Chromium 启动 / JSON 读写 / 北京时间.

退出码 0 = 全部通过; 非 0 = 存在失败项 (日志同时由外层 Tee 落盘 logs/install.log).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tcutil

RESULTS = []


def check(name, fn):
    try:
        detail = fn() or ""
        RESULTS.append((name, True, detail))
    except Exception as e:
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))


def _dep(mod):
    def _run(m=mod):
        __import__(m)
        return "可导入"
    return _run


check("Python 版本", lambda: sys.version.replace("\n", " "))
for mod in ("requests", "playwright", "zoneinfo", "pytest"):
    check(f"依赖 {mod}", _dep(mod))


def _tzdata():
    import tzdata  # noqa: F401

    return "tzdata 包可用"


check("依赖 tzdata (Windows zoneinfo 需要)", _tzdata)


def _chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("about:blank")
        title = page.title()
        browser.close()
        return f"Chromium 启动/开关正常 (title={title!r})"


check("Playwright Chromium 实测启动", _chromium)


def _json_rw():
    import tempfile

    data = {"中文键": "值 测试", "list": [1, 2, 3], "路径": "E:\\项目 目录"}
    path = Path(tempfile.gettempdir()) / "tc_env_test 测试.json"
    tcutil.save_json(path, data)
    loaded = tcutil.load_json(path)
    path.unlink()
    assert loaded == data, "JSON 往返数据不一致"
    return "UTF-8 JSON 读写正常 (含中文与空格路径)"


check("JSON 配置读写 (UTF-8/中文/空格路径)", _json_rw)


def _bj_time():
    ms = tcutil.bj_to_ms(tcutil.parse_bj("2026-11-02 10:00:00"))
    assert ms == 1793584800000, f"北京时间换算错误: {ms}"
    assert tcutil.ms_to_bj_str(ms) == "2026-11-02 10:00:00"
    return "北京时间解析与系统时区无关 (Asia/Shanghai)"


check("北京时间换算", _bj_time)

print("=" * 60)
failed = 0
for name, ok, detail in RESULTS:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}: {detail}")
    failed += 0 if ok else 1
print("=" * 60)
print(f"环境验证: {len(RESULTS) - failed} 通过 / {failed} 失败")
sys.exit(1 if failed else 0)
