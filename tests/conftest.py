"""测试公共设施: 真实页面样本构建 + 路径隔离.

样本数据来自 2026-11-01 对 double11-202611 活动页 __NEXT_DATA__ 的实测提取,
数值未做任何修改; 测试通过深拷贝+定向篡改构造各类异常场景.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import tcutil as tc  # noqa: E402

# 2026-11-01 实测的完整 sale 对象 (与 tests/data/next_data_sample.json 配套)
REAL_SALE = {
    "act_id": 1996855673581677,
    "goodsId": 1996855673581677,
    "activityId": 166111704417621,
    "goods_name": "日常大促-秒杀-轻量 4核4G3M$开箱即用，高带宽",
    "product_name": "lighthouse_v5",
    "productType": "lighthouse_v5",
    "user_num": 1,
    "maxNum": 1,
    "minNum": 1,
    "step": 1,
    "seckill_act_extra": {
        "attachList": [
            {"label": "地域", "value": "广州/上海/北京"},
            {"label": "有效期", "value": "自购买一年内有效"},
        ],
        "skuList": ["4核4G3M", "300GB 月流量"],
        "subTitle": "建站、Web应用、电商独立站等高性价比选",
        "title": "轻量应用服务器",
    },
    "dictItem": {
        "DictId": 2486,
        "IntroPageLink": "https://cloud.tencent.com/product/lighthouse",
        "Name": "轻量应用服务器",
        "ParentId": 500,
        "Slug": "lighthouse",
        "Type": 1,
    },
    "typeMap": {
        "fields": ["area"],
        "data": {
            "1": [
                {
                    "label": "国内[特惠型][cpu4核][内存4G][SSD40GB][峰值带宽3M][流量包300GB]",
                    "value": "bundle_budget_mc_lg4_01",
                    "valueMap": {
                        "bandwidth": "3M",
                        "cpu": "4",
                        "dataDiskSize": "40",
                        "monthlyTraffic": "300",
                        "ram": "4",
                        "systemDisk": "40GB SSD盘",
                        "type": "4核4G3M",
                        "prompt": "40GB SSD盘 300GB流量 3M峰值带宽",
                    },
                }
            ]
        },
    },
    "regionMap": {
        "fields": ["area"],
        "data": {
            "1": [
                {"label": "上海", "regionEn": "ap-shanghai", "value": 4},
                {"label": "北京", "regionEn": "ap-beijing", "value": 8},
                {"label": "广州", "regionEn": "ap-guangzhou", "value": 1},
            ]
        },
    },
    "areaOptions": [{"descp": {}, "label": "内地", "value": 1}],
    "timeSpanUnitOptions": [{"label": "1年", "unitLabel": "1年", "value": "12m", "valueMap": "1年"}],
    "BlueprintIdOptions": [{"descp": {}, "label": "linux类", "value": "LINUX_UNIX"}],
    "scenarioOptions": [{"descp": {}, "label": "不区分场景", "value": "0"}],
    "defaultGoodsParam": {
        "BlueprintId": "LINUX_UNIX",
        "area": 1,
        "ddocUnionConnect": 0,
        "goodsNum": 1,
        "imageId": "lhbp-eqora508",
        "regionId": 4,
        "scenario": "0",
        "timeSpanUnit": "12m",
        "type": "bundle_budget_mc_lg4_01",
        "zone": "",
    },
    "defaultOrderParam": {
        "BlueprintId": "LINUX_UNIX",
        "area": 1,
        "ddocUnionConnect": 0,
        "goodsNum": 1,
        "imageId": "lhbp-eqora508",
        "regionId": 4,
        "scenario": "0",
        "timeSpanUnit": "12m",
        "type": "bundle_budget_mc_lg4_01",
        "zone": "",
    },
    "priceData": {
        "act_id": 1996855673581677,
        "amount_unit": "pent",
        "currency": "CNY",
        "pid": 1050604,
        "policy": 4.8718,
        "price": 6500,
        "real_cost": 3800,
        "total_cost": 78000,
        "timeSpan": 12,
        "timeUnit": "m",
        "v2_act_id": "199685_1",
        "product_code": "p_lighthouse",
        "sub_product_code": "sp_lighthouse_bundle_budget_mc",
    },
    "priceGoodsParams": ["BlueprintId", "area", "goodsNum", "regionId", "scenario", "timeSpanUnit", "type"],
    "progress": 0,
    "$goods": {
        "id": 199685,
        "big_goods_id": "1996855673581677",
        "activity_id": 166111704417621,
        "name": "日常大促-秒杀-轻量 4核4G3M$开箱即用，高带宽",
        "product_name": "lighthouse_v5",
        "total_num": 100000,
        "user_num": 1,
        "user_num_limit": 1,
        "total_limit": 1,
        "flash_sales": 1,
        "status": 0,
        "limits": {
            "goodsNum": {"range": {"max": 1, "min": 1, "step": 1}},
            "regionId": {"constraint": {"field": ["area"], "value": {"1": [4, 8, 1]}}},
            "type": {"constraint": {"field": ["area"], "value": {"1": ["bundle_budget_mc_lg4_01"]}}},
            "timeSpanUnit": {"option": ["12m"]},
            "imageId": {"any": 1},
            "area": {"option": [1]},
        },
        "user_condition": {
            "activated": 1,
            "company": 0,
            "cooperator": 0,
            "local_user": 1,
            "qcloud_new_user_by_idcard": 2,
            "real_name": 1,
            "registered": 1,
            "restricted_by": 2,
        },
        "act_id": 1996855673581677,
    },
}


def load_sample():
    """读样本文件并把 __SALE__ 占位替换为完整真实 sale 对象."""
    raw = json.loads((Path(__file__).parent / "data" / "next_data_sample.json").read_text(encoding="utf-8"))
    raw.pop("_comment", None)

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if node == "__SALE__":
            return copy.deepcopy(REAL_SALE)
        return node

    return walk(raw)


@pytest.fixture(scope="session")
def sample_next_data():
    return load_sample()


@pytest.fixture
def sample_html(sample_next_data):
    """把样本包成完整活动页 HTML."""
    payload = json.dumps(sample_next_data, ensure_ascii=False)
    return (
        "<!DOCTYPE html><html><head><title>让每一个 Agent，都跑在云上</title>"
        f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'
        "</head><body></body></html>"
    )


@pytest.fixture
def target():
    return copy.deepcopy(tc.DEFAULT_SETTINGS["target"])


@pytest.fixture
def settings():
    return copy.deepcopy(tc.DEFAULT_SETTINGS)


@pytest.fixture
def isolate_project_files(tmp_path, monkeypatch):
    """把 tcutil 的项目级文件路径指到临时目录, 避免测试污染真实文件."""
    monkeypatch.setattr(tc, "COOKIES_PATH", tmp_path / "cookies.json")
    monkeypatch.setattr(tc, "ACTIVITY_PARAMS_PATH", tmp_path / "activity_params.json")
    monkeypatch.setattr(tc, "AUTO_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(tc, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(tc, "LOG_DIR", tmp_path / "logs")
    return tmp_path


def make_next_data_html(next_data_obj):
    payload = json.dumps(next_data_obj, ensure_ascii=False)
    return f'<html><script id="__NEXT_DATA__" type="application/json">{payload}</script></html>'


def iter_sales(next_data):
    """遍历样本里所有场次的所有 sale (同一商品多场: 变异测试必须改全部场次)."""
    flash = next_data["props"]["pageProps"]["activityData"]["twoDaySeckillState"]["flashResult"]
    for entries in flash.values():
        for entry in entries:
            for sale in entry.get("sales") or []:
                yield sale
