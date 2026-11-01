"""场景组: Windows 路径(中文+空格) / 依赖缺失 / Cookie 缺失或路径隔离."""

import json

import pytest

import tcutil as tc


def test_chinese_and_space_path_roundtrip(tmp_path, monkeypatch):
    """场景1: 路径含中文与空格时, cookies 读写与 JSON 往返必须正常."""
    deep = tmp_path / "项目 目录" / "腾讯 云"
    deep.mkdir(parents=True)
    cookies_path = deep / "cookies.json"
    data = [{"name": "skey", "value": "中文值测试", "domain": ".cloud.tencent.com", "path": "/"}]
    cookies_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(tc, "COOKIES_PATH", cookies_path)
    session, csrf, source = tc.build_session()
    assert source.startswith("skey")
    assert csrf == tc.compute_g_tk("中文值测试")
    assert session.cookies.get("skey") == "中文值测试"


def test_missing_cookies_clear_error(isolate_project_files):
    """场景3: cookies.json 不存在时, 明确报错而非崩栈."""
    assert not tc.COOKIES_PATH.exists()
    with pytest.raises(FileNotFoundError, match="login.bat"):
        tc.build_session()


def test_settings_missing_file_uses_defaults(isolate_project_files):
    """settings.json 缺失也能跑 (全默认值, dry 模式)."""
    s = tc.load_settings()
    assert s["runtime"]["mode"] == "dry"
    assert s["target"]["max_price_yuan"] == 38.0
    assert s["target"]["region_priority"] == [4, 1, 8]


def test_settings_user_override_merge(isolate_project_files):
    """用户改了部分配置, 其余保持默认."""
    tc.save_json(tc.SETTINGS_PATH, {"runtime": {"mode": "real"}, "target": {"max_price_yuan": 30}})
    s = tc.load_settings()
    assert s["runtime"]["mode"] == "real"
    assert s["target"]["max_price_yuan"] == 30
    assert s["target"]["sku_type"] == tc.DEFAULT_SETTINGS["target"]["sku_type"]  # 未改的保留
    assert s["seckill"]["lead_ms"] == tc.DEFAULT_SETTINGS["seckill"]["lead_ms"]


def test_check_env_missing_dependency():
    """场景2: 依赖缺失时逐项报告, 不抛异常."""
    def fake_importer(name):
        if name == "playwright":
            raise ImportError("No module named 'playwright'")
        return object()

    items = tc.check_env(importer=fake_importer)
    by_name = {name: ok for name, ok, _ in items}
    assert by_name["playwright"] is False
    assert by_name["requests"] is True


def test_mask_secret():
    assert tc.mask_secret("abcdefghij") == "abc***hij"
    assert tc.mask_secret("ab") == "***"
    assert tc.mask_secret(None) == "***"


def test_bj_time_independent_of_local_timezone():
    """关键 Windows 修复: 秒杀时间解析不依赖系统时区, 统一北京时间.

    '2026-11-02 10:00:00' 北京时间 -> 1793584800000ms (活动页实测值).
    """
    ms = tc.bj_to_ms(tc.parse_bj("2026-11-02 10:00:00"))
    assert ms == 1793584800000
    assert tc.ms_to_bj_str(ms) == "2026-11-02 10:00:00"
    # 活动窗口边界
    assert tc.ms_to_bj_str(1791820800000) == "2026-10-13 00:00:00"
    assert tc.ms_to_bj_str(1796054399000) == "2026-11-30 23:59:59"
