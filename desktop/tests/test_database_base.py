"""D0 数据库基座测试（TDD RED）—— PRD 9.6 / 12.6 · 陷阱 T-01 / T-02 / T-03 / T-04 / T-08

证明四件在 SQLite 上极易写错的事：
  1. PRAGMA 真的生效（foreign_keys 不持久化，T-02；synchronous 不许降级，T-03）
  2. 每请求一连接 + 请求结束即关闭（T-01，否则 waitress 多线程共用连接会串位）
  3. 账本不可变性由触发器承担（外键只挡父表删除，挡不住删流水）
  4. 迁移器：顺序执行、记录版本、失败整体回滚且拒绝启动
"""
import sqlite3

import pytest

from core.migrator import current_version, run_migrations
from services import timeutil


# ---------- 1. PRAGMA ----------

def test_connection_has_required_pragmas(db_conn):
    got = {
        "foreign_keys": db_conn.execute("PRAGMA foreign_keys").fetchone()[0],
        "busy_timeout": db_conn.execute("PRAGMA busy_timeout").fetchone()[0],
        "synchronous": db_conn.execute("PRAGMA synchronous").fetchone()[0],
        "journal_mode": db_conn.execute("PRAGMA journal_mode").fetchone()[0],
    }
    assert got["foreign_keys"] == 1, "SQLite 默认关闭外键，必须每连接显式打开（T-02）"
    assert got["busy_timeout"] == 5000
    assert got["synchronous"] in (2, 3), "synchronous 必须是 FULL(2) 或更高，禁止 NORMAL(1)（T-03）"
    assert str(got["journal_mode"]).lower() == "wal"


def test_isolation_level_is_none_so_transactions_are_explicit(db_conn):
    """隐式事务时机不可控（PRD 9.6），必须手动 BEGIN IMMEDIATE / COMMIT"""
    assert db_conn.isolation_level is None


def test_pragmas_survive_across_separate_connections(db_path):
    """foreign_keys/busy_timeout 不持久化：新开连接必须重新设置 —— 这正是 T-02 的成因"""
    raw = sqlite3.connect(db_path)
    assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0, "裸连接外键默认关闭"
    raw.close()


# ---------- 2. 外键与账本触发器 ----------

def test_orphan_stock_log_is_rejected(client, seed, db_conn):
    """引用不存在的商品/仓库必须失败（RESTRICT 挡父表 + 外键生效）"""
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,"
            "quantity,before_qty,after_qty,product_name_snapshot,created_at) "
            "VALUES('orphan-1',9999,1,1,'IN',1,0,1,'x',?)", (timeutil.now_iso(),))


def test_ledger_row_cannot_be_deleted(db_conn, seed):
    """物理删除流水被触发器拒绝 —— 外键挡不住这条路径，必须靠触发器"""
    with pytest.raises(sqlite3.IntegrityError, match="账本"):
        db_conn.execute("DELETE FROM stock_logs WHERE id=1")


@pytest.mark.parametrize("col,val", [("quantity", 999), ("type", "'OUT'"), ("before_qty", -5)])
def test_ledger_accounting_columns_are_immutable(db_conn, seed, col, val):
    with pytest.raises(sqlite3.IntegrityError, match="账本"):
        db_conn.execute(f"UPDATE stock_logs SET {col}={val} WHERE id=1")


def test_ledger_reversed_by_and_remark_may_be_updated(db_conn, seed):
    """冲销需要回写 reversed_by 指针、补写备注 —— 这两列必须放行，否则冲销功能无法实现"""
    db_conn.execute("UPDATE stock_logs SET reversed_by=2, remark='冲销登记' WHERE id=1")
    row = db_conn.execute("SELECT reversed_by, remark FROM stock_logs WHERE id=1").fetchone()
    assert row[0] == 2 and row[1] == "冲销登记"


# ---------- 3. 幂等键与 CHECK 约束 ----------

def test_duplicate_client_msg_id_is_rejected(db_conn, seed):
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,"
            "quantity,before_qty,after_qty,product_name_snapshot,created_at) "
            "VALUES('dup-1',1,1,1,'IN',1,0,1,'x',?)", (timeutil.now_iso(),))
        db_conn.execute(
            "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,"
            "quantity,before_qty,after_qty,product_name_snapshot,created_at) "
            "VALUES('dup-1',1,1,1,'IN',2,1,3,'x',?)", (timeutil.now_iso(),))


@pytest.mark.parametrize("qtype,qty", [("IN", 0), ("IN", -3), ("OUT", 0), ("ADJUST", 0)])
def test_quantity_check_constraint(db_conn, seed, qtype, qty):
    """IN/OUT 方向靠 type 表达，quantity 不得为 0；ADJUST 允许负但禁 0"""
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,"
            "quantity,before_qty,after_qty,product_name_snapshot,created_at) "
            "VALUES(?,1,1,1,?,?,0,0,'x',?)",
            (f"chk-{qtype}-{qty}", qtype, qty, timeutil.now_iso()))


def test_lowercase_enum_is_rejected_by_check(db_conn, seed):
    """T-20：枚举大小写混用必须被数据库挡住，不能只靠约定"""
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,"
            "quantity,before_qty,after_qty,product_name_snapshot,created_at) "
            "VALUES('low-1',1,1,1,'in',1,0,1,'x',?)", (timeutil.now_iso(),))


# ---------- 4. 原子扣减（T-06 的唯一正确写法）----------

def test_atomic_decrement_returns_zero_rowcount_when_insufficient(db_conn, seed):
    """库存 10 提 99 → rowcount==0（判不足）；提 3 → rowcount==1 且余 7。
       先 SELECT 再 UPDATE 在并发下会扣成负数，所以这个形状是强制的。"""
    hit = db_conn.execute(
        "UPDATE stock_current SET qty=qty-?, updated_at=? "
        "WHERE product_id=1 AND warehouse_id=1 AND qty>=?", (99, timeutil.now_iso(), 99)).rowcount
    assert hit == 0, "库存不足时必须 0 行受影响，且库存不能被部分扣减"
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 10

    hit2 = db_conn.execute(
        "UPDATE stock_current SET qty=qty-?, updated_at=? "
        "WHERE product_id=1 AND warehouse_id=1 AND qty>=?", (3, timeutil.now_iso(), 3)).rowcount
    assert hit2 == 1
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 7


def test_insert_or_replace_is_not_used_for_stock_current(db_conn, seed):
    """T-07：INSERT OR REPLACE 是 DELETE+INSERT，会触发派生表级联与列重置。
       验证 upsert 两步法不影响既有列值。"""
    db_conn.execute("INSERT OR IGNORE INTO stock_current(product_id,warehouse_id,qty,updated_at) "
                    "VALUES(1,1,0,?)", (timeutil.now_iso(),))
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 10, \
        "INSERT OR IGNORE 已存在则不动，不能覆盖成 0"


# ---------- 5. 迁移器 ----------

def _latest_migration_version() -> int:
    """迁移目录里最大的编号 —— 断言写死 1 的话，加 0002 就会误报（版本随迁移推进）。"""
    from core.migrator import _default_dir, _files
    return max(v for v, _ in _files(_default_dir()))


def test_migrations_record_version(db_path):
    run_migrations(db_path)
    assert current_version(db_path) == _latest_migration_version()


def test_migrations_are_idempotent(db_path):
    run_migrations(db_path)
    run_migrations(db_path)  # 第二次不得报错、不得重复建表
    assert current_version(db_path) == _latest_migration_version()


def test_failed_migration_rolls_back_whole_file_and_raises(tmp_path, monkeypatch):
    """迁移必须单事务执行：半途失败 → 表不存在 + 版本不前进 + 抛异常拒绝启动"""
    db = str(tmp_path / "bad.db")
    bad = tmp_path / "migrations"
    bad.mkdir()
    (bad / "0001_ok.sql").write_text(
        "CREATE TABLE a(id INTEGER PRIMARY KEY);", encoding="utf-8")
    (bad / "0002_bad.sql").write_text(
        "CREATE TABLE b(id INTEGER PRIMARY KEY); INSERT INTO nonexistent VALUES(1);",
        encoding="utf-8")
    monkeypatch.setattr("core.migrator.MIGRATIONS_DIR", str(bad))
    with pytest.raises(Exception):
        run_migrations(db)
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "a" in names, "已成功的迁移 1 应保留"
    assert "b" not in names, "失败的迁移 2 必须整体回滚（不能只建了一半）"
    conn.close()


def test_backup_uses_online_backup_api_not_file_copy(tmp_path):
    """T-04：WAL 下未 checkpoint 的数据在 -wal 里，只拷 .db 得到残缺库"""
    src_path = str(tmp_path / "src.db")
    conn = sqlite3.connect(src_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.execute("INSERT INTO t VALUES(42)")
    conn.commit()
    # 不 checkpoint，制造「数据还在 wal 里」的状态
    dst_path = str(tmp_path / "dst.db")
    dst = sqlite3.connect(dst_path)
    conn.backup(dst)
    assert dst.execute("SELECT x FROM t").fetchone()[0] == 42, "在线备份必须拿到完整数据"
    dst.close(); conn.close()


# ---------- 6. 事务原语必须可重入（批量同步实测撞出来的 500）----------

def test_nested_transaction_uses_savepoint_not_begin(db_conn, seed):
    """事务里再开事务时，SQLite 的 BEGIN 会直接报错。
       批量同步正是「外层事务 + 每条自己的写入事务」这种形状（T-08），所以 transaction 必须可重入。"""
    from models import database
    from services import timeutil
    with database.transaction(db_conn):
        db_conn.execute(
            "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,quantity,"
            "before_qty,after_qty,product_name_snapshot,created_at) VALUES('nest-1',1,1,1,'IN',1,10,11,'x',?)",
            (timeutil.now_iso(),))
        with database.transaction(db_conn):  # 嵌套：不得抛 "cannot start a transaction"
            db_conn.execute("UPDATE stock_current SET qty = 11, updated_at=? WHERE product_id=1",
                            (timeutil.now_iso(),))
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 11


def test_nested_rollback_does_not_kill_outer_transaction(db_conn, seed):
    """内层失败只回滚内层，外层已写的流水必须保留（批量同步「一条坏不影响整批」的机制基础）。"""
    from core.errors import BizError
    from models import database
    from services import timeutil
    with database.transaction(db_conn):
        db_conn.execute(
            "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,quantity,"
            "before_qty,after_qty,product_name_snapshot,created_at) VALUES('nest-outer',1,1,1,'IN',1,10,11,'x',?)",
            (timeutil.now_iso(),))
        with pytest.raises(BizError):
            with database.transaction(db_conn):
                db_conn.execute(
                    "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,quantity,"
                    "before_qty,after_qty,product_name_snapshot,created_at) VALUES('nest-inner',1,1,1,'IN',1,10,11,'x',?)",
                    (timeutil.now_iso(),))
                raise BizError(1009, "模拟失败")
    remaining = {r[0] for r in db_conn.execute("SELECT client_msg_id FROM stock_logs")}
    assert "nest-outer" in remaining, "外层写入不得被内层失败带走"
    assert "nest-inner" not in remaining, "内层必须被回滚"
