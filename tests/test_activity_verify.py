"""场景组: 活动页解析与规格/价格红线校验 (页面打不开/商品不存在/规格不符/超价等).

变异测试原则: 同一商品出现在页面多个场次, 篡改必须覆盖全部场次 (iter_sales),
否则解析器取到的第一场未被篡改, 测试形同虚设.
"""

import copy

import tcutil as tc
from conftest import iter_sales, make_next_data_html


def _verify(data, target):
    parsed = tc.parse_activity(make_next_data_html(data), target)
    return parsed, tc.verify_target(parsed, target)


def _status_map(rpt):
    return {i.name: i.status for i in rpt.items}


def test_good_page_all_core_checks_pass(sample_next_data, target):
    """基准: 真实页面样本 -> 核心项全过, 只有 IPv4/资格 待确认."""
    parsed, rpt = _verify(sample_next_data, target)
    assert parsed["ok"] is True
    assert parsed["activity_id"] == 166111704417621
    sale = parsed["sale"]
    assert sale["act_id"] == 1996855673581677
    assert sale["sku_type"] == "bundle_budget_mc_lg4_01"
    assert sale["price_fen"] == 3800
    assert parsed["price_fen_all"] == [3800]  # 4 场全是 38 元
    st = _status_map(rpt)
    assert st["活动页可达"] == "pass"
    assert st["商品类型"] == "pass"
    assert st["规格核对(4核4G/3M/40G SSD/300G)"] == "pass"
    assert st["价格红线(<=38元)"] == "pass"
    assert st["中国大陆地域"] == "pass"
    assert st["地域优先级"] == "pass"
    assert st["购买时长"] == "pass"
    assert st["购买数量"] == "pass"
    # 无法从页面核实的不许谎报通过
    assert st["独立公网 IPv4"] == "pending"
    assert st["购买资格"] == "pending"


def test_page_unreachable(sample_next_data, target):
    """场景5: 活动页打不开(无 __NEXT_DATA__) -> 解析失败, 校验失败."""
    parsed = tc.parse_activity("<html><body>404</body></html>", target)
    assert parsed["ok"] is False
    rpt = tc.verify_target(parsed, target)
    assert rpt.failed and "活动页解析" in _status_map(rpt)


def test_goods_not_found(sample_next_data, target):
    """场景6: 页面商品不含目标 SKU -> 目标商品 fail."""
    data = copy.deepcopy(sample_next_data)
    for entries in data["props"]["pageProps"]["activityData"]["twoDaySeckillState"]["flashResult"].values():
        for entry in entries:
            entry["sales"] = []
    parsed, rpt = _verify(data, target)
    assert parsed["sale"] is None
    st = _status_map(rpt)
    assert st["目标商品"] == "fail"


def test_not_4c4g(sample_next_data, target):
    """场景7: 商品不是 4核4G (全部场次 cpu/ram=2) -> 规格核对 fail."""
    data = copy.deepcopy(sample_next_data)
    for sale in iter_sales(data):
        vm = sale["typeMap"]["data"]["1"][0]["valueMap"]
        vm["cpu"] = "2"
        vm["ram"] = "2"
    _, rpt = _verify(data, target)
    st = _status_map(rpt)
    assert st["规格核对(4核4G/3M/40G SSD/300G)"] == "fail"


def test_not_mainland(sample_next_data, target):
    """场景8: 商品非中国大陆 (area=2 海外) -> 中国大陆地域 fail."""
    data = copy.deepcopy(sample_next_data)
    for sale in iter_sales(data):
        sale["areaOptions"] = [{"label": "境外", "value": 2}]
    _, rpt = _verify(data, target)
    st = _status_map(rpt)
    assert st["中国大陆地域"] == "fail"


def test_no_public_ipv4_is_pending_not_pass(sample_next_data, target):
    """场景9: 页面无公网 IPv4 信息 -> 必须显示待确认, 不得写成通过."""
    _, rpt = _verify(sample_next_data, target)
    st = _status_map(rpt)
    assert st["独立公网 IPv4"] == "pending"


def test_price_over_38(sample_next_data, target):
    """场景10: 全部场次价格 68 元 > 38 红线 -> 价格红线 fail (任何模式都禁下单)."""
    data = copy.deepcopy(sample_next_data)
    for sale in iter_sales(data):
        sale["priceData"]["real_cost"] = 6800
    _, rpt = _verify(data, target)
    st = _status_map(rpt)
    assert st["价格红线(<=38元)"] == "fail"
    assert not rpt.all_pass


def test_price_over_38_in_any_session(sample_next_data, target):
    """价格红线保守原则: 只要有一场卖 68 元 (其余 38), 也必须 fail."""
    data = copy.deepcopy(sample_next_data)
    flash = data["props"]["pageProps"]["activityData"]["twoDaySeckillState"]["flashResult"]
    flash["2026-11-02"][0]["sales"][0]["priceData"]["real_cost"] = 6800  # 只改一场
    parsed, rpt = _verify(data, target)
    assert parsed["price_fen_all"] == [3800, 6800]
    st = _status_map(rpt)
    assert st["价格红线(<=38元)"] == "fail"


def test_price_unknown(sample_next_data, target):
    """场景11: 无法确定价格 -> 待确认 (真实下单一律拒绝, 模拟可演练)."""
    data = copy.deepcopy(sample_next_data)
    for sale in iter_sales(data):
        sale["priceData"] = {}
    _, rpt = _verify(data, target)
    st = _status_map(rpt)
    assert st["价格红线(<=38元)"] == "pending"
    assert rpt.all_pass  # 不算 fail, 但 real 模式另有硬拦截


def test_lighthouse_agent_rejected(sample_next_data, target):
    """相似品识别: dictItem.Slug=lighthouse-agent (Lighthouse Agent) -> 商品类型 fail."""
    data = copy.deepcopy(sample_next_data)
    for sale in iter_sales(data):
        sale["dictItem"]["Slug"] = "lighthouse-agent"
    _, rpt = _verify(data, target)
    st = _status_map(rpt)
    assert st["商品类型"] == "fail"


def test_wrong_time_span(sample_next_data, target):
    """时长不符: 页面只卖 1 个月 -> 购买时长 fail."""
    data = copy.deepcopy(sample_next_data)
    for sale in iter_sales(data):
        sale["timeSpanUnitOptions"] = [{"label": "1个月", "value": "1m"}]
    _, rpt = _verify(data, target)
    st = _status_map(rpt)
    assert st["购买时长"] == "fail"


def test_region_no_intersection(sample_next_data, target):
    """地域无交集: 页面只有中国香港 -> 地域优先级 fail."""
    data = copy.deepcopy(sample_next_data)
    for sale in iter_sales(data):
        sale["regionMap"]["data"]["1"] = [{"label": "中国香港", "regionEn": "ap-hongkong", "value": 5}]
    _, rpt = _verify(data, target)
    st = _status_map(rpt)
    assert st["地域优先级"] == "fail"


def test_name_keyword_missing(sample_next_data, target):
    """商品名关键词缺失 (没有 300GB 字样) -> 关键词 fail."""
    data = copy.deepcopy(sample_next_data)
    for sale in iter_sales(data):
        sale["seckill_act_extra"]["skuList"] = ["4核4G3M"]
        sale["goods_name"] = "日常大促-秒杀-轻量 4核4G3M$开箱即用"
        sale["$goods"]["name"] = sale["goods_name"]
    _, rpt = _verify(data, target)
    st = _status_map(rpt)
    assert st["商品名关键词"] == "fail"


def test_fetch_activity_falls_back_to_playwright(sample_html, monkeypatch):
    """防护页拦截回退: requests 拿到 EO 挑战页时自动改用 Playwright 渲染."""
    import tcutil as tc
    import snap_up_server as sus

    class ChallengeResp:
        text = "<script>function a(a){EO_Bot_Ssid...</script>"
        status_code = 200

        def raise_for_status(self):
            return None

    class ChallengeSession:
        def get(self, url, headers=None, timeout=None):
            return ChallengeResp()

    def fake_pw_fetch(url, **kw):
        return sample_html  # 模拟 Playwright 渲染出的完整活动页

    monkeypatch.setattr(tc, "fetch_html_via_playwright", fake_pw_fetch)
    html = sus.fetch_activity(ChallengeSession(), "https://example.com")
    assert "__NEXT_DATA__" in html  # 回退后拿到了真实页面


def test_fetch_activity_prefers_direct(sample_html, monkeypatch):
    """requests 直连成功 (含 __NEXT_DATA__) 时不启动 Playwright."""
    import snap_up_server as sus

    class OkResp:
        text = sample_html
        status_code = 200

        def raise_for_status(self):
            return None

    class OkSession:
        def get(self, url, headers=None, timeout=None):
            return OkResp()

    def boom(url, **kw):
        raise AssertionError("不应走 Playwright 回退")

    import tcutil as tc

    monkeypatch.setattr(tc, "fetch_html_via_playwright", boom)
    html = sus.fetch_activity(OkSession(), "https://example.com")
    assert "__NEXT_DATA__" in html
