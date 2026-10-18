"""products 数据访问 —— PRD 5.3 / docs/03 4.1~4.5 · 陷阱 T-05 / T-19 / T-22

★ 唯一 SQL 出口之一：全部参数化；排序列走 SORT_WHITELIST 映射（绝不拼接用户输入）。
★ 列表一次 JOIN 带回库存（total_qty / stock_by_warehouse），禁止模板循环查库。
★ barcode UNIQUE 含软删记录：软删商品的条码永久占用（PRD 4.3.5，防一码两品）。
"""
from __future__ import annotations

from models import database
from services import timeutil

SORT_WHITELIST = {"id": "p.id", "name": "p.name", "barcode": "p.barcode",
                  "category": "p.category", "updated_at": "p.updated_at"}

COLUMNS = """p.id, p.name, p.barcode, p.category, p.unit, p.cost_price, p.sale_price,
             p.min_stock, p.remark, p.is_active, p.deleted_at, p.created_at, p.updated_at"""

SELECT_WITH_STOCK = f"""
  SELECT {COLUMNS},
         COALESCE(SUM(sc.qty), 0) AS total_qty
    FROM products p
    LEFT JOIN stock_current sc ON sc.product_id = p.id
"""


def _order_clause(sort: str | None, order: str | None) -> str:
    col = SORT_WHITELIST.get(str(sort), "p.id")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    return f"ORDER BY {col} {direction}"


def search(conn, keyword=None, barcode=None, category=None, only_active=True,
           page=1, page_size=20, sort="id", order="desc") -> tuple[list[dict], int]:
    where, args = [], []
    if only_active:
        where.append("p.is_active = 1")
    if barcode:
        where.append("p.barcode = ?")
        args.append(barcode)
    elif keyword:
        # 精确条码优先，其次名称模糊（PRD 7.2），最后按内码命中
        # ★内码是派生值（见 _internal_code），库里没有这一列，必须在 SQL 里现算：
        #   写成 p.internal_code 会直接 "no such column" —— 关键词搜索整条链路 500。
        where.append("(p.barcode = ? OR p.name LIKE ? OR ('S' || printf('%010d', p.id)) = ?)")
        args.extend([keyword, f"%{keyword}%", keyword])
    if category:
        where.append("p.category = ?")
        args.append(category)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM products p {sql_where}", tuple(args)).fetchone()[0]
    rows = conn.execute(
        f"""{SELECT_WITH_STOCK} {sql_where}
             GROUP BY p.id {_order_clause(sort, order)} LIMIT ? OFFSET ?""",
        tuple(args) + (page_size, (page - 1) * page_size)).fetchall()
    items = [dict(r) for r in rows]
    for item in items:
        item["internal_code"] = _internal_code(item["id"])
        item["stock_by_warehouse"] = stock_by_warehouse(conn, item["id"])
    return items, int(total)


def _internal_code(product_id: int) -> str:
    """内码 = 'S' + 10 位补零 ID：与厂商纯数字条码天然不冲突（PRD 5.7）。"""
    return f"S{int(product_id):010d}"


def get(conn, product_id: int) -> dict | None:
    row = conn.execute(f"SELECT {COLUMNS} FROM products p WHERE p.id = ?", (product_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["internal_code"] = _internal_code(product_id)
    item["total_qty"] = total_qty(conn, product_id)
    item["stock_by_warehouse"] = stock_by_warehouse(conn, product_id)
    return item


def find_by_barcode(conn, barcode: str) -> dict | None:
    row = conn.execute(f"SELECT {COLUMNS} FROM products p WHERE p.barcode = ?", (barcode,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["internal_code"] = _internal_code(item["id"])
    item["total_qty"] = total_qty(conn, item["id"])
    item["stock_by_warehouse"] = stock_by_warehouse(conn, item["id"])
    return item


def exists_barcode(conn, barcode: str) -> dict | None:
    """含软删记录（唯一性判定必须把它们算进来）。"""
    row = conn.execute(
        "SELECT id, name, barcode, is_active FROM products WHERE barcode = ?", (barcode,)).fetchone()
    return dict(row) if row else None


def total_qty(conn, product_id: int) -> int:
    row = conn.execute("SELECT COALESCE(SUM(qty),0) FROM stock_current WHERE product_id=?", (product_id,)).fetchone()
    return int(row[0])


def stock_by_warehouse(conn, product_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT sc.warehouse_id, sc.qty FROM stock_current sc
            JOIN warehouses w ON w.id = sc.warehouse_id
           WHERE sc.product_id = ? ORDER BY w.sort_order, w.id""", (product_id,)).fetchall()
    return [{"warehouse_id": r["warehouse_id"], "qty": r["qty"]} for r in rows]


def create(conn, fields: dict) -> dict:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO products(name, barcode, category, unit, cost_price, sale_price,
                                min_stock, remark, is_active, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,1,?,?)""",
        (fields["name"], fields.get("barcode"), fields.get("category") or "未分类",
         fields.get("unit") or "件", fields.get("cost_price", 0), fields.get("sale_price", 0),
         fields.get("min_stock", 0), fields.get("remark"), now, now),
    )
    return get(conn, int(cur.lastrowid))


def update(conn, product_id: int, fields: dict, expected_updated_at: str | None = None) -> tuple[int, dict | None]:
    """返回 (受影响行数, 更新后的商品)。0 行 = 乐观锁冲突或记录不存在。"""
    if not fields:
        return 0, get(conn, product_id)
    sets, args = [], []
    for key in ("name", "barcode", "category", "unit", "cost_price", "sale_price", "min_stock", "remark"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return 0, get(conn, product_id)
    now = timeutil.now_iso()
    sets.append("updated_at = ?")
    args.append(now)
    args.append(product_id)
    sql = f"UPDATE products SET {', '.join(sets)} WHERE id = ?"
    if expected_updated_at is not None:
        sql += " AND updated_at = ?"
        args.append(expected_updated_at)
    rowcount = conn.execute(sql, tuple(args)).rowcount
    return rowcount, get(conn, product_id)


def soft_delete(conn, product_id: int) -> int:
    return conn.execute(
        "UPDATE products SET is_active = 0, deleted_at = ?, updated_at = ? WHERE id = ? AND is_active = 1",
        (timeutil.now_iso(), timeutil.now_iso(), product_id)).rowcount


def restore(conn, product_id: int) -> int:
    return conn.execute(
        "UPDATE products SET is_active = 1, deleted_at = NULL, updated_at = ? WHERE id = ?",
        (timeutil.now_iso(), product_id)).rowcount


def log_count(conn, product_id: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM stock_logs WHERE product_id=?", (product_id,)).fetchone()[0])


def hard_delete(conn, product_id: int) -> bool:
    """仅无流水时允许（调用方负责校验 log_count 与权限）。"""
    return conn.execute("DELETE FROM products WHERE id = ?", (product_id,)).rowcount == 1


def next_internal_code(conn, product_id: int) -> str:
    return _internal_code(product_id)


def count_active(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM products WHERE is_active = 1").fetchone()[0])


def list_categories(conn) -> list[str]:
    rows = conn.execute("SELECT DISTINCT category FROM products ORDER BY category").fetchall()
    return [r["category"] for r in rows if r["category"]]
