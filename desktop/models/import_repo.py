"""批量导入/导出的数据访问 —— docs/14 §3（P0-1）

★ 本层只写 SQL，不判业务（分层铁律）：哪些行算冲突、能不能期初建账，全在
  `services/import_service.py` 判断。
★ 导入一律**逐行 INSERT**（不用 `executemany`）：`skip_invalid=true` 时每行要能
  单独 SAVEPOINT 回滚并精确报出出错行号；`executemany` 一旦抛错，整批都说不清是谁坏的。
★ 期初库存的写入不在这里 —— 它属于账本，只能走 `services/stock_service.opening_stock()`。
"""
from __future__ import annotations

from services import timeutil


def insert_product(conn, fields: dict) -> int:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO products(name, barcode, category, unit, cost_price, sale_price,
                                min_stock, remark, is_active, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,1,?,?)""",
        (fields["name"], fields.get("barcode"), fields.get("category") or "未分类",
         fields.get("unit") or "件", fields.get("cost_price", 0), fields.get("sale_price", 0),
         fields.get("min_stock", 0), fields.get("remark"), now, now))
    return int(cur.lastrowid)


def insert_partner(conn, fields: dict) -> int:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO partners(name, type, contact, phone, address, remark, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (fields["name"], fields["type"], fields.get("contact"), fields.get("phone"),
         fields.get("address"), fields.get("remark"), now, now))
    return int(cur.lastrowid)


def count_stock_logs(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0])


def find_warehouse(conn, code_or_name: str) -> dict | None:
    """期初模板允许填「仓库编码」或「仓库名称」。

    ★ 必须两种都试：`initdb` 建的主仓库没有编码（code 为 NULL），
      只认 code 会让所有客户在第一步就卡住（docs/14 3.3 的列名是 warehouse_code）。
    """
    text = str(code_or_name or "").strip()
    if not text:
        return None
    row = conn.execute(
        """SELECT id, code, name, is_active FROM warehouses
            WHERE code = ? OR name = ? ORDER BY (code = ?) DESC LIMIT 1""",
        (text.upper(), text, text.upper())).fetchone()
    return dict(row) if row else None


def list_products_for_export(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT p.id, p.name, p.barcode, p.category, p.unit, p.cost_price, p.sale_price,
                  p.min_stock, p.remark, p.is_active, p.created_at, p.updated_at
             FROM products p ORDER BY p.id""").fetchall()
    return [dict(r) for r in rows]


def list_partners_for_export(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT id, name, type, contact, phone, address, remark, is_active, created_at
             FROM partners ORDER BY type, name""").fetchall()
    return [dict(r) for r in rows]


def list_stock_for_export(conn) -> list[dict]:
    """当前库存导出：商品 × 仓库。

    ★ 不含 `avg_cost`：成本口径属 P1-5（`stock_current.avg_cost` 尚未加列），
      这里不拿 `products.cost_price`（参考成本）冒充账面成本 —— 那会让客户按错数报税。
    """
    rows = conn.execute(
        """SELECT p.name, p.barcode, p.unit, w.name AS warehouse_name,
                  sc.qty, sc.updated_at
             FROM stock_current sc
             JOIN products p   ON p.id = sc.product_id
             JOIN warehouses w ON w.id = sc.warehouse_id
            ORDER BY w.sort_order, w.id, p.name""").fetchall()
    return [dict(r) for r in rows]
