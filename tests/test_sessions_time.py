"""场景组: 场次选择 / 活动未开始 / 服务器对时."""

import copy
from datetime import datetime, timezone

import tcutil as tc
from conftest import make_next_data_html

PAGE_NOW_MS = 1793516287093  # 样本页面渲染时刻 (2026-11-01 14:58 北京时间)


def test_parse_all_sessions(sample_next_data, target):
    parsed = tc.parse_activity(make_next_data_html(sample_next_data), target)
    assert len(parsed["sessions"]) == 4  # 两天 x 两场
    assert all(s["sale_found"] for s in parsed["sessions"])
    starts = sorted(s["start_ms"] for s in parsed["sessions"])
    assert starts == [1793498400000, 1793516400000, 1793584800000, 1793602800000]
    assert parsed["page_now_ms"] == PAGE_NOW_MS


def test_pick_next_session_auto(sample_next_data, target, settings):
    """自动识别: 页面时刻的下一场 = 2026-11-01 15:00 场."""
    parsed = tc.parse_activity(make_next_data_html(sample_next_data), target)
    settings["seckill"]["manual_datetime"] = ""
    sess, note = tc.pick_session(parsed, settings, now_ms=PAGE_NOW_MS)
    assert sess["start_ms"] == 1793516400000
    assert "2026-11-01" in note and "15:00:00" in note


def test_pick_session_skips_ended(sample_next_data, target, settings):
    """已结束的 10:00 场不会被选中 (活动未开始/进行中的判定)."""
    parsed = tc.parse_activity(make_next_data_html(sample_next_data), target)
    # 12:01 (1793505600000 = 12:00 结束 + 60s): 上午场已结束 -> 选 15:00 场
    sess, _ = tc.pick_session(parsed, settings, now_ms=1793505600000 + 60_000)
    assert sess["start_ms"] == 1793516400000  # 跳过已结束的上午场


def test_pick_session_in_progress_returns_it(sample_next_data, target, settings):
    """场次进行中 (10:26) -> 该场仍可选 (end 未过)."""
    parsed = tc.parse_activity(make_next_data_html(sample_next_data), target)
    sess, _ = tc.pick_session(parsed, settings, now_ms=1793500000000)  # 11-01 10:26
    assert sess["start_ms"] == 1793498400000  # 上午场进行中


def test_manual_datetime_override(sample_next_data, target, settings):
    parsed = tc.parse_activity(make_next_data_html(sample_next_data), target)
    settings["seckill"]["manual_datetime"] = "2026-11-03 10:00:00"
    sess, note = tc.pick_session(parsed, settings)
    assert sess["start_ms"] == tc.bj_to_ms(tc.parse_bj("2026-11-03 10:00:00"))
    assert "手动指定" in note


def test_manual_datetime_malformed(sample_next_data, target, settings):
    parsed = tc.parse_activity(make_next_data_html(sample_next_data), target)
    settings["seckill"]["manual_datetime"] = "11月2日下午3点"
    sess, note = tc.pick_session(parsed, settings)
    assert sess is None and "格式错误" in note


def test_all_sessions_ended(sample_next_data, target, settings):
    """场景12 边界: 所有场次已结束 -> 无场可选, 明确报错而非硬打."""
    data = copy.deepcopy(sample_next_data)
    for entries in data["props"]["pageProps"]["activityData"]["twoDaySeckillState"]["flashResult"].values():
        for entry in entries:
            entry["startTime"] = 1793000000000
            entry["endTime"] = 1793000001000
    parsed = tc.parse_activity(make_next_data_html(data), target)
    settings["seckill"]["manual_datetime"] = ""
    sess, note = tc.pick_session(parsed, settings, now_ms=PAGE_NOW_MS)
    assert sess is None


def test_server_clock_sync(monkeypatch):
    """对时: Date 头(秒精度) 校准后 skew 应在合理范围, 且绝不抛异常."""

    class FakeResp:
        headers = {"Date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")}

    def fake_head(url, timeout=None, allow_redirects=False):
        return FakeResp()

    monkeypatch.setattr(tc.requests, "head", fake_head)
    clock = tc.ServerClock(samples=3)
    ok, note = clock.sync("https://example.com")
    assert ok
    assert -2000 <= clock.skew_ms <= 2500  # 秒精度截断 + 半 RTT 修正的正常范围


def test_server_clock_sync_failure(monkeypatch):
    """对时失败不致命: 回退本地钟 (skew=0)."""

    def fake_head(url, timeout=None, allow_redirects=False):
        raise OSError("network down")

    monkeypatch.setattr(tc.requests, "head", fake_head)
    clock = tc.ServerClock(samples=2)
    ok, note = clock.sync("https://example.com")
    assert not ok and clock.skew_ms == 0
    assert "失败" in note
