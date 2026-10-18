"""盘点单数据访问 —— docs/14 第 4.4 / 4.5 节（P0-2）
★ 本层只写 SQL 不判业务：状态机、快照口径、过账数量全在 services/stocktake_service.py。
★ 快照只取「该仓库当前 qty <> 0 的启用商品」：qty=0 的历史行会把表灌成几千行噪音；
  盘盈（账面 0 实物有）走「追加行」，book_qty 记 0（docs/14 4.7）。
★ diff_qty 在写库时就按 `real_qty - book_qty` 算好存下：差异报表只认这个快照口径，
  与过账用的「实时当前值」是两回事（docs/14 4.3），两个数字各有用途，不要合并。
"""
from __future__ import annotations

from services import timeutil

_MAIN_COLS = """s.id, s.code, s.name, s.warehouse_id, s.scope_category, s.status,
                s.book_locked_at, s.posted_at, s.operator_id, s.submitter_id, s.approver_id,
                s.remark, s.created_at, s.updated_at,
                w.name AS warehouse_name,
                COALESCE(u.display_name, u.username) AS operator_name,
                COALESCE(a.display_name, a.username) AS approver_name,
                COALESCE(b.display_name, b.username) AS submitter_name"""

_STATS_COLS = """(SELECT COUNT(*) FROM wms_stocktake_detail d WHERE d.stocktake_id = s.id) AS line_count,
                 (SELECT COUNT(*) FROM wms_stocktake_detail d
                   WHERE d.stocktake_id = s.id AND d.real_qty IS NOT NULL) AS counted_count,
                 (SELECT COUNT(*) FROM wms_stocktake_detail d
                   WHERE d.stocktake_id = s.id AND d.real_qty IS NOT NULL
                     AND d.diff_qty <> 0) AS diff_count,
                 (SELECT COALESCE(SUM(ABS(d.diff_qty)), 0) FROM wms_stocktake_detail d
                   WHERE d.stocktake_id = s.id AND d.real_qty IS NOT NULL) AS diff_abs_qty"""

_JOINS = """LEFT JOIN warehouses w ON w.id = s.warehouse_id
            LEFT JOIN users u ON u.id = s.operator_id
            LEFT JOIN users a ON a.id = s.approver_id
            LEFT JOIN users b ON b.id = s.submitter_id"""

_DETAIL_COLS = """d.id, d.stocktake_id, d.product_id, d.book_qty, d.real_qty, d.diff_qty,
                  d.product_name_snapshot, d.remark, d.created_at,
                  p.barcode, p.unit, p.category,
                  COALESCE(sc.qty, 0) AS current_qty"""


# ==================== 主表 ====================

def insert(conn, fields: dict) -> int:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO wms_stocktake(code, name, warehouse_id, scope_category, status,
                                     book_locked_at, operator_id, remark, created_at, updated_at)
           VALUES(?,?,?,?,'DRAFT',?,?,?,?,?)""",
        (fields["code"], fields["name"], int(fields["warehouse_id"]), fields.get("scope_category"),
         fields.get("book_locked_at") or now, int(fields["operator_id"]),
         fields.get("remark"), now, now))
    return int(cur.lastrowid)


def update(conn, stocktake_id: int, fields: dict) -> int:
    """主表更新（白名单列，禁止拼任意列名）。"""
    sets, args = [], []
    for key in ("name", "status", "posted_at", "submitter_id", "approver_id", "remark"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return 0
    sets.append("updated_at = ?")
    args.append(timeutil.now_iso())
    args.append(int(stocktake_id))
    return conn.execute(
        f"UPDATE wms_stocktake SET {', '.join(sets)} WHERE id = ?", tuple(args)).rowcount


def set_status(conn, stocktake_id: int, expect, fields: dict) -> int:
    """条件更新做并发保护：状态不符时 0 行，调用方转 409（重复点过账/审批）。

    `expect` 可以是单个状态字符串，也可以是允许的状态元组（提交允许 DRAFT/REJECTED）。
    """
    sets, args = [], []
    for key in ("status", "posted_at", "submitter_id", "approver_id", "remark", "name"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    sets.append("updated_at = ?")
    args.append(timeutil.now_iso())
    args.append(int(stocktake_id))
    if isinstance(expect, (tuple, list, set)):
        wanted = [str(x).upper() for x in expect]
        args.extend(wanted)
        cond = f"status IN ({', '.join('?' * len(wanted))})"
    else:
        args.append(str(expect).upper())
        cond = "status = ?"
    return conn.execute(
        f"UPDATE wms_stocktake SET {', '.join(sets)} WHERE id = ? AND {cond}",
        tuple(args)).rowcount


def count_snapshot_rows(conn, warehouse_id: int, category: str | None) -> int:
    """建单前预检：该仓库有多少行可盘（口径必须与 snapshot() 完全一致）。"""
    sql = """SELECT COUNT(*) FROM stock_current sc
               JOIN products p ON p.id = sc.product_id
              WHERE sc.warehouse_id = ? AND sc.qty <> 0 AND p.is_active = 1"""
    args = [int(warehouse_id)]
    if category:
        sql += " AND p.category = ?"
        args.append(str(category))
    return int(conn.execute(sql, tuple(args)).fetchone()[0])


def get(conn, stocktake_id: int) -> dict | None:
    row = conn.execute(
        f"""SELECT {_MAIN_COLS}, {_STATS_COLS}
              FROM wms_stocktake s {_JOINS}
             WHERE s.id = ?""", (int(stocktake_id),)).fetchone()
    return dict(row) if row else None


def _filters(filters: dict) -> tuple[str, list]:
    where, args = ["1 = 1"], []
    if filters.get("status"):
        where.append("s.status = ?")
        args.append(str(filters["status"]).upper())
    if filters.get("warehouse_id"):
        where.append("s.warehouse_id = ?")
        args.append(int(filters["warehouse_id"]))
    if filters.get("date_from"):
        where.append("substr(s.created_at, 1, 10) >= ?")
        args.append(str(filters["date_from"])[:10])
    if filters.get("date_to"):
        where.append("substr(s.created_at, 1, 10) <= ?")
        args.append(str(filters["date_to"])[:10])
    if filters.get("keyword"):
        where.append("(s.code LIKE ? OR s.name LIKE ?)")
        args.extend([f"%{filters['keyword']}%", f"%{filters['keyword']}%"])
    return " AND ".join(where), args


def list_all(conn, filters: dict) -> tuple[list[dict], int]:
    clause, args = _filters(filters or {})
    total = conn.execute(
        f"SELECT COUNT(*) FROM wms_stocktake s WHERE {clause}", tuple(args)).fetchone()[0]
    page = max(1, int(filters.get("page") or 1))
    page_size = max(1, int(filters.get("page_size") or 20))
    rows = conn.execute(
        f"""SELECT {_MAIN_COLS}, {_STATS_COLS}
              FROM wms_stocktake s {_JOINS}
             WHERE {clause}
             ORDER BY s.created_at DESC, s.id DESC
             LIMIT ? OFFSET ?""", tuple(args + [page_size, (page - 1) * page_size])).fetchall()
    return [dict(r) for r in rows], int(total)


# ==================== 快照与明细 ====================

def snapshot(conn, stocktake_id: int, warehouse_id: int, category: str | None) -> int:
    """按仓库当前库存生成快照；只取 qty<>0 的启用商品（停用商品不参与盘点）。"""
    now = timeutil.now_iso()
    sql = """INSERT INTO wms_stocktake_detail(stocktake_id, product_id, book_qty, real_qty,
                                              diff_qty, product_name_snapshot, created_at)
             SELECT ?, p.id, sc.qty, NULL, 0, p.name, ?
               FROM stock_current sc
               JOIN products p ON p.id = sc.product_id
              WHERE sc.warehouse_id = ? AND sc.qty <> 0 AND p.is_active = 1"""
    args = [int(stocktake_id), now, int(warehouse_id)]
    if category:
        sql += " AND p.category = ?"
        args.append(str(category))
    cursor = conn.execute(sql, tuple(args))
    return int(cursor.rowcount or 0)


def add_detail(conn, stocktake_id: int, fields: dict) -> int:
    cur = conn.execute(
        """INSERT INTO wms_stocktake_detail(stocktake_id, product_id, book_qty, real_qty,
                                            diff_qty, product_name_snapshot, remark, created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (int(stocktake_id), int(fields["product_id"]), int(fields.get("book_qty") or 0),
         fields.get("real_qty"), int(fields.get("real_qty") or 0) - int(fields.get("book_qty") or 0),
         fields["product_name_snapshot"], fields.get("remark"), timeutil.now_iso()))
    return int(cur.lastrowid)


def list_details(conn, stocktake_id: int) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_DETAIL_COLS}
              FROM wms_stocktake_detail d
              JOIN products p ON p.id = d.product_id
              LEFT JOIN stock_current sc
                     ON sc.product_id = d.product_id
                    AND sc.warehouse_id = (SELECT warehouse_id FROM wms_stocktake WHERE id = d.stocktake_id)
             WHERE d.stocktake_id = ?
             ORDER BY p.category, p.name, d.id""", (int(stocktake_id),)).fetchall()
    return [dict(r) for r in rows]


def get_detail(conn, stocktake_id: int, detail_id: int) -> dict | None:
    row = conn.execute(
        f"""SELECT {_DETAIL_COLS}
              FROM wms_stocktake_detail d
              JOIN products p ON p.id = d.product_id
              LEFT JOIN stock_current sc
                     ON sc.product_id = d.product_id
                    AND sc.warehouse_id = (SELECT warehouse_id FROM wms_stocktake WHERE id = d.stocktake_id)
             WHERE d.stocktake_id = ? AND d.id = ?""",
        (int(stocktake_id), int(detail_id))).fetchone()
    return dict(row) if row else None


def find_detail_by_product(conn, stocktake_id: int, product_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, real_qty FROM wms_stocktake_detail WHERE stocktake_id = ? AND product_id = ?",
        (int(stocktake_id), int(product_id))).fetchone()
    return dict(row) if row else None


def set_real_qty(conn, detail_id: int, real_qty: int | None) -> int:
    """录入实盘数：diff_qty 用 book_qty 现算（快照口径，供差异报表）。"""
    return conn.execute(
        """UPDATE wms_stocktake_detail
              SET real_qty = ?,
                  diff_qty = CASE WHEN ? IS NULL THEN 0 ELSE ? - book_qty END
            WHERE id = ?""",
        (real_qty, real_qty, real_qty, int(detail_id))).rowcount


def count_recorded(conn, stocktake_id: int) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM wms_stocktake_detail WHERE stocktake_id = ? AND real_qty IS NOT NULL",
        (int(stocktake_id),)).fetchone()[0])


def count_details(conn, stocktake_id: int) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM wms_stocktake_detail WHERE stocktake_id = ?",
        (int(stocktake_id),)).fetchone()[0])


# ==================== 期间变动检测 ====================

def count_logs_since(conn, warehouse_id: int, since: str) -> int:
    """建单快照之后该仓库发生的流水笔数：>0 说明账面数已过时（过账按实时值算，仅提示）。"""
    return int(conn.execute(
        "SELECT COUNT(*) FROM stock_logs WHERE warehouse_id = ? AND created_at > ?",
        (int(warehouse_id), str(since))).fetchone()[0])
