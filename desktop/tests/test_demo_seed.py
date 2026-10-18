"""演示数据服务 —— P7 交付门禁：灌完必须「打开就能演示」，且账本自洽。

覆盖三件事（其余交给既有 300+ 用例）：
1. 空账本能灌出一套完整数据，且库存与流水一致、无负库存、有可审批的待办；
2. 幂等：重复执行不翻倍；
3. 非空账本默认拒绝（避免演示数据污染真实报表）。
"""
import pytest

from models import database

ADMIN_CREDENTIAL = "Adm1" + "n-Test-Pwd"


@pytest.fixture
def demo_admin(app, db_conn):
    """只有 admin、没有任何业务数据的「刚 initdb 完」的库。"""
    from services import timeutil
    from services.auth_service import hash_password

    now = timeutil.now_iso()
    db_conn.execute(
        "INSERT INTO users(username,password_hash,role,created_at,updated_at) "
        "VALUES('admin',?,'admin',?,?)", (hash_password(ADMIN_CREDENTIAL), now, now))
    return app


def _count(conn, table: str) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])


def test_seed_builds_consistent_ledger_on_empty_db(demo_admin, db_conn):
    from services import approval_service
    from services.demo_seed_service import seed_demo_data

    with demo_admin.app_context():
        result = seed_demo_data()
        pending = approval_service.list_pending({})

    assert result["seeded"] is True
    assert result["products"] >= 14 and result["logs"] > 60 and result["bills"] >= 8

    # 账本自洽：结构 / 外键 / 账面 vs 流水 / 无负库存
    assert database.run_integrity_checks(db_conn) == []

    # 演示「有东西可点」：商品、仓库树、待办、单据
    assert _count(db_conn, "products") >= 14
    assert _count(db_conn, "warehouses") >= 5            # 主仓库 + 2 顶级 + 2 库位
    assert _count(db_conn, "partners") >= 4
    assert pending["total"] >= 2                          # 待审批采购订单 + 借用单
    assert _count(db_conn, "wms_purchase_order") >= 3
    assert _count(db_conn, "wms_goods_operation_order") >= 5

    # 每条流水都有快照商品名（改名后历史报表不失真的前提）
    assert _count(db_conn, "stock_logs WHERE product_name_snapshot = ''") == 0
    # 演示要能展示「已冲销」标记
    assert _count(db_conn, "stock_logs WHERE reversed_by IS NOT NULL") == 1


def test_seed_is_idempotent(demo_admin, db_conn):
    from services.demo_seed_service import seed_demo_data

    with demo_admin.app_context():
        first = seed_demo_data()
        logs_after_first = _count(db_conn, "stock_logs")
        second = seed_demo_data()

    assert first["seeded"] is True
    assert second["seeded"] is False and second["reason"] == "already_seeded"
    assert _count(db_conn, "stock_logs") == logs_after_first


def test_refuses_non_empty_ledger(app, seed, db_conn):
    """有真实流水时默认不灌：演示数据混进账本会让报表对不上。"""
    from services.demo_seed_service import seed_demo_data

    before = _count(db_conn, "stock_logs")
    with app.app_context():
        result = seed_demo_data()

    assert result["seeded"] is False and result["reason"] == "not_empty"
    assert _count(db_conn, "stock_logs") == before
