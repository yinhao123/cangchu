"""领用 / 退库 / 归还 / 借用 / 调拨单 数据访问 —— docs/13 第 4.4 节

★ 本层只写 SQL；状态流转、过账方向、库存扣减都在 services/operation_service.py。
★ 明细「整单替换」：未过账的单据允许随意改行；已过账禁止改（service 拦）。
"""
from __future__ import annotations

from services import timeutil

_ORDER_COLS = """o.id, o.code, o.name, o.op_type, o.order_id, o.in_warehousing_id, o.warehouse_id,
                 o.to_warehouse_id, o.operator_user_id, o.partner_id, o.op_date,
                 o.expect_return_date, o.total_qty, o.total_amount, o.status, o.posted_at,
                 o.posted_by, o.submit_at, o.approve_at, o.approver_id, o.reject_reason,
                 o.reversed_at, o.reversed_by, o.remark, o.is_active, o.created_at, o.updated_at,
                 w.name AS warehouse_name, t.name AS to_warehouse_name,
                 u.display_name AS operator_name, u.username AS operator_username"""

_DETAIL_COLS = """d.id, d.order_id, d.product_id, d.product_name_snap, d.barcode_snap, d.unit_snap,
                  d.qty, d.price, d.amount, d.serial_number, d.is_join_asset, d.sort_order,
                  d.remark, d.created_at, d.updated_at"""

OP_TYPES = ("RECEIVE", "RETURN_STOCK", "RETURN_BORROW", "BORROW", "TRANSFER")


def insert_order(conn, fields: dict) -> int:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO wms_goods_operation_order(code, name, op_type, order_id, in_warehousing_id,
                                                 warehouse_id, to_warehouse_id, operator_user_id,
                                                 partner_id, op_date, expect_return_date,
                                                 total_qty, total_amount, status, remark,
                                                 is_active, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,1,?,?)""",
        (fields["code"], fields["name"], fields["op_type"], fields.get("order_id"),
         fields.get("in_warehousing_id"), fields["warehouse_id"], fields.get("to_warehouse_id"),
         fields["operator_user_id"], fields.get("partner_id"), fields.get("op_date") or now,
         fields.get("expect_return_date"), int(fields.get("total_qty") or 0),
         float(fields.get("total_amount") or 0), fields.get("remark"), now, now))
    return int(cur.lastrowid)


def update_order(conn, order_id: int, fields: dict) -> int:
    sets, args = [], []
    for key in ("name", "op_type", "order_id", "in_warehousing_id", "warehouse_id",
                "to_warehouse_id", "operator_user_id", "partner_id", "op_date",
                "expect_return_date", "total_qty", "total_amount", "status", "posted_at",
                "posted_by", "submit_at", "approve_at", "approver_id", "reject_reason",
                "reversed_at", "reversed_by", "remark", "is_active"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return 0
    sets.append("updated_at = ?")
    args.append(timeutil.now_iso())
    args.append(order_id)
    return conn.execute(
        f"UPDATE wms_goods_operation_order SET {', '.join(sets)} WHERE id = ?", tuple(args)).rowcount


def get_order(conn, order_id: int) -> dict | None:
    row = conn.execute(
        f"""SELECT {_ORDER_COLS} FROM wms_goods_operation_order o
              LEFT JOIN warehouses w ON w.id = o.warehouse_id
              LEFT JOIN warehouses t ON t.id = o.to_warehouse_id
              LEFT JOIN users      u ON u.id = o.operator_user_id
             WHERE o.id = ?""", (order_id,)).fetchone()
    return dict(row) if row else None


def list_orders(conn, filters: dict) -> tuple[list[dict], int]:
    where, args = ["o.is_active = 1"], []
    if filters.get("op_type"):
        where.append("o.op_type = ?")
        args.append(str(filters["op_type"]).upper())
    if filters.get("status"):
        where.append("o.status = ?")
        args.append(str(filters["status"]).upper())
    if filters.get("warehouse_id"):
        where.append("(o.warehouse_id = ? OR o.to_warehouse_id = ?)")
        args.extend([int(filters["warehouse_id"]), int(filters["warehouse_id"])])
    if filters.get("date_from"):
        where.append("o.op_date >= ?")
        args.append(str(filters["date_from"]))
    if filters.get("date_to"):
        where.append("o.op_date <= ?")
        args.append(str(filters["date_to"]))
    if filters.get("keyword"):
        where.append("(o.code LIKE ? OR o.name LIKE ?)")
        args.extend([f"%{filters['keyword']}%", f"%{filters['keyword']}%"])
    clause = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM wms_goods_operation_order o WHERE {clause}", tuple(args)).fetchone()[0]
    page, page_size = int(filters.get("page") or 1), int(filters.get("page_size") or 20)
    rows = conn.execute(
        f"""SELECT {_ORDER_COLS} FROM wms_goods_operation_order o
              LEFT JOIN warehouses w ON w.id = o.warehouse_id
              LEFT JOIN warehouses t ON t.id = o.to_warehouse_id
              LEFT JOIN users      u ON u.id = o.operator_user_id
             WHERE {clause}
             ORDER BY o.op_date DESC, o.id DESC
             LIMIT ? OFFSET ?""", tuple(args + [page_size, (page - 1) * page_size])).fetchall()
    return [dict(r) for r in rows], int(total)


def replace_details(conn, order_id: int, lines: list[dict]) -> None:
    conn.execute("DELETE FROM wms_goods_operation_detail WHERE order_id = ?", (order_id,))
    now = timeutil.now_iso()
    for index, line in enumerate(lines):
        conn.execute(
            """INSERT INTO wms_goods_operation_detail(order_id, product_id, product_name_snap,
                                                      barcode_snap, unit_snap, qty, price, amount,
                                                      serial_number, is_join_asset, sort_order,
                                                      remark, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (order_id, line["product_id"], line["product_name_snap"], line.get("barcode_snap"),
             line["unit_snap"], int(line["qty"]), line.get("price"), line.get("amount"),
             line.get("serial_number"), int(line.get("is_join_asset") or 0), index,
             line.get("remark"), now, now))


def list_details(conn, order_id: int) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_DETAIL_COLS} FROM wms_goods_operation_detail d
             WHERE d.order_id = ? ORDER BY d.sort_order, d.id""", (order_id,)).fetchall()
    return [dict(r) for r in rows]
