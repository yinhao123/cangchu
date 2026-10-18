"""settings key-value 读写 —— PRD 5.9 / 20 章

value_type ∈ string|int|bool|json：写入时校验并规范化，读取时按类型转换。
禁止在这里做业务判断（谁能改、改了要不要重启，都在 services/settings_service.py）。
"""
from __future__ import annotations

import json

from services import timeutil

DEFAULTS: dict[str, tuple[str, str]] = {
    "server_name": ("ScanableSelf", "string"),
    "broadcast_interval_ms": ("3000", "int"),
    "broadcast_enabled": ("true", "bool"),
    "backup_enabled": ("true", "bool"),
    "backup_time": ("03:00", "string"),
    "backup_keep": ("7", "int"),
    "low_stock_alert": ("true", "bool"),
    "page_size": ("20", "int"),
    "report_default_days": ("30", "int"),
    "udp_target": ("auto", "string"),
    "label_size": ("40x30", "string"),
    "http_port": ("5000", "int"),
    # ---- WMS 单据层（docs/13）----
    # 总开关：关闭后单据的写操作全部拒绝，既有流水/库存完全不受影响（docs/13 第 8.2 节回滚方案）
    "wms_bills_enabled": ("true", "bool"),
    # 领用/退库/归还是否需要 admin 审批后才过账（采购入库始终强制审批）
    "wms_operation_approve_required": ("true", "bool"),
    # ---- 单据打印（P0-3，docs/14 第 5.4 节）----
    # 抬头公司名留空时回退 server_name；联系信息为空则打印页不显示该行
    "company_name": ("", "string"),
    "company_phone": ("", "string"),
    "company_address": ("", "string"),
    # 纸张：a4（激光/喷墨）或 dot（针式连续纸 241×140mm），详情页可临时切换
    "print_paper": ("a4", "string"),
}


def get(conn, key: str, default=None):
    row = conn.execute("SELECT value, value_type FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        if key in DEFAULTS:
            return _cast(DEFAULTS[key][0], DEFAULTS[key][1])
        return default
    return _cast(row["value"], row["value_type"])


def _cast(value: str, value_type: str):
    if value_type == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    if value_type == "bool":
        return str(value).lower() == "true"
    if value_type == "json":
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def get_all(conn) -> list[dict]:
    rows = conn.execute("SELECT key, value, value_type, updated_at, updated_by FROM settings ORDER BY key").fetchall()
    have = {r["key"] for r in rows}
    out = [dict(r) for r in rows]
    for key, (value, vtype) in DEFAULTS.items():  # 未落库的默认项也要在页面上可见可改
        if key not in have:
            out.append({"key": key, "value": value, "value_type": vtype, "updated_at": None,
                        "updated_by": None, "is_default": 1})
    return sorted(out, key=lambda x: x["key"])


def set_many(conn, items: dict, updated_by: int | None = None) -> list[str]:
    """逐个 upsert；返回实际写入的 key 列表。类型转换失败由调用方（settings_service）提前挡下。"""
    now = timeutil.now_iso()
    written = []
    for key, value in items.items():
        vtype = _type_of(key)
        stored = _serialize(value, vtype)
        conn.execute(
            """INSERT INTO settings(key, value, value_type, updated_at, updated_by)
               VALUES(?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, value_type=excluded.value_type,
                                              updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
            (key, stored, vtype, now, updated_by))
        written.append(key)
    return written


def _type_of(key: str) -> str:
    return DEFAULTS.get(key, ("", "string"))[1]


def _serialize(value, vtype: str) -> str:
    if vtype == "bool":
        return "true" if value in (True, 1, "1", "true", "True") else "false"
    if vtype == "json":
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def seed_defaults(conn) -> int:
    """幂等灌默认配置（已存在的不覆盖）。"""
    now = timeutil.now_iso()
    n = 0
    for key, (value, vtype) in DEFAULTS.items():
        cur = conn.execute(
            "INSERT OR IGNORE INTO settings(key, value, value_type, updated_at) VALUES(?,?,?,?)",
            (key, value, vtype, now))
        n += cur.rowcount
    return n
