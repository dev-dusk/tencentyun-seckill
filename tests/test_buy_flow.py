"""场景组: 下单流程 (限流退避/超时已建单/未付单/成功/价格复核/付款页/Ctrl+C)."""

import copy

import pytest

import snap_up_server as sus
import tcutil as tc


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return FakeResp(self.payload if not callable(self.payload) else self.payload(len(self.calls)))


@pytest.fixture
def params():
    return {
        "ACTIVITY_ID": 166111704417621,
        "CHECK_ACT_ID": 1996855673581677,
        "BUY_ACT_ID": 1996855673581677,
        "SKU": "bundle_budget_mc_lg4_01",
        "BUSINESS": None,
        "GOODS_PARAM": {
            "BlueprintId": "LINUX_UNIX", "area": 1, "ddocUnionConnect": 0, "goodsNum": 1,
            "imageId": "lhbp-eqora508", "scenario": "0", "timeSpanUnit": "12m",
            "type": "bundle_budget_mc_lg4_01", "zone": "",
        },
        "capture": {},
    }


@pytest.fixture
def cfg():
    return copy.deepcopy(tc.DEFAULT_SETTINGS["runtime"])


# ---------- 库存接口 ----------

def test_check_available_no_stock(params):
    """场景13: 接口正常但无库存 -> 有货列表为空, 不误报."""
    sess = FakeSession({"code": 0, "msg": "ok", "data": [{"quota": {
        "4": {"bundle_budget_mc_lg4_01": {"available": 0}},
        "1": {"bundle_budget_mc_lg4_01": {"available": 0}},
        "8": {"bundle_budget_mc_lg4_01": {"available": 0}},
    }}]})
    ok, in_stock, raw = sus.check_available(sess, {}, params, [4, 1, 8])
    assert ok is True and in_stock == []


def test_check_available_in_stock_only_some_regions(params):
    sess = FakeSession({"code": 0, "msg": "ok", "data": [{"quota": {
        "4": {"bundle_budget_mc_lg4_01": {"available": 3}},
        "1": {"bundle_budget_mc_lg4_01": {"available": 0}},
    }}]})
    ok, in_stock, _ = sus.check_available(sess, {}, params, [4, 1, 8])
    assert ok and in_stock == [4]


def test_check_available_empty_data_no_crash(params):
    """历史 bug: data 为空列表时不得 IndexError."""
    sess = FakeSession({"code": 0, "msg": "ok", "data": []})
    ok, in_stock, _ = sus.check_available(sess, {}, params, [4])
    assert ok is True and in_stock == []


def test_check_available_not_logined(params, capsys):
    """场景4: Cookie 过期 -> NOT-LOGINED, 提示重新登录."""
    sess = FakeSession({"code": "NOT-LOGINED", "msg": "用户未登录"})
    ok, _, _ = sus.check_available(sess, {}, params, [4])
    assert ok is False
    assert "重新运行 login.bat" in capsys.readouterr().out


# ---------- 下单返回码分类 ----------

def _dry_round(monkeypatch, round_no, cfg):
    monkeypatch.setattr(sus, "SIM", sus.SimulationProvider())
    for _ in range(round_no - 1):  # 快进到目标轮次
        sus.buy_round(None, {}, {"capture": {}}, [4, 1, 8], _, "url", cfg, True)
    return sus.buy_round(None, {}, {"capture": {}}, [4, 1, 8], round_no, "url", cfg, True)


def test_round_not_started_then_rate_limited(monkeypatch, cfg):
    """场景12/14: 未开始继续打; 限流返回 rate_limited."""
    outcome, _, hist, _ = _dry_round(monkeypatch, 1, cfg)
    assert outcome == "retry" and hist.get(1100136) == 3
    outcome, detail, hist, _ = _dry_round(monkeypatch, 2, cfg)
    assert outcome == "rate_limited" and hist.get(408) == 3


def test_round_timeout_does_not_abort(monkeypatch, cfg):
    """场景15: 下单超时(返回 None)不算失败, 不中断继续打; 已建单由下一轮 1101411 揭示(见 test_unpaid_direct)."""
    outcome, _, hist, _ = _dry_round(monkeypatch, 3, cfg)
    assert outcome == "retry" and None in hist  # 超时轮: 不判定成功也不误判失败
    outcome, _, hist, _ = _dry_round(monkeypatch, 4, cfg)
    assert outcome == "retry" and hist.get(1100205) == 3  # 繁忙轮照常继续


def test_round_busy_then_success(monkeypatch, cfg):
    """场景18: 繁忙继续, code=0 成功即停."""
    outcome, _, _, _ = _dry_round(monkeypatch, 4, cfg)
    assert outcome == "retry"
    outcome, _, _, payload = _dry_round(monkeypatch, 5, cfg)
    assert outcome == "success" and payload["code"] == 0


def test_unpaid_direct(params, monkeypatch, cfg):
    """场景16: 已有未支付订单 -> unpaid 立即停."""
    fake_result = [{"code": 1101411, "msg": '存在未支付订单 <a href="//console.cloud.tencent.com/expense/deal/info?bigDealId=38000261">x</a>'}
                   for _ in range(3)]
    monkeypatch.setattr(sus, "buy_now", lambda *a, **k: (4, fake_result[0]))
    outcome, detail, _, _ = sus.buy_round(None, {}, params, [4, 1, 8], 1, "url", cfg, False)
    assert outcome == "unpaid" and "bigDealId=38000261" in detail


def test_auth_error_stops(params, monkeypatch, cfg):
    monkeypatch.setattr(sus, "buy_now", lambda *a, **k: (4, {"code": "CSRF-ERROR", "msg": "csrf invalid"}))
    outcome, detail, _, _ = sus.buy_round(None, {}, params, [4], 1, "url", cfg, False)
    assert outcome == "fatal" and "CSRF-ERROR" in detail


def test_face_required_stops(params, monkeypatch, cfg):
    """场景17: 人脸核身未完成 (1100202) -> fatal 停."""
    monkeypatch.setattr(sus, "buy_now", lambda *a, **k: (4, {"code": 1100202, "msg": "需要人脸核身"}))
    outcome, detail, _, _ = sus.buy_round(None, {}, params, [4], 1, "url", cfg, False)
    assert outcome == "fatal" and "1100202" in detail


# ---------- 退避 ----------

def test_backoff_exponential_with_cap(cfg):
    """场景14: 限流指数退避 1.5 -> 3 -> 6 -> 8(封顶)."""
    assert sus.next_backoff(1, cfg) == 1.5
    assert sus.next_backoff(2, cfg) == 3.0
    assert sus.next_backoff(3, cfg) == 6.0
    assert sus.next_backoff(4, cfg) == 8.0
    assert sus.next_backoff(9, cfg) == 8.0


# ---------- 成功处理与价格复核 ----------

SUCCESS_PAYLOAD = {
    "code": 0, "msg": "ok",
    "data": {"dealName": "轻量应用服务器Lighthouse", "bigDealId": "38000261111000002",
             "totalCost": 3800,
             "payUrl": "https://console.cloud.tencent.com/expense/deal/info?bigDealId=38000261111000002"},
}


def test_handle_success_price_ok(capsys):
    sus.handle_success(copy.deepcopy(SUCCESS_PAYLOAD), 38.0, dry=True, open_browser=False)
    out = capsys.readouterr().out
    assert "38000261111000002" in out          # 订单号提取
    assert "复核通过" in out and "38.00" in out  # 金额 3800分=38元 <= 红线


def test_handle_success_price_over_redline(capsys):
    """成功但金额 68 元 -> 大声警告不要支付 (价格红线最高优先级)."""
    payload = copy.deepcopy(SUCCESS_PAYLOAD)
    payload["data"]["totalCost"] = 6800
    sus.handle_success(payload, 38.0, dry=True, open_browser=False)
    out = capsys.readouterr().out
    assert "超出红线" in out and "请勿支付" in out


def test_handle_success_dry_does_not_open_browser(capsys):
    sus.handle_success(copy.deepcopy(SUCCESS_PAYLOAD), 38.0, dry=True, open_browser=True)
    out = capsys.readouterr().out
    assert "[模拟] 已抑制打开浏览器" in out


def test_open_url_failure_graceful(monkeypatch, capsys):
    """场景19: 打不开付款页 -> 打印手动链接, 不崩."""
    def boom(url):
        raise OSError("no browser")

    monkeypatch.setattr(sus.webbrowser, "open", boom)
    assert sus.open_url_safely("https://console.cloud.tencent.com/x", dry=False) is False
    assert "请手动访问" in capsys.readouterr().out


def test_response_price_fen():
    assert sus.response_price_fen(SUCCESS_PAYLOAD) == 3800
    assert sus.response_price_fen({"code": 0}) is None


def test_extract_order_url_variants():
    assert sus.extract_order_url({"msg": "bigDealId=12345 请处理"}) .endswith("bigDealId=12345")
    assert sus.extract_order_url({"msg": '<a href="//console.cloud.tencent.com/expense/deal/info?bigDealId=9">x</a>'}).startswith("https://")
    assert sus.extract_order_url({"msg": ""}) is None


# ---------- 请求模板 ----------

def test_build_buy_payload_default_no_business_guess(params):
    data = sus.build_buy_payload(params, 8, "https://cloud.tencent.com/act/pro/double11-202611")
    assert data["goods"][0]["goods_param"]["regionId"] == 8
    assert data["goods"][0]["act_id"] == 1996855673581677
    assert "business" not in data  # 未验证的 business 绝不猜测拼装


def test_build_buy_payload_with_business(params):
    params["BUSINESS"] = {"id": 24448, "from": "lightningDeals"}
    data = sus.build_buy_payload(params, 4, "url")
    assert data["business"] == {"id": 24448, "from": "lightningDeals"}


def test_build_buy_payload_uses_captured_template(params):
    params["capture"]["do_goods"] = {
        "url": "https://act-api.cloud.tencent.com/dianshi/do-goods",
        "payload": {"activity_id": 166111704417621,
                    "business": {"id": 99999, "from": "double11"},
                    "goods": [{"act_id": 1996855673581677, "type": "bundle_budget_mc_lg4_01",
                               "goods_param": {"regionId": 4, "goodsNum": 1, "timeSpanUnit": "12m",
                                               "type": "bundle_budget_mc_lg4_01"}}]},
    }
    data = sus.build_buy_payload(params, 8, "url")
    assert data["business"] == {"id": 99999, "from": "double11"}   # 捕获模板原样复用
    assert data["goods"][0]["goods_param"]["regionId"] == 8          # 仅注入地域


# ---------- Ctrl+C ----------

def test_ctrl_c_propagates_from_wait():
    """场景20: Ctrl+C (KeyboardInterrupt) 从等待环节传出, 由 main 捕获安全退出."""

    class BombedClock:
        def now_ms(self):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        sus.wait_until(9999999999999, BombedClock(), "测试")
