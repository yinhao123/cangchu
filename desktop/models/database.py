"""SQLite 连接与事务基座 —— PRD 9.6 / 12.6 · 陷阱 T-01 / T-02 / T-03 / T-08

★ 全项目唯一 SQL 出口层。业务代码禁止自行 sqlite3.connect()。
★ 每请求一连接（T-01）：waitress 8 线程，跨线程共用连接会串位/随机错乱。
★ PRAGMA 每次连接后重设（T-02）：foreign_keys 与 busy_timeout 不持久化，SQLite 默认关外键。
★ synchronous=FULL（T-03）：账本宁慢不丢，禁止为性能降 NORMAL。
★ isolation_level=None：事务由业务显式 BEGIN IMMEDIATE / COMMIT / ROLLBACK，避免隐式提交时机不可控。
"""
from __future__ import annotations

import itertools
import sqlite3
from contextlib import contextmanager
from typing import Any, Callable, Iterable

from flask import current_app, g

PRAGMAS: tuple[str, ...] = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = FULL",
    "PRAGMA journal_mode = WAL",
)


def connect(db_file: str) -> sqlite3.Connection:
    """建立一个符合本项目约束的连接（测试与脚本走这里，保证与生产同路径）。"""
    conn = sqlite3.connect(db_file, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        conn.execute(pragma)
    return conn


def get_connection() -> sqlite3.Connection:
    """当前请求的连接；同请求内复用，请求结束由 teardown 关闭。"""
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def init_app(app) -> None:
    """注册 teardown 关连接 + 暴露 DB_PATH 校验。"""
    app.config.setdefault("DB_PATH", None)

    @app.teardown_appcontext
    def _close(exc=None):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()


_point_seq = itertools.count(1)


@contextmanager
def transaction(conn: sqlite3.Connection):
    """显式事务，**可重入**。

    顶层：BEGIN IMMEDIATE（立刻拿写锁）→ COMMIT / ROLLBACK。
    嵌套：自动降级为 SAVEPOINT —— 批量同步是「外层一个事务 + 每条一个写入事务」的形状（T-08），
         若嵌套仍发 BEGIN，SQLite 直接报 "cannot start a transaction within a transaction"。
    铁律：一次业务操作的「写流水 + 更新库存」必须整体在同一个事务内（PRD 5.4）。
    """
    if conn.in_transaction:
        point = f"tx_{next(_point_seq)}"
        conn.execute(f"SAVEPOINT {point}")
        try:
            yield conn
            conn.execute(f"RELEASE {point}")
        except Exception:
            conn.execute(f"ROLLBACK TO {point}")
            conn.execute(f"RELEASE {point}")
            raise
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def batch_with_savepoints(conn: sqlite3.Connection, items: Iterable[Any],
                          handler: Callable[[Any], Any]) -> list[Any]:
    """批量同步专用（T-08）：外层单事务 + 每条 SAVEPOINT。

    一条失败只回滚那一条（其余 199 条保留），同时整批只 fsync 一次。
    逐条 autocommit 会把 200 条变成 200 次 fsync 并让其它写入长时间排队。
    """
    results: list[Any] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for index, item in enumerate(items):
            point = f"sp_{index}"
            conn.execute(f"SAVEPOINT {point}")
            try:
                results.append(handler(item))
                conn.execute(f"RELEASE {point}")
            except Exception as exc:  # 业务错（含嵌套事务已回滚到自身 savepoint）：该条失败，继续后面的
                conn.execute(f"ROLLBACK TO {point}")
                conn.execute(f"RELEASE {point}")
                results.append(exc)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return results


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def one_or_none(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> dict | None:
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row is not None else None


def run_integrity_checks(conn: sqlite3.Connection) -> list[str]:
    """启动自检（PRD 15.5/15.6）：结构完整性 + 外键 + 账本不变式；非空即进只读模式。"""
    problems: list[str] = []
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        problems.append("integrity_check 未通过：数据库文件可能损坏")
    for row in conn.execute("PRAGMA foreign_key_check"):
        problems.append(f"外键悬空：table={row[0]} rowid={row[1]} refs={row[2]}")
    for row in conn.execute(
        """SELECT sc.product_id, sc.warehouse_id, sc.qty AS recorded,
                  COALESCE(SUM(CASE l.type WHEN 'IN' THEN l.quantity
                                           WHEN 'OUT' THEN -l.quantity
                                           ELSE l.quantity END), 0) AS computed
             FROM stock_current sc
             LEFT JOIN stock_logs l ON l.product_id=sc.product_id AND l.warehouse_id=sc.warehouse_id
            GROUP BY sc.product_id, sc.warehouse_id, sc.qty
           HAVING recorded != computed"""
    ):
        problems.append(f"库存与流水不一致：product={row[0]} warehouse={row[1]} 账面={row[2]} 应为={row[3]}")
    for row in conn.execute("SELECT product_id, warehouse_id, qty FROM stock_current WHERE qty < 0"):
        problems.append(f"出现负库存：product={row[0]} warehouse={row[1]} qty={row[2]}")

    # 检查 6（0007 起，docs/14 7.4）：成本重放一致性。数量不一致已在上面报告，这里只报均价。
    from models import stock_repo  # 延迟导入：避免与 cost_service 的相互引用在导入期成环
    for diff in stock_repo.recompute_from_logs(conn):
        if diff["recorded"] != diff["computed"]:
            continue
        problems.append(
            f"库存均价与流水重放不一致：product={diff['product_id']} warehouse={diff['warehouse_id']} "
            f"账面={diff['avg_recorded']} 应为={diff['avg_computed']}")
    return problems
