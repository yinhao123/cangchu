"""单据层地基测试（P1 验收） —— docs/13 第 4.1 / 4.5 节 · 迁移 0002

覆盖：
  1. 0002 迁移真的加上了列（仓库两级树 + 流水溯源）
  2. 仓库树校验：父级不存在 / 库位下再挂 / 有子库位禁止停用
  3. post_bill_lines：溯源落库、库存同步、幂等、整单原子回滚
对应 docs/13 第 8.3 节验收清单前 4 项。
"""
import uuid

import pytest

from core.errors import BizError


def _uid() -> str:
    return str(uuid.uuid4())


def _cols(conn, table: str) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


# ---------- 1. 迁移 0002 ----------

def test_migration_0002_adds_columns(db_conn):
    assert {"code", "parent_id", "level", "category"} <= _cols(db_conn, "warehouses")
    assert {"ref_type", "ref_id"} <= _cols(db_conn, "stock_logs")


def test_existing_warehouse_is_top_level(db_conn, seed):
    """历史行必须自动落到「顶级仓库」，不能因为加列变成孤儿。"""
    from models import warehouse_repo
    row = warehouse_repo.get(db_conn, 1)
    assert (row["parent_id"], row["level"]) == (0, 1)


def test_schema_meta_version_matches_latest_migration(db_conn):
    from core.migrator import _default_dir, _files
    latest = max(v for v, _ in _files(_default_dir()))
    assert db_conn.execute("SELECT MAX(version) FROM schema_meta").fetchone()[0] == latest


# ---------- 2. 仓库两级树 ----------

def test_create_child_location(app, db_conn, seed):
    from services import warehouse_service
    with app.app_context():
        child = warehouse_service.create({"name": "A区-01", "parent_id": 1})
    assert (child["parent_id"], child["level"]) == (1, 2)


def test_create_with_unknown_parent_is_rejected(app, db_conn, seed):
    from services import warehouse_service
    with app.app_context(), pytest.raises(BizError) as exc:
        warehouse_service.create({"name": "孤儿库位", "parent_id": 999})
    assert exc.value.code == 1017


def test_third_level_is_rejected(app, db_conn, seed):
    """两级封顶：库位（level=2）不能再挂下级。"""
    from services import warehouse_service
    with app.app_context():
        child = warehouse_service.create({"name": "A区-01", "parent_id": 1})
        with pytest.raises(BizError) as exc:
            warehouse_service.create({"name": "货架-1", "parent_id": child["id"]})
    assert exc.value.code == 1017


def test_reparent_existing_warehouse_is_rejected(app, db_conn, seed):
    """改挂上级会让库存归属说不清 —— 直接拒绝（要调整请新建）。"""
    from services import warehouse_service
    with app.app_context():
        warehouse_service.create({"name": "二号仓"})
        with pytest.raises(BizError) as exc:
            warehouse_service.update(1, {"parent_id": 2})
    assert exc.value.code == 1017


def test_deactivate_warehouse_with_children_is_rejected(app, db_conn, seed):
    """有子库位的仓库禁止停用（否则子库位变孤儿）。用空仓库测，避免撞上「有库存」那条 1010。"""
    from services import warehouse_service
    with app.app_context():
        parent = warehouse_service.create({"name": "二号仓"})  # 无库存
        warehouse_service.create({"name": "B区-01", "parent_id": parent["id"]})
        with pytest.raises(BizError) as exc:
            warehouse_service.update(parent["id"], {"is_active": False})
    assert exc.value.code == 1017


def test_deactivate_with_stock_still_wins_over_children(app, db_conn, seed):
    """两条规则同时命中时，「有库存」优先：库存无处可去是更严重的问题。"""
    from services import warehouse_service
    with app.app_context():
        warehouse_service.create({"name": "A区-01", "parent_id": 1})
        with pytest.raises(BizError) as exc:
            warehouse_service.update(1, {"is_active": False})
    assert exc.value.code == 1010


def test_duplicate_code_is_rejected(app, db_conn, seed):
    from services import warehouse_service
    with app.app_context():
        warehouse_service.update(1, {"code": "WH01"})
        warehouse_service.create({"name": "二号仓"})
        with pytest.raises(BizError) as exc:
            warehouse_service.create({"name": "三号仓", "code": "WH01"})
    assert exc.value.code == 1010


def test_list_warehouses_returns_child_count(app, db_conn, seed):
    from services import warehouse_service
    with app.app_context():
        warehouse_service.create({"name": "A区-01", "parent_id": 1})
        data = warehouse_service.list_warehouses()
    parent = next(x for x in data["list"] if x["id"] == 1)
    assert parent["child_count"] == 1


# ---------- 3. 单据过账出口 ----------

def _line(**kw) -> dict:
    base = {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 3,
            "direction": "IN"}
    base.update(kw)
    return base


def test_post_bill_lines_writes_ref_and_moves_stock(app, db_conn, seed):
    from services import stock_service
    with app.app_context():
        views = stock_service.post_bill_lines([_line()], {"id": 1}, "PURCHASE_IN", 1)
    assert views[0]["duplicated"] is False
    row = db_conn.execute("SELECT * FROM stock_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert (row["ref_type"], row["ref_id"], row["type"]) == ("PURCHASE_IN", 1, "IN")
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 13


def test_post_bill_lines_is_idempotent(app, db_conn, seed):
    """重复过账（同 client_msg_id）不得重复加库存 —— 单据重试的兜底。"""
    from services import stock_service
    msg = _uid()
    with app.app_context():
        stock_service.post_bill_lines([_line(client_msg_id=msg)], {"id": 1}, "PURCHASE_IN", 1)
        again = stock_service.post_bill_lines([_line(client_msg_id=msg)], {"id": 1}, "PURCHASE_IN", 1)
    assert again[0]["duplicated"] is True
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 13
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0] == 2  # seed 一条 + 本次一条


def test_post_bill_lines_rolls_back_whole_bill(app, db_conn, seed):
    """整单原子：第二行库存不足 → 第一行也必须不生效（绝不留半截单据）。"""
    from services import stock_service
    with app.app_context(), pytest.raises(BizError) as exc:
        stock_service.post_bill_lines(
            [_line(quantity=10, direction="OUT"), _line(quantity=1, direction="OUT")],
            {"id": 1}, "RECEIVE", 7)
    assert exc.value.code == 1009
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 10
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs WHERE ref_type='RECEIVE'").fetchone()[0] == 0


def test_post_bill_lines_rejects_unknown_ref_type(app, db_conn, seed):
    from services import stock_service
    with app.app_context(), pytest.raises(BizError) as exc:
        stock_service.post_bill_lines([_line()], {"id": 1}, "NOT_A_BILL", 1)
    assert exc.value.code == 1002


def test_post_bill_lines_requires_client_msg_id(app, db_conn, seed):
    from services import stock_service
    line = _line()
    line.pop("client_msg_id")
    with app.app_context(), pytest.raises(BizError) as exc:
        stock_service.post_bill_lines([line], {"id": 1}, "PURCHASE_IN", 1)
    assert exc.value.code == 1002


def test_ref_columns_are_consistent(app, db_conn, seed):
    """docs/02 不变式检查 5：ref_type 与 ref_id 必须同有同无。"""
    from services import stock_service
    with app.app_context():
        stock_service.post_bill_lines([_line()], {"id": 1}, "PURCHASE_IN", 1)
    bad = db_conn.execute(
        "SELECT id FROM stock_logs WHERE (ref_type IS NULL) <> (ref_id IS NULL)").fetchall()
    assert bad == []
