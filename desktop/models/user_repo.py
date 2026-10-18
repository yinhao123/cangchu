"""users / api_tokens 数据访问 —— PRD 5.1 / docs/02 第 2 章

★ password_hash / token_hash 永不写日志（core/logging_setup 有兜底，但仍不得主动打印）。
★ 禁止 SELECT *（T-19）；返回 dict，NULL 保留为 None。
"""
from __future__ import annotations

from services import timeutil

COLUMNS = ("id, username, display_name, password_hash, role, is_active, "
           "must_change_password, created_at, updated_at, last_login_at")


def find_by_username(conn, username: str) -> dict | None:
    row = conn.execute(f"SELECT {COLUMNS} FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def find_by_id(conn, user_id: int) -> dict | None:
    row = conn.execute(f"SELECT {COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def create(conn, username: str, password_hash: str, role: str = "operator",
           display_name: str | None = None, must_change_password: int = 0) -> dict:
    now = timeutil.now_iso()
    cur = conn.execute(
        """INSERT INTO users(username, display_name, password_hash, role, must_change_password,
                             created_at, updated_at)
           VALUES(?,?,?,?,?,?,?)""",
        (username, display_name, password_hash, role, must_change_password, now, now))
    return find_by_id(conn, int(cur.lastrowid))


def set_password(conn, user_id: int, password_hash: str, clear_must_change: bool = True) -> int:
    now = timeutil.now_iso()
    flag = 0 if clear_must_change else 1
    return conn.execute(
        "UPDATE users SET password_hash=?, must_change_password=?, updated_at=? WHERE id=?",
        (password_hash, flag, now, user_id)).rowcount


def mark_login(conn, user_id: int) -> None:
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (timeutil.now_iso(), user_id))


def set_active(conn, user_id: int, is_active: int) -> int:
    """禁用账号会连带失去鉴权（api_auth 里 is_active=0 直接判 None，PRD 5.1）。"""
    return conn.execute("UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
                        (is_active, timeutil.now_iso(), user_id)).rowcount


def list_all(conn) -> list[dict]:
    rows = conn.execute(
        f"SELECT id, username, display_name, role, is_active, must_change_password, created_at, last_login_at "
        f"FROM users ORDER BY id").fetchall()  # 故意不含 password_hash
    return [dict(r) for r in rows]


def count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
