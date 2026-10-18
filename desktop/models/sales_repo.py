"""销售订单 / 销售退货 / 客户对账 数据访问 —— docs/14 第 6 章（P1-4）

★ 本层只写 SQL 不判业务：状态机、可发量、过账全在 services/sales_service.py。
★ 发货回写 shipped_qty 必须走 bump_shipped 的条件更新，禁止先查后写（T-06 同源风险）。
★ 对账单只读流水（stock_logs），不去 join 单据的合计字段 —— 金额必须与账本一致，
  这样退货、部分发货、红冲都不会让对账单和库存对不上。
"""
from __future__ import annotations

from services import timeutil

_ORDER_COLS = """o.id, o.code, o.name, o.applicant_user_id, o.customer_id, o.customer_name_snap,
                 o.warehouse_id, o.order_date, o.total_qty, o.total_amount, o.status,
                 o.submit_at, o.approve_at, o.approver_id, o.reject_reason, o.remark,
                 o.is_active, o.created_at, o.updated_at,
                 u.display_name AS applicant_name, u.username AS applicant_username,
                 w.name AS warehouse_name"""

_DETAIL_COLS = """d.id, d.order_id, d.product_id, d.product_name_snap, d.barcode_snap, d.unit_snap,
                  d.price, d.qty, d.amount, d.shipped_qty, d.sort_order, d.remark,
                  d.created_at, d.updated_at"""

_RETURN_COLS = """r.id, r.code, r.name, r.source_order_id, r.order_code_snap, r.customer_id,
                  r.customer_name_snap, r.warehouse_id, r.return_date, r.total_qty, r.total_amount,
                  r.status, r.posted_at, r.posted_by, r.submit_at, r.approve_at, r.approver_id,
                  r.reject_reason, r.return_reason, r.remark, r.is_active,
                  r.created_at, r.updated_at,
                  w.name AS warehouse_name, u.display_name AS applicant_name"""

_RETURN_DETAIL_COLS = """d.id, d.return_id, d.order_detail_id, d.product_id, d.product_name_snap,
                         d.barcode_snap, d.unit_snap, d.qty, d.price, d.amount, d.sort_order,
                         d.remark, d.created_at, d.updated_at"""


# ==================== 销售订单 ====================

def insert_order(conn, fields: dict) -> int:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO wms_sales_order(code, name, applicant_user_id, customer_id,
                                       customer_name_snap, warehouse_id, order_date,
                                       total_qty, total_amount, status, remark,
                                       is_active, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,'DRAFT',?,1,?,?)""",
        (fields["code"], fields["name"], fields["applicant_user_id"], fields.get("customer_id"),
         fields.get("customer_name_snap"), fields["warehouse_id"],
         fields.get("order_date") or now, int(fields.get("total_qty") or 0),
         float(fields.get("total_amount") or 0), fields.get("remark"), now, now))
    return int(cur.lastrowid)


def update_order(conn, order_id: int, fields: dict) -> int:
    sets, args = [], []
    for key in ("name", "customer_id", "customer_name_snap", "warehouse_id", "order_date",
                "total_qty", "total_amount", "status", "submit_at", "approve_at", "approver_id",
                "reject_reason", "remark", "is_active"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return 0
    sets.append("updated_at = ?")
    args.append(timeutil.now_iso())
    args.append(int(order_id))
    return conn.execute(
        f"UPDATE wms_sales_order SET {', '.join(sets)} WHERE id = ?", tuple(args)).rowcount


def get_order(conn, order_id: int) -> dict | None:
    row = conn.execute(
        f"""SELECT {_ORDER_COLS} FROM wms_sales_order o
              LEFT JOIN users      u ON u.id = o.applicant_user_id
              LEFT JOIN warehouses w ON w.id = o.warehouse_id
             WHERE o.id = ?""", (int(order_id),)).fetchone()
    return dict(row) if row else None


def list_orders(conn, filters: dict) -> tuple[list[dict], int]:
    where, args = ["o.is_active = 1"], []
    if filters.get("status"):
        where.append("o.status = ?")
        args.append(str(filters["status"]).upper())
    if filters.get("customer_id"):
        where.append("o.customer_id = ?")
        args.append(int(filters["customer_id"]))
    if filters.get("warehouse_id"):
        where.append("o.warehouse_id = ?")
        args.append(int(filters["warehouse_id"]))
    if filters.get("date_from"):
        where.append("o.order_date >= ?")
        args.append(str(filters["date_from"]))
    if filters.get("date_to"):
        where.append("o.order_date <= ?")
        args.append(str(filters["date_to"]))
    if filters.get("keyword"):
        where.append("(o.code LIKE ? OR o.name LIKE ?)")
        args.extend([f"%{filters['keyword']}%", f"%{filters['keyword']}%"])
    clause = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM wms_sales_order o WHERE {clause}", tuple(args)).fetchone()[0]
    page = max(1, int(filters.get("page") or 1))
    page_size = max(1, int(filters.get("page_size") or 20))
    rows = conn.execute(
        f"""SELECT {_ORDER_COLS} FROM wms_sales_order o
              LEFT JOIN users      u ON u.id = o.applicant_user_id
              LEFT JOIN warehouses w ON w.id = o.warehouse_id
             WHERE {clause}
             ORDER BY o.order_date DESC, o.id DESC
             LIMIT ? OFFSET ?""", tuple(args + [page_size, (page - 1) * page_size])).fetchall()
    return [dict(r) for r in rows], int(total)


def replace_order_details(conn, order_id: int, lines: list[dict]) -> None:
    conn.execute("DELETE FROM wms_sales_order_detail WHERE order_id = ?", (int(order_id),))
    now = timeutil.now_iso()
    for index, line in enumerate(lines):
        conn.execute(
            """INSERT INTO wms_sales_order_detail(order_id, product_id, product_name_snap,
                                                   barcode_snap, unit_snap, price, qty, amount,
                                                   shipped_qty, sort_order, remark,
                                                   created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?)""",
            (int(order_id), line["product_id"], line["product_name_snap"], line.get("barcode_snap"),
             line["unit_snap"], float(line.get("price") or 0), int(line["qty"]),
             float(line.get("amount") or 0), index, line.get("remark"), now, now))


def list_order_details(conn, order_id: int) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_DETAIL_COLS} FROM wms_sales_order_detail d
             WHERE d.order_id = ? ORDER BY d.sort_order, d.id""", (int(order_id),)).fetchall()
    return [dict(r) for r in rows]


def get_order_detail(conn, order_id: int, detail_id: int) -> dict | None:
    row = conn.execute(
        f"""SELECT {_DETAIL_COLS} FROM wms_sales_order_detail d
             WHERE d.order_id = ? AND d.id = ?""", (int(order_id), int(detail_id))).fetchone()
    return dict(row) if row else None


def bump_shipped(conn, detail_id: int, qty: int) -> int:
    """发货回写累计发货量：条件更新，超发时 0 行由调用方转 1016（并发双点发货也挡得住）。"""
    return conn.execute(
        """UPDATE wms_sales_order_detail SET shipped_qty = shipped_qty + ?, updated_at = ?
            WHERE id = ? AND shipped_qty + ? <= qty""",
        (int(qty), timeutil.now_iso(), int(detail_id), int(qty))).rowcount


# ==================== 销售退货 ====================

def insert_return(conn, fields: dict) -> int:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO wms_sales_return(code, name, source_order_id, order_code_snap, customer_id,
                                        customer_name_snap, warehouse_id, return_date,
                                        total_qty, total_amount, status, return_reason, remark,
                                        is_active, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,1,?,?)""",
        (fields["code"], fields["name"], fields.get("source_order_id"),
         fields.get("order_code_snap"), fields["customer_id"], fields.get("customer_name_snap"),
         fields["warehouse_id"], fields.get("return_date") or now,
         int(fields.get("total_qty") or 0), float(fields.get("total_amount") or 0),
         fields["return_reason"], fields.get("remark"), now, now))
    return int(cur.lastrowid)


def update_return(conn, return_id: int, fields: dict) -> int:
    sets, args = [], []
    for key in ("name", "source_order_id", "order_code_snap", "customer_id", "customer_name_snap",
                "warehouse_id", "return_date", "total_qty", "total_amount", "status", "posted_at",
                "posted_by", "submit_at", "approve_at", "approver_id", "reject_reason",
                "return_reason", "remark", "is_active"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return 0
    sets.append("updated_at = ?")
    args.append(timeutil.now_iso())
    args.append(int(return_id))
    return conn.execute(
        f"UPDATE wms_sales_return SET {', '.join(sets)} WHERE id = ?", tuple(args)).rowcount


def get_return(conn, return_id: int) -> dict | None:
    row = conn.execute(
        f"""SELECT {_RETURN_COLS} FROM wms_sales_return r
              LEFT JOIN warehouses w ON w.id = r.warehouse_id
              LEFT JOIN users      u ON u.id = r.posted_by
             WHERE r.id = ?""", (int(return_id),)).fetchone()
    return dict(row) if row else None


def list_returns(conn, filters: dict) -> tuple[list[dict], int]:
    where, args = ["r.is_active = 1"], []
    if filters.get("status"):
        where.append("r.status = ?")
        args.append(str(filters["status"]).upper())
    if filters.get("customer_id"):
        where.append("r.customer_id = ?")
        args.append(int(filters["customer_id"]))
    if filters.get("source_order_id"):
        where.append("r.source_order_id = ?")
        args.append(int(filters["source_order_id"]))
    if filters.get("date_from"):
        where.append("r.return_date >= ?")
        args.append(str(filters["date_from"]))
    if filters.get("date_to"):
        where.append("r.return_date <= ?")
        args.append(str(filters["date_to"]))
    if filters.get("keyword"):
        where.append("(r.code LIKE ? OR r.name LIKE ?)")
        args.extend([f"%{filters['keyword']}%", f"%{filters['keyword']}%"])
    clause = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM wms_sales_return r WHERE {clause}", tuple(args)).fetchone()[0]
    page = max(1, int(filters.get("page") or 1))
    page_size = max(1, int(filters.get("page_size") or 20))
    rows = conn.execute(
        f"""SELECT {_RETURN_COLS} FROM wms_sales_return r
              LEFT JOIN warehouses w ON w.id = r.warehouse_id
              LEFT JOIN users      u ON u.id = r.posted_by
             WHERE {clause}
             ORDER BY r.return_date DESC, r.id DESC
             LIMIT ? OFFSET ?""", tuple(args + [page_size, (page - 1) * page_size])).fetchall()
    return [dict(r) for r in rows], int(total)


def replace_return_details(conn, return_id: int, lines: list[dict]) -> None:
    conn.execute("DELETE FROM wms_sales_return_detail WHERE return_id = ?", (int(return_id),))
    now = timeutil.now_iso()
    for index, line in enumerate(lines):
        conn.execute(
            """INSERT INTO wms_sales_return_detail(return_id, order_detail_id, product_id,
                                                   product_name_snap, barcode_snap, unit_snap,
                                                   qty, price, amount, sort_order, remark,
                                                   created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (int(return_id), line.get("order_detail_id"), line["product_id"],
             line["product_name_snap"], line.get("barcode_snap"), line["unit_snap"],
             int(line["qty"]), float(line.get("price") or 0), float(line.get("amount") or 0),
             index, line.get("remark"), now, now))


def list_return_details(conn, return_id: int) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_RETURN_DETAIL_COLS} FROM wms_sales_return_detail d
             WHERE d.return_id = ? ORDER BY d.sort_order, d.id""", (int(return_id),)).fetchall()
    return [dict(r) for r in rows]


# ==================== 客户对账 ====================

def statement_rows(conn, partner_id: int, date_from: str, date_to: str) -> list[dict]:
    """按客户 + 期间取销售出库与退货流水（金额来自账本，不用单据合计）。

    ★ 只认 ref_type ∈ (SALES_OUT, SALES_RETURN)：别的流水即使带 partner_id 也不进对账单
      （采购入库、领用单也会带 partner_id，混进来金额就错了）。
    """
    rows = conn.execute(
        """SELECT l.id AS log_id, l.created_at, l.ref_type, l.ref_id,
                  l.product_name_snapshot, l.quantity, l.unit_price, l.amount,
                  COALESCE(so.code, sr.code) AS doc_code,
                  COALESCE(so.name, sr.name) AS doc_name
             FROM stock_logs l
             LEFT JOIN wms_sales_order  so ON l.ref_type = 'SALES_OUT'    AND so.id = l.ref_id
             LEFT JOIN wms_sales_return sr ON l.ref_type = 'SALES_RETURN' AND sr.id = l.ref_id
            WHERE l.partner_id = ?
              AND l.ref_type IN ('SALES_OUT', 'SALES_RETURN')
              AND substr(l.created_at, 1, 10) >= ?
              AND substr(l.created_at, 1, 10) <= ?
            ORDER BY l.created_at, l.id""",
        (int(partner_id), str(date_from)[:10], str(date_to)[:10])).fetchall()
    return [dict(r) for r in rows]
