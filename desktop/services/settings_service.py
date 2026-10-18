"""系统设置业务层 —— PRD 5.9 / 20 章

★ key 白名单 + needs_restart 标记：改了要重启才生效的项必须在响应里显式告知，
  禁止出现「改了没反应」的不明状态（自用系统最容易让人误判的地方）。
"""
from __future__ import annotations

from core.errors import BizError
from models import database, settings_repo
from services import validators

# key: (value_type, needs_restart, min, max)
KEY_SCHEMA: dict[str, tuple[str, bool, int | None, int | None]] = {
    "server_name": ("string", False, None, None),
    "broadcast_interval_ms": ("int", False, 1000, 10000),
    "broadcast_enabled": ("bool", False, None, None),
    "backup_enabled": ("bool", False, None, None),
    "backup_time": ("string", False, None, None),
    "backup_keep": ("int", False, 1, 60),
    "low_stock_alert": ("bool", False, None, None),
    "page_size": ("int", False, 20, 100),
    "report_default_days": ("int", False, 1, 366),
    "udp_target": ("string", False, None, None),
    "label_size": ("string", False, None, None),
    "http_port": ("int", True, 1024, 65535),
    "wms_bills_enabled": ("bool", False, None, None),
    "wms_operation_approve_required": ("bool", False, None, None),
    # 单据打印（P0-3）：抬头信息与默认纸张
    "company_name": ("string", False, None, None),
    "company_phone": ("string", False, None, None),
    "company_address": ("string", False, None, None),
    "print_paper": ("string", False, None, None),
}


#: 设置分组：(组 id, 中文标题)。顺序即界面顺序。
SETTING_GROUPS: tuple[tuple[str, str], ...] = (
    ("device", "设备与配对"),
    ("ops", "业务与提醒"),
    ("print", "单据打印"),
    ("data", "数据与备份"),
    ("network", "网络与安全"),
    ("other", "其它"),
)

#: 展示元数据：(组, 中文标签, 提示)。★放服务端而不是前端：
#: 两端各写一套标签，改一处漏一处就是「界面上写着 A、实际改的是 B」。
SETTING_META: dict[str, tuple[str, str, str]] = {
    "server_name": ("device", "设备名称", "手机 APP 里显示的本机名字，只影响显示"),
    "broadcast_interval_ms": ("device", "自动发现广播间隔（毫秒）", "1000~10000；调大更省电，调小手机发现更快"),
    "udp_target": ("device", "广播目标", "auto = 按网卡自动推算；只有跨网段才需要手填地址"),
    "low_stock_alert": ("ops", "低库存提醒", "库存低于预警线时在首页提示"),
    "page_size": ("ops", "列表每页条数", "20~100；仓库电脑屏幕小就调小一点"),
    "report_default_days": ("ops", "报表默认统计天数", "打开报表时默认看最近多少天"),
    "label_size": ("ops", "标签纸尺寸", "条码打印用的标签纸，单位毫米"),
    "wms_bills_enabled": ("ops", "启用 WMS 单据",
                          "关闭后采购/领用等单据不能再新建或过账，既有的出入库流水与库存不受影响"),
    "wms_operation_approve_required": ("ops", "操作单需审批",
                                       "开启：领用/退库/归还/借用/调拨须 admin 审批后才扣减库存；关闭：提交即过账（采购入库始终需审批）"),
    "company_name": ("print", "单据抬头公司名", "打印采购单/入库单/领用单时印在标题上方；留空则用设备名称"),
    "company_phone": ("print", "联系电话", "留空则打印页不显示电话"),
    "company_address": ("print", "地址", "留空则打印页不显示地址"),
    "print_paper": ("print", "默认打印纸张", "a4 = 普通打印机；dot = 针式连续纸 241×140mm（单据详情页可临时切换）"),
    "backup_enabled": ("data", "每日自动备份", "关闭后只能手动备份，建议保持开启"),
    "backup_time": ("data", "自动备份时间", "24 小时制 HH:MM，建议放在下班之后"),
    "backup_keep": ("data", "备份保留份数", "1~60；超出后自动删除最旧的备份"),
    "http_port": ("network", "服务端口", "重启后生效；改动后手机需要用新端口重新配对"),
    "broadcast_enabled": ("network", "局域网自动发现", "关闭后手机不会自动列出本机，仍可用二维码扫码配对"),
}

#: 枚举型配置的可选值：前端渲染成下拉，避免手输拼错（如 "60×40" 写成全角乘号）
SETTING_OPTIONS: dict[str, tuple[str, ...]] = {
    "label_size": ("40x30", "50x30", "60x40"),
    "udp_target": ("auto",),
    "print_paper": ("a4", "dot"),
}


def get_all_view() -> dict:
    conn = database.get_connection()
    items = []
    for row in settings_repo.get_all(conn):
        vtype, needs_restart, _, _ = KEY_SCHEMA.get(row["key"], ("string", False, None, None))
        group, label, hint = SETTING_META.get(row["key"], ("other", row["key"], ""))
        item = {"key": row["key"], "value": settings_repo.get(conn, row["key"]),
                "raw_value": row["value"], "value_type": vtype,
                "needs_restart": needs_restart, "updated_at": row["updated_at"],
                "group": group, "label": label, "hint": hint}
        options = SETTING_OPTIONS.get(row["key"])
        if options:
            item["options"] = list(options)   # 仅枚举项带 options，其余前端按 value_type 渲染
        items.append(item)
    return {"items": items,
            "groups": [{"id": gid, "title": title} for gid, title in SETTING_GROUPS]}


def update_many(payload: dict, operator: dict) -> dict:
    items = (payload or {}).get("items")
    if isinstance(items, dict):
        pairs = items
    elif isinstance(items, list):
        pairs = {x["key"]: x.get("value") for x in items if isinstance(x, dict) and "key" in x}
    else:
        raise BizError(1002, "参数校验失败", {"errors": {"items": ["items 必须是对象或 {key,value} 数组"]}})
    errors: dict[str, list[str]] = {}
    clean: dict = {}
    for key, value in pairs.items():
        spec = KEY_SCHEMA.get(key)
        if spec is None:
            errors.setdefault(key, []).append("未知配置项")
            continue
        vtype, needs_restart, lo, hi = spec
        try:
            clean[key] = _coerce(value, vtype, lo, hi, key)
        except BizError as e:
            for k, v in (e.data or {}).get("errors", {}).items():
                errors.setdefault(k, []).extend(v)
    if errors:
        raise BizError(1002, "参数校验失败", {"errors": errors})
    conn = database.get_connection()
    written = settings_repo.set_many(conn, clean, operator.get("id"))
    return {"updated": written,
            "needs_restart": [k for k in written if KEY_SCHEMA.get(k, ("", False))[1]],
            "items": get_all_view()["items"]}


def _coerce(value, vtype: str, lo, hi, key: str):
    if vtype == "int":
        n = validators.require_int(value, key, minimum=lo if lo is not None else 0,
                                   maximum=hi if hi is not None else 10 ** 9)
        return n
    if vtype == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise BizError(1002, "参数校验失败", {"errors": {key: ["必须是布尔值"]}})
    if vtype == "json":
        import json as _json
        if isinstance(value, str):
            try:
                return _json.loads(value)
            except ValueError:
                raise BizError(1002, "参数校验失败", {"errors": {key: ["必须是合法 JSON"]}})
        return value
    text = validators.require_str(value, key, max_len=60)
    if key == "backup_time":
        import re
        if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", text or ""):
            raise BizError(1002, "参数校验失败", {"errors": {key: ["格式必须是 HH:MM"]}})
    return text


def typed(key: str, default=None):
    conn = database.get_connection()
    return settings_repo.get(conn, key, default)
