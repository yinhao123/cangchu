"""采购订单 / 采购入库单 数据访问 —— docs/13 第 4.2 / 4.3 节

★ 本层只写 SQL 不判业务（状态流转、可收量校验都在 services/purchase_service.py）。
★ 明细一律「整单替换」（DELETE + INSERT），因为单据未过账前允许随意改行；
  已过账的入库单禁止改明细（由 service 拦）。
★ 过账回写 received_qty 必须走 bump_received 的条件更新，禁止先查后写（T-06 同源风险）。
"""
from __future__ import annotations

from models import database
from services import timeutil

_ORDER_COLS = """o.id, o.code, o.name, o.applicant_user_id, o.partner_id, o.partner_name_snap,
                 o.applicant_date, o.total_qty, o.total_amount, o.status, o.submit_at,
                 o.approve_at, o.approver_id, o.reject_reason, o.remark, o.is_active,
                 o.created_at, o.updated_at,
                 u.display_name AS applicant_name, u.username AS applicant_username"""

_INBOUND_COLS = """i.id, i.code, i.name, i.order_id, i.order_code_snap, i.warehouse_id, i.partner_id,
                   i.in_date, i.total_qty, i.total_amount, i.status, i.posted_at, i.posted_by,
                   i.submit_at, i.approve_at, i.approver_id, i.reject_reason, i.remark,
                   i.is_active, i.created_at, i.updated_at,
                   w.name AS warehouse_name, p.name AS partner_name"""

_DETAIL_COLS = """d.id, d.order_id, d.product_id, d.product_name_snap, d.barcode_snap, d.unit_snap,
                  d.price, d.qty, d.amount, d.received_qty, d.sort_order, d.remark,
                  d.created_at, d.updated_at"""

_IN_DETAIL_COLS = """d.id, d.inbound_id, d.order_detail_id, d.product_id, d.product_name_snap,
                     d.unit_snap, d.qty, d.price, d.amount, d.sort_order, d.remark,
                     d.created_at, d.updated_at"""


# ==================== 采购订单 ====================

def insert_order(conn, fields: dict) -> int:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO wms_purchase_order(code, name, applicant_user_id, partner_id, partner_name_snap,
                                          applicant_date, total_qty, total_amount, status, remark,
                                          is_active, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,'DRAFT',?,1,?,?)""",
        (fields["code"], fields["name"], fields["applicant_user_id"], fields.get("partner_id"),
         fields.get("partner_name_snap"), fields.get("applicant_date") or now,
         int(fields.get("total_qty") or 0), float(fields.get("total_amount") or 0),
         fields.get("remark"), now, now))
    return int(cur.lastrowid)


def update_order(conn, order_id: int, fields: dict) -> int:
    sets, args = [], []
    for key in ("name", "partner_id", "partner_name_snap", "applicant_date", "total_qty",
                "total_amount", "status", "submit_at", "approve_at", "approver_id",
                "reject_reason", "remark", "is_active"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return 0
    sets.append("updated_at = ?")
    args.append(timeutil.now_iso())
    args.append(order_id)
    return conn.execute(
        f"UPDATE wms_purchase_order SET {', '.join(sets)} WHERE id = ?", tuple(args)).rowcount


def get_order(conn, order_id: int) -> dict | None:
    row = conn.execute(
        f"""SELECT {_ORDER_COLS} FROM wms_purchase_order o
              LEFT JOIN users u ON u.id = o.applicant_user_id
             WHERE o.id = ?""", (order_id,)).fetchone()
    return dict(row) if row else None


def find_order_by_code(conn, code: str) -> dict | None:
    row = conn.execute("SELECT id FROM wms_purchase_order WHERE code = ?", (code,)).fetchone()
    return dict(row) if row else None


def _order_filters(filters: dict) -> tuple[str, list]:
    where, args = ["o.is_active = 1"], []
    if filters.get("status"):
        where.append("o.status = ?")
        args.append(str(filters["status"]).upper())
    if filters.get("partner_id"):
        where.append("o.partner_id = ?")
        args.append(int(filters["partner_id"]))
    if filters.get("date_from"):
        where.append("o.applicant_date >= ?")
        args.append(str(filters["date_from"]))
    if filters.get("date_to"):
        where.append("o.applicant_date <= ?")
        args.append(str(filters["date_to"]))
    if filters.get("keyword"):
        where.append("(o.code LIKE ? OR o.name LIKE ?)")
        args.extend([f"%{filters['keyword']}%", f"%{filters['keyword']}%"])
    return " AND ".join(where), args


def list_orders(conn, filters: dict) -> tuple[list[dict], int]:
    where, args = _order_filters(filters)
    total = conn.execute(
        f"SELECT COUNT(*) FROM wms_purchase_order o WHERE {where}", tuple(args)).fetchone()[0]
    page, page_size = int(filters.get("page") or 1), int(filters.get("page_size") or 20)
    rows = conn.execute(
        f"""SELECT {_ORDER_COLS} FROM wms_purchase_order o
              LEFT JOIN users u ON u.id = o.applicant_user_id
             WHERE {where}
             ORDER BY o.created_at DESC, o.id DESC
             LIMIT ? OFFSET ?""", tuple(args + [page_size, (page - 1) * page_size])).fetchall()
    return [dict(r) for r in rows], int(total)


def replace_order_details(conn, order_id: int, lines: list[dict]) -> None:
    conn.execute("DELETE FROM wms_purchase_order_detail WHERE order_id = ?", (order_id,))
    now = timeutil.now_iso()
    for index, line in enumerate(lines):
        conn.execute(
            """INSERT INTO wms_purchase_order_detail(order_id, product_id, product_name_snap,
                                                     barcode_snap, unit_snap, price, qty, amount,
                                                     received_qty, sort_order, remark,
                                                     created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?)""",
            (order_id, line["product_id"], line["product_name_snap"], line.get("barcode_snap"),
             line["unit_snap"], float(line.get("price") or 0), int(line["qty"]),
             float(line.get("amount") or 0), index, line.get("remark"), now, now))


def list_order_details(conn, order_id: int) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_DETAIL_COLS} FROM wms_purchase_order_detail d
             WHERE d.order_id = ? ORDER BY d.sort_order, d.id""", (order_id,)).fetchall()
    return [dict(r) for r in rows]


def bump_received(conn, detail_id: int, qty: int) -> int:
    """过账回写累计入库量：条件更新，超收时 0 行由调用方转 1016（并发双点过账也挡得住）。"""
    return conn.execute(
        """UPDATE wms_purchase_order_detail SET received_qty = received_qty + ?, updated_at = ?
            WHERE id = ? AND received_qty + ? <= qty""",
        (int(qty), timeutil.now_iso(), int(detail_id), int(qty))).rowcount


# ==================== 采购入库单 ====================

def insert_inbound(conn, fields: dict) -> int:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO wms_purchase_in_warehousing(code, name, order_id, order_code_snap, warehouse_id,
                                                  partner_id, in_date, total_qty, total_amount, status,
                                                  remark, is_active, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,'DRAFT',?,1,?,?)""",
        (fields["code"], fields["name"], fields.get("order_id"), fields.get("order_code_snap"),
         fields["warehouse_id"], fields.get("partner_id"), fields.get("in_date") or now,
         int(fields.get("total_qty") or 0), float(fields.get("total_amount") or 0),
         fields.get("remark"), now, now))
    return int(cur.lastrowid)


def update_inbound(conn, inbound_id: int, fields: dict) -> int:
    sets, args = [], []
    for key in ("name", "order_id", "order_code_snap", "warehouse_id", "partner_id", "in_date",
                "total_qty", "total_amount", "status", "posted_at", "posted_by", "submit_at",
                "approve_at", "approver_id", "reject_reason", "remark", "is_active"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return 0
    sets.append("updated_at = ?")
    args.append(timeutil.now_iso())
    args.append(inbound_id)
    return conn.execute(
        f"UPDATE wms_purchase_in_warehousing SET {', '.join(sets)} WHERE id = ?", tuple(args)).rowcount


def get_inbound(conn, inbound_id: int) -> dict | None:
    row = conn.execute(
        f"""SELECT {_INBOUND_COLS} FROM wms_purchase_in_warehousing i
              LEFT JOIN warehouses w ON w.id = i.warehouse_id
              LEFT JOIN partners   p ON p.id = i.partner_id
             WHERE i.id = ?""", (inbound_id,)).fetchone()
    return dict(row) if row else None


def list_inbounds(conn, filters: dict) -> tuple[list[dict], int]:
    where, args = ["i.is_active = 1"], []
    if filters.get("status"):
        where.append("i.status = ?")
        args.append(str(filters["status"]).upper())
    if filters.get("order_id"):
        where.append("i.order_id = ?")
        args.append(int(filters["order_id"]))
    if filters.get("warehouse_id"):
        where.append("i.warehouse_id = ?")
        args.append(int(filters["warehouse_id"]))
    if filters.get("date_from"):
        where.append("i.in_date >= ?")
        args.append(str(filters["date_from"]))
    if filters.get("date_to"):
        where.append("i.in_date <= ?")
        args.append(str(filters["date_to"]))
    if filters.get("keyword"):
        where.append("(i.code LIKE ? OR i.name LIKE ?)")
        args.extend([f"%{filters['keyword']}%", f"%{filters['keyword']}%"])
    clause = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM wms_purchase_in_warehousing i WHERE {clause}", tuple(args)).fetchone()[0]
    page, page_size = int(filters.get("page") or 1), int(filters.get("page_size") or 20)
    rows = conn.execute(
        f"""SELECT {_INBOUND_COLS} FROM wms_purchase_in_warehousing i
              LEFT JOIN warehouses w ON w.id = i.warehouse_id
              LEFT JOIN partners   p ON p.id = i.partner_id
             WHERE {clause}
             ORDER BY i.in_date DESC, i.id DESC
             LIMIT ? OFFSET ?""", tuple(args + [page_size, (page - 1) * page_size])).fetchall()
    return [dict(r) for r in rows], int(total)


def replace_inbound_details(conn, inbound_id: int, lines: list[dict]) -> None:
    conn.execute("DELETE FROM wms_purchase_in_warehousing_detail WHERE inbound_id = ?", (inbound_id,))
    now = timeutil.now_iso()
    for index, line in enumerate(lines):
        conn.execute(
            """INSERT INTO wms_purchase_in_warehousing_detail(inbound_id, order_detail_id, product_id,
                                                             product_name_snap, unit_snap, qty, price,
                                                             amount, sort_order, remark,
                                                             created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (inbound_id, line.get("order_detail_id"), line["product_id"], line["product_name_snap"],
             line["unit_snap"], int(line["qty"]), float(line.get("price") or 0),
             float(line.get("amount") or 0), index, line.get("remark"), now, now))


def list_inbound_details(conn, inbound_id: int) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_IN_DETAIL_COLS} FROM wms_purchase_in_warehousing_detail d
             WHERE d.inbound_id = ? ORDER BY d.sort_order, d.id""", (inbound_id,)).fetchall()
    return [dict(r) for r in rows]
