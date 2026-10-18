"""极简迁移器 —— PRD 4.5 · 规则 20-stack-conventions.md

设计（6 张表不值得上 Alembic）：
  schema_meta(version, applied_at) 记录已到版本 → 顺序执行编号更大者 → 每个文件单事务 → 记录版本。
  任一文件失败：该文件整体 ROLLBACK（不留半截表）+ 版本不前进 + 抛异常 → 调用方必须拒绝启动。

★ 迁移文件里的 PRAGMA 语句会在事务外单独执行：journal_mode 在事务内切换会被 SQLite 拒绝。
★ 禁止修改已应用的迁移文件；写错就加新文件向前修复。
★ 迁移前必须先做在线备份（services/backup_service.py，PRD 15.3）。
"""
from __future__ import annotations

import os
import re
import sqlite3

from services import timeutil

MIGRATIONS_DIR: str | None = None  # 默认取包内 db/migrations；测试可 monkeypatch

_PRAGMA_RE = re.compile(r"^\s*(PRAGMA[^;]*);", re.IGNORECASE | re.MULTILINE)

BOOTSTRAP = "CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"



def split_statements(sql: str) -> list[str]:
    """把 SQL 文本切成可逐句执行的语句 —— 迁移必须「单事务 + 逐句执行」。

    ★ 不能用 executescript：它会先提交挂起事务，失败时无法原子回滚（实测踩到）。
    ★ CREATE TRIGGER ... BEGIN ... END; 体内的分号不是语句结束符，必须整条保留。
    ★ 跳过 -- 与 /* */ 注释、字符串字面量里的分号。
    """
    statements: list[str] = []
    buf: list[str] = []
    in_str: str | None = None
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if in_str:
            buf.append(ch)
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch == "-" and nxt == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in ("'", '"'):
            in_str = ch
        buf.append(ch)
        if ch == ";":
            stmt = "".join(buf).strip()
            up = stmt.upper()
            if up.startswith("CREATE TRIGGER") and not up.rstrip(";").endswith("END"):
                i += 1  # 触发器体内分号，继续累积
                continue
            if stmt:
                statements.append(stmt)
            buf = []
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _default_dir() -> str:
    if MIGRATIONS_DIR:
        return MIGRATIONS_DIR
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "migrations")


def _files(migrations_dir: str) -> list[tuple[int, str]]:
    """按编号排序的 (version, path)。编号来自文件名 NNNN_*.sql。"""
    out: list[tuple[int, str]] = []
    for name in sorted(os.listdir(migrations_dir)):
        if not name.endswith(".sql"):
            continue
        m = re.match(r"^(\d+)_", name)
        if not m:
            raise ValueError(f"迁移文件名必须形如 NNNN_描述.sql，实际：{name}")
        out.append((int(m.group(1)), os.path.join(migrations_dir, name)))
    return out


def current_version(db_file: str) -> int:
    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_meta").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0  # 表还不存在 = 全新库
    finally:
        conn.close()


def run_migrations(db_file: str, migrations_dir: str | None = None) -> list[int]:
    """把库升到最新。返回本次实际应用的版本号列表。失败抛异常且不记录版本。"""
    mdir = migrations_dir or _default_dir()
    applied: list[int] = []
    conn = sqlite3.connect(db_file, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute(BOOTSTRAP)
        have = current_version(db_file)
        for version, path in _files(mdir):
            if version <= have:
                continue
            sql = open(path, encoding="utf-8").read()
            pragmas = _PRAGMA_RE.findall(sql)
            body = _PRAGMA_RE.sub("", sql)
            for p in pragmas:  # 事务外执行（journal_mode 不允许在事务内切换）
                conn.execute(p + ";")
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in split_statements(body):  # 逐句执行，异常时整体回滚
                    conn.execute(stmt)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta(version, applied_at) VALUES(?, ?)",
                    (version, timeutil.now_iso()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise RuntimeError(f"迁移 {os.path.basename(path)} 失败，已整体回滚，拒绝带病启动") from None
            applied.append(version)
    finally:
        conn.close()
    return applied
