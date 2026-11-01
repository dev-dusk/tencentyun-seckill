"""共享工具模块: 配置加载、北京时间、日志、会话、活动页解析、规格与价格校验.

snap_up_server.py 与 get_cookies.py 共用, 并为 tests/ 提供可离线测试的纯函数.
"""

import contextlib
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"
ACTIVITY_PARAMS_PATH = BASE_DIR / "activity_params.json"
AUTO_CONFIG_PATH = BASE_DIR / "config.json"
COOKIES_PATH = BASE_DIR / "cookies.json"
LOG_DIR = BASE_DIR / "logs"

# ---------------- 北京时间 (不依赖 Windows 系统时区) ----------------
# 优先 zoneinfo(需 pip 包 tzdata, Windows 无系统 tzdata), 失败回退固定 UTC+8
try:
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo("Asia/Shanghai").utcoffset(datetime(2026, 1, 1))
        BJ_TZ = ZoneInfo("Asia/Shanghai")
    except Exception:
        BJ_TZ = timezone(timedelta(hours=8))
except Exception:
    BJ_TZ = timezone(timedelta(hours=8))


def bj_now():
    """当前北京时间 (aware datetime)."""
    return datetime.now(BJ_TZ)


def bj_now_ms():
    """当前北京时间毫秒时间戳."""
    return int(time.time() * 1000)


def bj_str(dt=None, with_ms=False):
    """北京时间格式化 'YYYY-MM-DD HH:MM:SS'."""
    dt = dt or bj_now()
    fmt = "%Y-%m-%d %H:%M:%S" + (".%f" if with_ms else "")
    return dt.strftime(fmt)


def parse_bj(s):
    """解析 'YYYY-MM-DD HH:MM:SS' 为北京时间的 aware datetime."""
    return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ_TZ)


def bj_to_ms(dt):
    """北京时间 aware datetime -> 毫秒时间戳."""
    return int(dt.timestamp() * 1000)


def ms_to_bj_str(ms):
    """毫秒时间戳 -> 'YYYY-MM-DD HH:MM:SS' (北京时间)."""
    if ms is None:
        return "?"
    return datetime.fromtimestamp(ms / 1000, BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 日志: 控制台 + 落盘 双写 ----------------
class Tee:
    """双写: 控制台 + 落盘(每次写即 open/close, 崩溃也不丢日志)."""

    def __init__(self, stream, log_path):
        self.stream = stream
        self.log_path = log_path

    def write(self, data):
        try:
            self.stream.write(data)
        except Exception:
            pass
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(data)
        with contextlib.suppress(Exception):
            self.stream.flush()

    def flush(self):
        with contextlib.suppress(Exception):
            self.stream.flush()

    def isatty(self):
        return self.stream.isatty() if hasattr(self.stream, "isatty") else False


def setup_logging(prefix):
    """__main__ 首行调用: 之后所有 print 同步落盘 logs/<prefix>_*.log, 返回日志路径."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{prefix}_{bj_now().strftime('%Y%m%d_%H%M%S')}.log"
    with contextlib.suppress(Exception):
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8")  # GBK 控制台防 emoji 炸
    sys.stdout = Tee(sys.stdout, log_path)
    sys.stderr = Tee(sys.stderr, log_path)
    return log_path


# ---------------- 配置读写 ----------------
def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    """写 JSON (UTF-8, 缩进 2, 保留中文)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


DEFAULT_SETTINGS = {
    "activity_url": "https://cloud.tencent.com/act/pro/double11-202611",
    "target": {
        # 商品类型必须是轻量应用服务器本体 (Slug=lighthouse), 排除 Lighthouse Agent 等相似品
        "product_slug": "lighthouse",
        "sku_type": "bundle_budget_mc_lg4_01",
        "name_keywords": ["4核4G", "300GB"],  # 商品名/卖点必须同时包含
        "spec": {  # 与页面 typeMap.valueMap 逐项核对
            "cpu": "4",
            "ram": "4",
            "bandwidth": "3M",
            "monthlyTraffic": "300",
            "systemDisk": "40GB SSD",
        },
        "time_span_unit": "12m",
        "goods_num": 1,
        "area": 1,  # 1=中国大陆
        "max_price_yuan": 38.0,  # 价格红线: 订单总金额超过它一律不提交
        "region_priority": [4, 1, 8],  # 上海 -> 广州 -> 北京
    },
    "seckill": {
        "auto_detect_session": True,  # 自动识别下一场 (页面场次数据)
        "manual_datetime": "",  # 非空则手动指定, 如 "2026-11-02 15:00:00"
        "lead_ms": 1500,
        "retry_window_s": 30,
    },
    "runtime": {
        "mode": "dry",  # dry=模拟(不真下单) | real=真实下单(还需 --real 与交互确认)
        "check_before_buy": False,  # True 则每轮先查库存, 有货地域才打
        "round_gap_s": 0.5,
        "round_jitter_s": 0.5,
        "buy_timeout_s": 3.0,
        "backoff_408_s": 1.5,
        "backoff_max_s": 8.0,
        "max_consecutive_rate_limit": 5,
        "max_rounds": 40,
        "verbose": False,
    },
    "log": {
        "level": "info",
        "open_browser_on_success": True,
    },
}


def deep_merge(base, override):
    """递归合并: override 覆盖 base, 缺省取 base."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings():
    """读 settings.json 并与默认值合并; 缺文件也能跑 (全默认)."""
    user = load_json(SETTINGS_PATH) or {}
    return deep_merge(DEFAULT_SETTINGS, user)


def load_activity_params():
    """读活动技术参数 activity_params.json."""
    return load_json(ACTIVITY_PARAMS_PATH) or {}


def save_activity_params(params):
    save_json(ACTIVITY_PARAMS_PATH, params)


def load_auto_config():
    """读扫码时自动同步的 config.json (act_url / facecheck), 不存在返回空."""
    return load_json(AUTO_CONFIG_PATH) or {}


def check_env(importer=None):
    """依赖自检: 返回 [(名称, 是否可用, 说明)]. 供 install 验证与测试复用."""
    importer = importer or __import__
    items = []
    for mod in ("requests", "playwright", "zoneinfo", "pytest"):
        try:
            importer(mod)
            items.append((mod, True, ""))
        except ImportError as e:
            items.append((mod, False, str(e)))
    try:
        import tzdata  # noqa: F401  Windows 下 zoneinfo 依赖 tzdata 包

        items.append(("tzdata", True, ""))
    except ImportError as e:
        items.append(("tzdata", False, str(e)))
    return items


# ---------------- 会话 / CSRF ----------------
def compute_g_tk(skey):
    """腾讯经典 g_tk 算法: x-csrf-token 由 skey hash 得出."""
    h = 5381
    for ch in skey:
        h += (h << 5) + ord(ch)
    return str(h & 0x7FFFFFFF)


def mask_secret(value, keep=3):
    """脱敏: 保留头尾各 keep 位, 中间打码. 用于日志安全."""
    s = str(value or "")
    if len(s) <= keep * 2:
        return "***"
    return f"{s[:keep]}***{s[-keep:]}"


def build_session():
    """加载 cookies.json 建会话, 返回 (session, csrf_token, token_source).

    找不到文件抛 FileNotFoundError; 无 skey 时用 activity_params 手填值兜底.
    """
    if not COOKIES_PATH.exists():
        raise FileNotFoundError("找不到 cookies.json, 请先运行 login.bat (get_cookies.py) 扫码登录")
    cookies = load_json(COOKIES_PATH) or []
    session = requests.Session()
    skey = None
    for cookie in cookies:
        session.cookies.set(
            cookie.get("name", ""),
            cookie.get("value", ""),
            domain=cookie.get("domain", ""),
            path=cookie.get("path", "/"),
        )
        # skey 多域同值, 优先 .cloud.tencent.com 的
        if (
            cookie.get("name") == "skey"
            and cookie.get("value")
            and (skey is None or cookie.get("domain") == ".cloud.tencent.com")
        ):
            skey = cookie["value"]
    if skey:
        return session, compute_g_tk(skey), "skey 自动计算(g_tk)"
    manual = (load_activity_params().get("X_CSRF_TOKEN_MANUAL") or "").strip()
    if manual:
        return session, manual, "activity_params 手填值(兜底)"
    return session, None, "无 skey 且无手填兜底"


def make_headers(csrf_token, referer):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
        ),
        "referer": referer,
    }
    if csrf_token:
        headers["x-csrf-token"] = csrf_token
    return headers


# ---------------- 活动页解析 (__NEXT_DATA__) ----------------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def fetch_html_via_playwright(url, timeout_ms=45000, settle_ms=1500, with_cookies=False):
    """用已安装的 Playwright Chromium 渲染页面并返回完整 HTML.

    活动页对纯 HTTP 客户端返回 EO 反爬挑战页 (无 __NEXT_DATA__);
    这里让真实浏览器引擎正常执行挑战 JS 并等它自动重载出真实页面,
    不伪造任何令牌. 显式等待 #__NEXT_DATA__ 出现, 避免快照落在挑战页上.
    with_cookies=True 时恢复 cookies.json 登录态 (SSR isLogined 旁证用).
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=USER_AGENT)
            if with_cookies and COOKIES_PATH.exists():
                for c in load_json(COOKIES_PATH) or []:
                    item = {"name": c.get("name", ""), "value": c.get("value", ""),
                            "domain": c.get("domain", ""), "path": c.get("path", "/")}
                    for k in ("expires", "httpOnly", "secure"):
                        if c.get(k) is not None:
                            item[k] = c[k]
                    if c.get("sameSite") in ("Strict", "Lax", "None"):
                        item["sameSite"] = c["sameSite"]
                    with contextlib.suppress(Exception):
                        context.add_cookies([item])
            page = context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
            except Exception:
                try:  # 挑战页偶尔卡住: 主动刷新一次再等
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_selector("#__NEXT_DATA__", timeout=20000)
                except Exception:
                    pass  # 仍失败则取当前内容, 由上层解析器给出明确报错
            page.wait_for_timeout(settle_ms)
            return page.content()
        finally:
            browser.close()


def extract_next_data(html):
    """从活动页 HTML 抠出 __NEXT_DATA__ JSON 对象; 失败返回 None."""
    m = re.search(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S
    )
    if not m:
        return None
    with contextlib.suppress(Exception):
        return json.loads(m.group(1))
    return None


def iter_flash_entries(activity_data):
    """遍历所有秒杀场次条目 (跨日期), 产出 (date, entry)."""
    for state_key in ("twoDaySeckillState", "daySeckillState"):
        state = (activity_data or {}).get(state_key) or {}
        flash = state.get("flashResult") if isinstance(state.get("flashResult"), dict) else None
        if flash:
            for date, entries in flash.items():
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict):
                            yield date, entry
        lst = state.get("list")
        if isinstance(lst, list):
            for entry in lst:
                if isinstance(entry, dict):
                    yield str(entry.get("start_date", "")), entry


def parse_sale(entry, sku_type):
    """在单个场次条目里找匹配 SKU 的 sale; 找不到返回 None.

    只认 typeMap 命中目标 SKU 的条目, 避免误抓同 act_id 的其它套餐.
    """
    for sale in entry.get("sales") or []:
        data_map = (sale.get("typeMap") or {}).get("data") or {}
        types = set()
        for area_entries in data_map.values():
            if isinstance(area_entries, list):
                for t in area_entries:
                    if isinstance(t, dict):
                        types.add(str(t.get("value")))
        if sku_type in types:
            return sale
    return None


def parse_session_entry(entry):
    """场次条目 -> {start_ms, end_ms, status, date, start_time, end_time}."""
    def _ms(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    start_ms = _ms(entry.get("startTime"))
    end_ms = _ms(entry.get("endTime"))
    if start_ms is None and entry.get("start_date") and entry.get("start_time"):
        with contextlib.suppress(Exception):
            start_ms = bj_to_ms(parse_bj(f"{entry['start_date']} {entry['start_time']}"))
    if end_ms is None and entry.get("end_date") and entry.get("end_time"):
        with contextlib.suppress(Exception):
            end_ms = bj_to_ms(parse_bj(f"{entry['end_date']} {entry['end_time']}"))
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "dynamic_status": entry.get("dynamic_status"),
        "dynamic_status_cn": entry.get("dynamic_status_cn", ""),
        "date": entry.get("start_date", ""),
        "start_time": entry.get("start_time", ""),
        "end_time": entry.get("end_time", ""),
    }


def parse_activity(html, target):
    """解析活动页, 返回解析结果 dict (不做通过/失败判断, 判断在 verify_target).

    返回:
    {
      "ok": bool,           # 页面结构解析成功与否
      "error": str|None,
      "ename": str, "activity_id": int,
      "activity_window": {"start_ms","end_ms"},
      "page_now_ms": int|None,       # 页面 SSR 服务器时间 (可作对时样本)
      "sessions": [ {start_ms,end_ms,dynamic_status,...,sale_found:bool} ],
      "sale": {...匹配商品的完整下单要素...} | None,
    }
    """
    nd = extract_next_data(html)
    if not nd:
        return {"ok": False, "error": "页面无 __NEXT_DATA__ (活动页打不开/结构变化)", "sessions": []}
    act = ((nd.get("props") or {}).get("pageProps") or {}).get("activityData") or {}
    activity_id = act.get("activityId")
    if activity_id is None:
        return {"ok": False, "error": "页面无 activityId (不是活动页?)", "sessions": []}

    detail = act.get("activityDetail") or {}
    sessions = []
    matched_sales = []  # 同一商品出现在多场: 全部收集, 价格逐场核对
    matched_session = None
    for date, entry in iter_flash_entries(act):
        sess = parse_session_entry(entry)
        sale = parse_sale(entry, target.get("sku_type", ""))
        sess["sale_found"] = sale is not None
        sessions.append(sess)
        if sale is not None:
            matched_sales.append(sale)
            if matched_session is None:
                matched_session = sess

    result = {
        "ok": True,
        "error": None,
        "ename": act.get("ename", ""),
        "activity_id": activity_id,
        "activity_window": {"start_ms": detail.get("startTime"), "end_ms": detail.get("endTime")},
        "page_now_ms": act.get("nowTime"),
        "is_logined": act.get("isLogined"),
        "sessions": sessions,
        # 所有场次该商品的去重实付价(分): 任一场超红线都算不安全 (价格红线保守原则)
        "price_fen_all": sorted(
            {
                (s.get("priceData") or {}).get("real_cost")
                for s in matched_sales
                if isinstance((s.get("priceData") or {}).get("real_cost"), (int, float))
            }
        ),
    }
    matched_sale = matched_sales[0] if matched_sales else None
    if matched_sale:
        price = matched_sale.get("priceData") or {}
        tm_data = (matched_sale.get("typeMap") or {}).get("data") or {}
        rm_data = (matched_sale.get("regionMap") or {}).get("data") or {}

        def _first_area(data_map, prefer="1"):
            if not isinstance(data_map, dict):
                return []
            if isinstance(data_map.get(prefer), list):
                return data_map[prefer]
            for v in data_map.values():
                if isinstance(v, list):
                    return v
            return []

        type_map = _first_area(tm_data)
        sku_info = next((t for t in type_map if isinstance(t, dict) and t.get("value") == target.get("sku_type")), {})
        region_map = _first_area(rm_data)
        goods = matched_sale.get("$goods") or {}
        result["sale"] = {
            "act_id": matched_sale.get("act_id"),
            "product_name": matched_sale.get("product_name"),
            "dict_slug": ((matched_sale.get("dictItem") or {}).get("Slug")),
            "goods_name": (goods.get("name") or matched_sale.get("goods_name") or ""),
            "sku_type": target.get("sku_type"),
            "sku_value_map": sku_info.get("valueMap") or {},
            "sku_label": sku_info.get("label", ""),
            "default_goods_param": matched_sale.get("defaultGoodsParam") or {},
            "region_map": region_map,  # [{label,regionEn,value}]
            "price_fen": price.get("real_cost"),  # 实付总价(分)
            "list_price_fen": price.get("total_cost"),
            "amount_unit": price.get("amount_unit"),
            "currency": price.get("currency"),
            "policy": price.get("policy"),
            "user_num": matched_sale.get("user_num"),
            "max_num": matched_sale.get("maxNum"),
            "time_span_options": [o.get("value") for o in matched_sale.get("timeSpanUnitOptions") or []],
            "area_options": [o.get("value") for o in matched_sale.get("areaOptions") or []],
            "attach": matched_sale.get("seckill_act_extra") or {},
            "user_condition": goods.get("user_condition") or {},
        }
        result["matched_session"] = matched_session
    else:
        result["sale"] = None
    return result


# ---------------- 规格与价格校验 (价格红线最高优先级) ----------------
class CheckItem:
    """单项检查结果: status ∈ pass/fail/pending(待确认)."""

    def __init__(self, name, status, detail=""):
        self.name = name
        self.status = status
        self.detail = detail

    def __repr__(self):
        tag = {"pass": "✅", "fail": "❌", "pending": "⏳"}.get(self.status, " ? ")
        return f"{tag} {self.name}: {self.detail}"


class CheckReport:
    """检查报告集合: fail 项一票否决, pending 项需人工确认."""

    def __init__(self):
        self.items = []

    def add(self, name, status, detail=""):
        self.items.append(CheckItem(name, status, detail))
        return self

    @property
    def failed(self):
        return [i for i in self.items if i.status == "fail"]

    @property
    def pending(self):
        return [i for i in self.items if i.status == "pending"]

    @property
    def all_pass(self):
        return not self.failed

    def summary(self):
        return f"通过 {sum(1 for i in self.items if i.status=='pass')} / " \
               f"失败 {len(self.failed)} / 待确认 {len(self.pending)}"

    def dump(self):
        for item in self.items:
            print(item)


def _fen_to_yuan(fen):
    try:
        return int(fen) / 100.0
    except (TypeError, ValueError):
        return None


def verify_target(parsed, target):
    """对解析结果做规格+价格校验, 返回 CheckReport.

    价格规则 (最高优先级):
      - real_cost(分) 解析成功且 <= max_price_yuan*100 才 pass;
      - 解析不出来 => pending(模拟可继续, 真实下单一律拒绝);
      - 超价 => fail (任何模式都拒绝下单).
    """
    rpt = CheckReport()
    if not parsed.get("ok"):
        rpt.add("活动页解析", "fail", parsed.get("error", "未知错误"))
        return rpt

    rpt.add("活动页可达", "pass", f"ename={parsed.get('ename')} activity_id={parsed.get('activity_id')}")
    sale = parsed.get("sale")
    if not sale:
        rpt.add("目标商品", "fail", f"页面所有场次均未找到 SKU={target.get('sku_type')}")
        return rpt

    # 1) 商品类型: 必须是轻量应用服务器本体 (Slug=lighthouse), 排除 Lighthouse Agent
    slug = sale.get("dict_slug") or ""
    if slug == target.get("product_slug"):
        rpt.add("商品类型", "pass", f"dictItem.Slug={slug} (轻量应用服务器本体)")
    else:
        rpt.add("商品类型", "fail", f"dictItem.Slug={slug!r} != {target.get('product_slug')!r} (疑似非轻量本体, 如 Lighthouse Agent)")

    # 2) 商品名关键词 (4核4G / 300GB)
    name = sale.get("goods_name", "")
    attach = sale.get("attach") or {}
    name_text = name + json.dumps(attach, ensure_ascii=False)
    missing_kw = [kw for kw in target.get("name_keywords", []) if kw not in name_text]
    if not missing_kw:
        rpt.add("商品名关键词", "pass", f"{target.get('name_keywords')} 均命中: {name[:60]}")
    else:
        rpt.add("商品名关键词", "fail", f"缺少关键词 {missing_kw}: {name[:60]}")

    # 3) 规格 valueMap 逐项核对 (cpu/ram/带宽/流量/系统盘)
    vm = sale.get("sku_value_map") or {}
    spec = target.get("spec", {})
    spec_bad = []
    for key, want in spec.items():
        got = str(vm.get(key, ""))
        if want not in got:  # systemDisk 用包含匹配 ("40GB SSD盘" 含 "40GB SSD")
            spec_bad.append(f"{key}: 期望含 {want!r}, 实际 {got!r}")
    if spec_bad:
        rpt.add("规格核对(4核4G/3M/40G SSD/300G)", "fail", "; ".join(spec_bad))
    else:
        rpt.add("规格核对(4核4G/3M/40G SSD/300G)", "pass", f"valueMap={vm}")

    # 4) 时长 12 个月 + 数量 1
    spans = sale.get("time_span_options") or []
    if target.get("time_span_unit") in spans:
        rpt.add("购买时长", "pass", f"页面支持 {spans}, 目标 {target.get('time_span_unit')}")
    else:
        rpt.add("购买时长", "fail", f"页面时长选项 {spans} 不含 {target.get('time_span_unit')}")
    try:
        max_num = int(sale.get("max_num"))
    except (TypeError, ValueError):
        max_num = None
    if max_num is not None and int(target.get("goods_num", 1)) <= max_num:
        rpt.add("购买数量", "pass", f"单笔上限 {max_num}, 目标 {target.get('goods_num')} 台")
    else:
        rpt.add("购买数量", "fail", f"页面单笔上限 {sale.get('max_num')}, 目标 {target.get('goods_num')}")

    # 5) 地域: 中国大陆 + 优先地域可用性
    areas = sale.get("area_options") or []
    if target.get("area") in areas:
        rpt.add("中国大陆地域", "pass", f"areaOptions={areas}")
    else:
        rpt.add("中国大陆地域", "fail", f"页面地域分区 {areas} 不含 area={target.get('area')}")
    region_map = sale.get("region_map") or []
    page_regions = {r.get("value"): r.get("label") for r in region_map if isinstance(r, dict)}
    want_regions = target.get("region_priority", [])
    usable = [r for r in want_regions if r in page_regions]
    drop = [f"{r}({page_regions.get(r, '页面未提供')})" for r in want_regions if r not in page_regions]
    if usable:
        detail = "可用地域: " + ", ".join(f"{page_regions[r]}(id={r})" for r in usable)
        if drop:
            detail += f"; 页面没有将跳过: {drop}"
        rpt.add("地域优先级", "pass", detail)
    else:
        rpt.add("地域优先级", "fail", f"页面可售地域 {page_regions} 与优先级 {want_regions} 无交集")

    # 6) 价格红线 (最高优先级): 核对该商品在页面全部场次的价格, 任一场超线即 fail
    unit = sale.get("amount_unit")
    max_yuan = float(target.get("max_price_yuan", 0))
    fens = parsed.get("price_fen_all")
    if not fens and isinstance(sale.get("price_fen"), (int, float)):
        fens = [sale["price_fen"]]
    fens = fens or []

    def _to_yuan(fen):
        if unit == "pent":
            return _fen_to_yuan(fen)
        return fen if fen <= 1000 else fen / 100.0  # 无单位标注时按分→元, 千以上视为元

    if not fens or any(f is None for f in fens):
        rpt.add(
            "价格红线(<=%.0f元)" % max_yuan,
            "pending",
            f"页面未给出可解析价格 (real_cost={sale.get('price_fen')}, unit={unit}); "
            "模拟可继续, 真实下单必须先人工确认",
        )
    else:
        yuans = [_to_yuan(f) for f in fens]
        if all(y <= max_yuan + 1e-9 for y in yuans):
            detail_price = "/".join(f"{y:.2f}" for y in yuans) + " 元/年"
            rpt.add(
                "价格红线(<=%.0f元)" % max_yuan,
                "pass",
                f"全部场次实付 {detail_price} (real_cost={fens}分, 原价 {sale.get('list_price_fen')}分, 折扣 {sale.get('policy')})",
            )
        else:
            bad = [f"{y:.2f}" for y in yuans if y > max_yuan + 1e-9]
            rpt.add(
                "价格红线(<=%.0f元)" % max_yuan,
                "fail",
                f"存在场次实付 {'/'.join(bad)} 元 > 上限 {max_yuan} 元 (全部场次价 {fens}分), 一律拒绝下单",
            )

    # 7) 公网 IPv4: 页面数据不含该字段, 无法自动核实 => 待确认
    rpt.add("独立公网 IPv4", "pending", "页面数据未标注, 请在官方商品详情页人工确认轻量含独立公网 IP")

    # 8) 购买资格 (实名/新用户限制)
    cond = sale.get("user_condition") or {}
    cond_desc = ", ".join(f"{k}={v}" for k, v in cond.items() if v not in (0, None))
    rpt.add(
        "购买资格",
        "pending",
        f"页面要求: {cond_desc or '未知'}; 以 check-available / 下单接口实际返回为准",
    )
    return rpt


def pick_session(parsed, settings, now_ms=None):
    """选下一场: 手动指定优先, 否则自动取含目标商品且未结束的最早场.

    返回 (session_dict|None, note).
    """
    now_ms = bj_now_ms() if now_ms is None else now_ms
    manual = (settings.get("seckill") or {}).get("manual_datetime") or ""
    sessions = [s for s in parsed.get("sessions", []) if s.get("sale_found") and s.get("start_ms")]
    if manual:
        try:
            ms = bj_to_ms(parse_bj(manual))
        except ValueError as e:
            return None, f"手动秒杀时间格式错误: {e}"
        return {"start_ms": ms, "end_ms": None, "date": manual[:10], "start_time": manual[11:],
                "dynamic_status": None, "dynamic_status_cn": "手动指定"}, f"手动指定场: {manual}"
    if not sessions:
        return None, "页面场次数据中没有目标商品"
    future = [s for s in sessions if s["end_ms"] and s["end_ms"] > now_ms]
    if future:
        s = min(future, key=lambda x: x["start_ms"])
        return s, f"自动选中: {s['date']} {s['start_time']} 场 ({s['dynamic_status_cn']})"
    # 所有场已结束但有未开始标注异常的场: 退而取最晚开始且 start 在未来的
    later = [s for s in sessions if s["start_ms"] > now_ms]
    if later:
        s = min(later, key=lambda x: x["start_ms"])
        return s, f"自动选中(按开始时间): {s['date']} {s['start_time']}"
    return None, "所有场次均已结束"


# ---------------- 服务器对时 ----------------
class ServerClock:
    """提前一次性校准钟差 (Date 响应头, UTC aware 解析), 之后零网络."""

    def __init__(self, samples=5):
        self.samples = samples
        self.skew_ms = 0
        self.ok = False
        self.last_error = None

    def sync(self, url, timeout=5):
        """取中位数抗抖动. Date 头只有秒精度: +500ms 对中, RTT 取半计入."""
        from datetime import timezone as _tz

        skews = []
        last_err = None
        for _ in range(self.samples):
            try:
                t0 = time.time()
                resp = requests.head(url, timeout=timeout, allow_redirects=False)
                t1 = time.time()
                date_hdr = resp.headers.get("Date")
                if not date_hdr:
                    continue
                dt = datetime.strptime(date_hdr, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=_tz.utc)
                server_ms = dt.timestamp() * 1000 + 500 + (t1 - t0) * 1000 / 2
                skews.append(server_ms - t1 * 1000)
            except Exception as e:
                last_err = e
        if skews:
            skews.sort()
            self.skew_ms = int(skews[len(skews) // 2])
            self.ok = True
            return True, f"钟差已校准 {self.skew_ms}ms ({len(skews)}/{self.samples} 样本中位数)"
        self.last_error = last_err
        return False, f"钟差校准失败 ({last_err}), 用本地钟 (skew=0)"

    def now_ms(self):
        return int(time.time() * 1000) + self.skew_ms
