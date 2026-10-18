"""partners（客户/供应商）数据访问 —— docs/03 4.8；UNIQUE(name, type)"""
from __future__ import annotations

from services import timeutil


def list_by_type(conn, partner_type: str, keyword=None, limit: int = 200) -> list[dict]:
    sql = ("SELECT id, name, type, contact, phone, address, remark, is_active, created_at, updated_at "
           "FROM partners WHERE type = ? AND is_active = 1")
    args: list = [partner_type]
    if keyword:
        sql += " AND (name LIKE ? OR contact LIKE ?)"
        args += [f"%{keyword}%", f"%{keyword}%"]
    sql += " ORDER BY name LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def list_active(conn, keyword=None, limit: int = 200) -> list[dict]:
    """不分类型的「全部启用往来单位」—— 供 `GET /api/partners`（不传 type）使用。

    库存调整等场景既要客户也要供应商，没有 type 语义可筛（docs/03 4.8）。
    """
    sql = ("SELECT id, name, type, contact, phone, address, remark, is_active, created_at, updated_at "
           "FROM partners WHERE is_active = 1")
    args: list = []
    if keyword:
        sql += " AND (name LIKE ? OR contact LIKE ?)"
        args += [f"%{keyword}%", f"%{keyword}%"]
    sql += " ORDER BY type, name LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def get(conn, partner_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()
    return dict(row) if row else None


def find_by_name_type(conn, name: str, partner_type: str) -> dict | None:
    row = conn.execute("SELECT id, name, type FROM partners WHERE name = ? AND type = ?",
                       (name, partner_type)).fetchone()
    return dict(row) if row else None


def create(conn, fields: dict) -> dict:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO partners(name, type, contact, phone, address, remark, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (fields["name"], fields["type"], fields.get("contact"), fields.get("phone"),
         fields.get("address"), fields.get("remark"), now, now))
    return get(conn, int(cur.lastrowid))


def update(conn, partner_id: int, fields: dict) -> int:
    sets, args = [], []
    for key in ("name", "contact", "phone", "address", "remark", "is_active"):
        if key in fields:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return 0
    sets.append("updated_at = ?")
    args += [timeutil.now_iso(), partner_id]
    return conn.execute(f"UPDATE partners SET {', '.join(sets)} WHERE id = ?", tuple(args)).rowcount
