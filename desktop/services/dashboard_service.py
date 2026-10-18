"""仪表盘聚合 —— PRD 5.2 · 陷阱 T-22

4 个聚合查询各自走索引；单个查询失败 → 该项回落 None + errors 收集，页面其余照常渲染（不整页 500）。
★ 库存合计**含负数原样返回**，禁止 MAX(0,...) 掩盖；同时给 anomaly_count 供红字告警。
"""
from __future__ import annotations

from models import database, product_repo
from services import timeutil


def summary() -> dict:
    conn = database.get_connection()
    today = timeutil.today_prefix()
    out: dict = {"errors": [], "generated_at": timeutil.now_iso()}
    # 字段名必须与 docs/03 6.4 一致（安卓 data class 直接映射，改名即崩）
    steps = {
        "product_count": lambda: int(conn.execute(
            "SELECT COUNT(*) FROM products WHERE is_active = 1").fetchone()[0]),
        "total_qty": lambda: int(conn.execute(
            "SELECT COALESCE(SUM(qty),0) FROM stock_current").fetchone()[0]),
        "today": _today_counts,
        "low_stock_list": lambda: _low_stock(conn)["list"],
        "low_stock_total": lambda: _low_stock(conn)["total"],
        "recent_logs": lambda: _recent(conn),
        "last_backup_at": lambda: __import__("services.backup_service", fromlist=["x"]).status()["last_ok_at"],
    }
    for key, fn in steps.items():
        try:
            out[key] = fn()
        except Exception as exc:  # 局部失败不整页崩（PRD 5.2 异常表）
            out[key] = None
            out["errors"].append(f"{key}: {exc}")
    try:
        out["stock_anomaly_count"] = len(_negative(conn))  # docs/03 6.4 字段名
    except Exception:
        out["stock_anomaly_count"] = None
    out["server"] = server_status()
    return out


def server_status() -> dict:
    """服务状态区数据（PRD 5.2）：来自实际绑定结果与广播线程状态，不是配置值。

    ★公开给 `api/system.py` 复用：仪表盘与「系统信息」卡必须给出**同一个**广播状态，
      各算一套的话，一边显示已开启、另一边显示已关闭，运维就无法判断该信谁。
    """
    from flask import current_app
    cfg = current_app.config
    broadcaster = cfg.get("BROADCASTER")
    active = bool(cfg.get("BROADCAST_ACTIVE")) and (
        bool(broadcaster.status().get("active")) if broadcaster is not None else bool(cfg.get("TESTING")))
    return {"ip": cfg.get("BOUND_IP") or "127.0.0.1",
            "port": int(cfg.get("BOUND_PORT") or cfg.get("HTTP_PORT", 5000)),
            "name": cfg.get("DISPLAY_NAME", "ScanableSelf"),
            "version": cfg.get("APP_VERSION", "0.0.0"),
            "server_time": timeutil.now_iso(),
            "broadcast_active": active}


def _today_counts() -> dict:
    conn = database.get_connection()
    day = timeutil.today_prefix()
    row = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN type='IN' THEN quantity ELSE 0 END),0)  AS in_qty,
             COALESCE(SUM(CASE WHEN type='OUT' THEN quantity ELSE 0 END),0) AS out_qty,
             COUNT(CASE WHEN type='IN' THEN 1 END)  AS in_count,
             COUNT(CASE WHEN type='OUT' THEN 1 END) AS out_count
           FROM stock_logs WHERE substr(created_at,1,10) = ?""", (day,)).fetchone()
    return {k: int(v) for k, v in dict(row).items()}


def _low_stock(conn, limit: int = 50) -> dict:
    rows = conn.execute(
        """SELECT p.id, p.name, p.barcode, p.min_stock, w.name AS warehouse_name, sc.qty
             FROM stock_current sc
             JOIN products p ON p.id = sc.product_id
             JOIN warehouses w ON w.id = sc.warehouse_id
            WHERE p.is_active = 1 AND sc.qty < p.min_stock
            ORDER BY sc.qty LIMIT ?""", (limit,)).fetchall()
    total = conn.execute(
        """SELECT COUNT(*) FROM stock_current sc JOIN products p ON p.id = sc.product_id
            WHERE p.is_active = 1 AND sc.qty < p.min_stock""").fetchone()[0]
    return {"list": [dict(r) for r in rows], "total": int(total)}


def _recent(conn, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """SELECT l.id, l.type, l.quantity, l.product_name_snapshot, l.created_at, l.source, u.username
             FROM stock_logs l LEFT JOIN users u ON u.id = l.operator_id
            ORDER BY l.created_at DESC, l.id DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _negative(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT product_id, warehouse_id, qty FROM stock_current WHERE qty < 0").fetchall()]
