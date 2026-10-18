"""warehouses 数据访问 —— docs/03 4.6~4.7 · docs/13 4.1（两级树）

本层只提供事实查询（如 has_stock / has_children）；「能不能删」的判断在 services 层（业务规则）。

★ 0002 起 warehouses 是两级树：parent_id=0 为顶级仓库（level=1），库位 level=2。
★ 库存只挂在任一层上，不做父子汇总：树只用于归类与选择，不改变 stock_current 的语义。
"""
from __future__ import annotations

from models import database
from services import timeutil

MAX_LEVEL = 2

_COLS = ("w.id, w.code, w.name, w.parent_id, w.level, w.category, w.address, "
         "w.is_active, w.sort_order, w.created_at, w.updated_at")


def list_all(conn, include_inactive: bool = False) -> list[dict]:
    where = "" if include_inactive else "WHERE w.is_active = 1"
    rows = conn.execute(
        f"""SELECT {_COLS},
                   COUNT(DISTINCT sc.product_id) AS product_count,
                   COALESCE(SUM(sc.qty), 0) AS total_qty,
                   (SELECT COUNT(*) FROM warehouses c WHERE c.parent_id = w.id) AS child_count
              FROM warehouses w
              LEFT JOIN stock_current sc ON sc.warehouse_id = w.id
             {where}
             GROUP BY w.id
             ORDER BY w.level, w.sort_order, w.id""").fetchall()
    return [dict(r) for r in rows]


def list_children(conn, parent_id: int, include_inactive: bool = False) -> list[dict]:
    where = "" if include_inactive else "AND is_active = 1"
    rows = conn.execute(
        f"""SELECT {_COLS} FROM warehouses w
             WHERE w.parent_id = ? {where}
             ORDER BY w.sort_order, w.id""", (parent_id,)).fetchall()
    return [dict(r) for r in rows]


def get(conn, warehouse_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
    return dict(row) if row else None


def create(conn, name: str, address: str | None = None, code: str | None = None,
           parent_id: int = 0, category: str | None = None) -> dict:
    now = timeutil.now_iso()
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM warehouses").fetchone()[0]
    cur = conn.execute(
        """INSERT INTO warehouses(name, address, code, parent_id, level, category, sort_order,
                                  created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (name, address, code, int(parent_id), 1 if int(parent_id) == 0 else 2, category,
         int(max_order) + 1, now, now))
    return get(conn, int(cur.lastrowid))


def update(conn, warehouse_id: int, fields: dict) -> int:
    sets, args = [], []
    for key in ("name", "address", "is_active", "sort_order", "code", "category", "parent_id", "level"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return 0
    sets.append("updated_at = ?")
    args.append(timeutil.now_iso())
    args.append(warehouse_id)
    return conn.execute(f"UPDATE warehouses SET {', '.join(sets)} WHERE id = ?", tuple(args)).rowcount


def has_stock(conn, warehouse_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM stock_current WHERE warehouse_id = ? AND qty != 0 LIMIT 1", (warehouse_id,)).fetchone()
    return row is not None


def has_children(conn, warehouse_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM warehouses WHERE parent_id = ? LIMIT 1", (warehouse_id,)).fetchone()
    return row is not None


def find_by_code(conn, code: str) -> dict | None:
    row = conn.execute("SELECT id, code, name FROM warehouses WHERE code = ?", (code,)).fetchone()
    return dict(row) if row else None


def stock_breakdown(conn, warehouse_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT p.id AS product_id, p.name, sc.qty FROM stock_current sc
             JOIN products p ON p.id = sc.product_id
            WHERE sc.warehouse_id = ? AND sc.qty != 0 ORDER BY p.name""", (warehouse_id,)).fetchall()
    return [dict(r) for r in rows]


def find_by_name(conn, name: str) -> dict | None:
    row = conn.execute("SELECT id, name FROM warehouses WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None
