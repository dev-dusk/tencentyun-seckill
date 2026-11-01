"""场景组: 登录抓包 (请求捕获/模板校验/场次窗口安全判定)."""

import json

import pytest

pytest.importorskip("playwright")  # get_cookies 依赖 playwright, 未装则跳过本组

import get_cookies as gc  # noqa: E402


class FakeRequest:
    def __init__(self, url, post_data=None, method="POST"):
        self.url = url
        self.post_data = post_data
        self.method = method


def test_capture_only_records_act_api():
    cap = gc.ApiCapture()
    cap.on_request(FakeRequest("https://cloud.tencent.com/act/pro/x", None, "GET"))
    cap.on_request(FakeRequest("https://act-api.cloud.tencent.com/dianshi/check-available",
                               '{"activity_id":166111704417621}'))
    cap.on_request(FakeRequest("https://buy.cloud.tencent.com/order", "{}"))
    assert len(cap.records) == 1
    assert cap.records[0]["payload"] == {"activity_id": 166111704417621}


def test_capture_never_stores_headers_or_cookies():
    """安全红线: 捕获记录只含 url/method/payload, 绝无凭证字段."""
    cap = gc.ApiCapture()
    cap.on_request(FakeRequest("https://act-api.cloud.tencent.com/dianshi/do-goods", "{}"))
    assert set(cap.records[0].keys()) == {"url", "method", "payload"}


def _do_goods_payload(act_id=1996855673581677, sku="bundle_budget_mc_lg4_01", with_business=True):
    payload = {
        "activity_id": 166111704417621,
        "goods": [{"act_id": act_id, "type": sku, "goods_param": {"regionId": 4, "type": sku}}],
    }
    if with_business:
        payload["business"] = {"id": 24448, "from": "lightningDeals"}
    return payload


def test_extract_capture_good(params_like, settings_like):
    """正确捕获 do-goods -> 模板入库 + business 提取."""
    cap = gc.ApiCapture()
    cap.on_request(FakeRequest("https://act-api.cloud.tencent.com/dianshi/do-goods",
                               json.dumps(_do_goods_payload())))
    assert gc.extract_capture_into_params(cap, params_like, settings_like) is True
    assert params_like["BUSINESS"] == {"id": 24448, "from": "lightningDeals"}
    assert params_like["capture"]["do_goods"]["payload"]["goods"][0]["act_id"] == 1996855673581677
    assert params_like["verified_items"]["buy_api"]["verified"] is True


def test_extract_capture_wrong_goods_rejected(params_like, settings_like):
    """抓到别的商品的下单请求 -> 校验不符, 丢弃."""
    cap = gc.ApiCapture()
    cap.on_request(FakeRequest("https://act-api.cloud.tencent.com/dianshi/do-goods",
                               json.dumps(_do_goods_payload(act_id=111, sku="cvm_2c2g"))))
    assert gc.extract_capture_into_params(cap, params_like, settings_like) is False
    assert "do_goods" not in params_like["capture"]


def test_extract_capture_business_omitted(params_like, settings_like):
    """页面请求确实不带 business -> 记录为显式省略 (而非猜测)."""
    cap = gc.ApiCapture()
    cap.on_request(FakeRequest("https://act-api.cloud.tencent.com/dianshi/do-goods",
                               json.dumps(_do_goods_payload(with_business=False))))
    assert gc.extract_capture_into_params(cap, params_like, settings_like) is True
    assert params_like["BUSINESS"] is None
    assert params_like["capture"].get("business_omitted") is True


def test_extract_capture_check_available(params_like, settings_like):
    cap = gc.ApiCapture()
    cap.on_request(FakeRequest("https://act-api.cloud.tencent.com/dianshi/check-available",
                               '{"activity_id":166111704417621,"goods":[{"act_id":1996855673581677,"region_id":[4,8,1]}],"preview":0}'))
    gc.extract_capture_into_params(cap, params_like, settings_like)
    assert params_like["capture"]["check_available"]["payload"]["goods"][0]["region_id"] == [4, 8, 1]
    assert params_like["verified_items"]["check_api"]["verified"] is True


def test_active_session_window_guard():
    """抓包安全闸: 场次进行窗口内 (开始前 5 分钟到结束) 不得引导点击下单."""
    sessions = [
        {"start_ms": 1793584800000, "end_ms": 1793592000000},  # 11-02 10:00~12:00
    ]
    parsed = {"sessions": sessions}
    active, sess = gc.in_active_session_window(parsed, now_ms=1793584800000 - 60_000)
    assert active is True  # 开抢前 1 分钟: 危险期
    active, _ = gc.in_active_session_window(parsed, now_ms=1793592000000 + 1)
    assert active is False  # 场次结束后: 安全
    active, _ = gc.in_active_session_window(parsed, now_ms=1793584800000 - 400_000)
    assert active is False  # 开抢前 >5 分钟: 安全


@pytest.fixture
def params_like():
    return {
        "ACTIVITY_ID": 166111704417621,
        "BUY_ACT_ID": 1996855673581677,
        "SKU": "bundle_budget_mc_lg4_01",
        "BUSINESS": None,
        "GOODS_PARAM": {},
        "capture": {},
        "verified_items": {"page_params": {"verified": True}, "check_api": {"verified": False},
                           "buy_api": {"verified": False}},
    }


@pytest.fixture
def settings_like():
    return {"target": {"sku_type": "bundle_budget_mc_lg4_01"}}
