"""通用操作单测试（P4 验收） —— docs/13 第 4.4 / 4.6 节

覆盖五种 op_type 的过账方向、调拨双流水同事务、冲销（反向流水 + 状态）、
以及「单据不写库存、只走过账出口」这条铁律的可观测后果。
"""
import pytest

from core.errors import BizError

ADMIN = {"id": 1}
OPERATOR = {"id": 2}
RETURN_DATE = "2026-11-01T09:00:00+08:00"


def _qty(db_conn, warehouse_id: int = 1, product_id: int = 1) -> int:
    row = db_conn.execute(
        "SELECT qty FROM stock_current WHERE product_id=? AND warehouse_id=?",
        (product_id, warehouse_id)).fetchone()
    return int(row["qty"]) if row else 0


def _second_warehouse(db_conn, name: str = "二号仓") -> int:
    from services import timeutil
    now = timeutil.now_iso()
    cur = db_conn.execute(
        "INSERT INTO warehouses(name,created_at,updated_at) VALUES(?,?,?)", (name, now, now))
    return int(cur.lastrowid)


def _create(app, op_type: str, qty: int = 3, **kw) -> dict:
    from services import operation_service
    payload = {"name": f"{op_type}单", "op_type": op_type, "warehouse_id": 1,
               "lines": [{"product_id": 1, "qty": qty}], **kw}
    with app.app_context():
        return operation_service.create_order(payload, ADMIN)


def _approve(app, order_id: int) -> dict:
    from services import operation_service
    with app.app_context():
        operation_service.submit_order(order_id, ADMIN)
        return operation_service.review_order(order_id, {"action": "APPROVE"}, ADMIN)


def _ref_rows(db_conn, ref_type: str) -> list:
    return db_conn.execute(
        "SELECT * FROM stock_logs WHERE ref_type=? ORDER BY id", (ref_type,)).fetchall()


# ---------- 1. 五种类型的过账方向 ----------

@pytest.mark.parametrize("op_type,delta", [
    ("RECEIVE", -3),        # 领用 = 出库
    ("BORROW", -3),         # 借用 = 出库
    ("RETURN_STOCK", 3),    # 退库 = 入库
    ("RETURN_BORROW", 3),   # 归还 = 入库
])
def test_simple_types_post_correct_direction(app, db_conn, seed, op_type, delta):
    extra = {"expect_return_date": RETURN_DATE} if op_type == "BORROW" else {}
    order = _create(app, op_type, 3, **extra)
    approved = _approve(app, order["id"])
    assert approved["status"] == "DONE" and approved["posted_at"]
    assert _qty(db_conn) == 10 + delta
    logs = _ref_rows(db_conn, op_type)
    assert len(logs) == 1 and logs[0]["ref_id"] == order["id"]
    assert logs[0]["type"] == ("OUT" if delta < 0 else "IN")


def test_transfer_writes_two_logs_in_one_transaction(app, db_conn, seed):
    target = _second_warehouse(db_conn)
    order = _create(app, "TRANSFER", 3, to_warehouse_id=target)
    assert order["code"].startswith("GO")
    _approve(app, order["id"])
    assert _qty(db_conn, 1) == 7 and _qty(db_conn, target) == 3
    out_logs, in_logs = _ref_rows(db_conn, "TRANSFER_OUT"), _ref_rows(db_conn, "TRANSFER_IN")
    assert len(out_logs) == 1 and len(in_logs) == 1
    assert out_logs[0]["ref_id"] == in_logs[0]["ref_id"] == order["id"]
    assert (out_logs[0]["type"], in_logs[0]["type"]) == ("OUT", "IN")


def test_transfer_rolls_back_entirely_when_target_disabled(app, db_conn, seed):
    """调拨过账：目标仓中途停用 → 源仓扣减也必须回滚（绝不允许库存凭空消失）。"""
    target = _second_warehouse(db_conn)
    order = _create(app, "TRANSFER", 3, to_warehouse_id=target)
    from services import operation_service
    with app.app_context():
        operation_service.submit_order(order["id"], ADMIN)
        db_conn.execute("UPDATE warehouses SET is_active=0 WHERE id=?", (target,))
        with pytest.raises(BizError) as exc:
            operation_service.review_order(order["id"], {"action": "APPROVE"}, ADMIN)
    assert exc.value.code == 1008
    assert _qty(db_conn, 1) == 10, "源仓扣减必须回滚"
    assert _ref_rows(db_conn, "TRANSFER_OUT") == []


def test_insufficient_stock_rejects_whole_bill(app, db_conn, seed):
    order = _create(app, "RECEIVE", 99)
    from services import operation_service
    with app.app_context():
        operation_service.submit_order(order["id"], ADMIN)
        with pytest.raises(BizError) as exc:
            operation_service.review_order(order["id"], {"action": "APPROVE"}, ADMIN)
    assert exc.value.code == 1009
    assert _qty(db_conn) == 10
    assert _ref_rows(db_conn, "RECEIVE") == []


# ---------- 2. 单头形态校验 ----------

def test_borrow_requires_expect_return_date(app, db_conn, seed):
    from services import operation_service
    with app.app_context(), pytest.raises(BizError) as exc:
        operation_service.create_order(
            {"name": "借用", "op_type": "BORROW", "warehouse_id": 1,
             "lines": [{"product_id": 1, "qty": 1}]}, ADMIN)
    assert exc.value.code == 1002


def test_transfer_requires_distinct_target(app, db_conn, seed):
    from services import operation_service
    with app.app_context(), pytest.raises(BizError) as exc:
        operation_service.create_order(
            {"name": "调拨", "op_type": "TRANSFER", "warehouse_id": 1, "to_warehouse_id": 1,
             "lines": [{"product_id": 1, "qty": 1}]}, ADMIN)
    assert exc.value.code == 1002


def test_non_transfer_rejects_target_warehouse(app, db_conn, seed):
    target = _second_warehouse(db_conn)
    from services import operation_service
    with app.app_context(), pytest.raises(BizError) as exc:
        operation_service.create_order(
            {"name": "领用", "op_type": "RECEIVE", "warehouse_id": 1, "to_warehouse_id": target,
             "lines": [{"product_id": 1, "qty": 1}]}, ADMIN)
    assert exc.value.code == 1002


def test_unknown_op_type_is_rejected(app, db_conn, seed):
    from services import operation_service
    with app.app_context(), pytest.raises(BizError) as exc:
        operation_service.create_order(
            {"name": "乱写", "op_type": "SCRAP", "warehouse_id": 1,
             "lines": [{"product_id": 1, "qty": 1}]}, ADMIN)
    assert exc.value.code == 1002


# ---------- 3. 冲销 ----------

def test_reverse_restores_stock_and_marks_order(app, db_conn, seed):
    order = _create(app, "RECEIVE", 4)
    _approve(app, order["id"])
    assert _qty(db_conn) == 6
    from services import operation_service
    with app.app_context():
        reversed_order = operation_service.reverse_order(
            order["id"], {"reason": "领错了部门"}, ADMIN)
    assert reversed_order["status"] == "REVERSED" and reversed_order["reversed_at"]
    assert _qty(db_conn) == 10, "冲销必须把库存还原"
    origin = _ref_rows(db_conn, "RECEIVE")[0]
    assert origin["reversed_by"] is not None, "原流水必须回写冲销指针"
    reversal = db_conn.execute(
        "SELECT * FROM stock_logs WHERE remark LIKE 'REVERSAL_OF:%'").fetchone()
    assert reversal["type"] == "IN", "领用（OUT）的冲销必须是 IN"


def test_reverse_transfer_restores_both_warehouses(app, db_conn, seed):
    target = _second_warehouse(db_conn)
    order = _create(app, "TRANSFER", 3, to_warehouse_id=target)
    _approve(app, order["id"])
    from services import operation_service
    with app.app_context():
        operation_service.reverse_order(order["id"], {"reason": "调拨取消"}, ADMIN)
    assert _qty(db_conn, 1) == 10 and _qty(db_conn, target) == 0


def test_double_reverse_is_rejected(app, db_conn, seed):
    order = _create(app, "RECEIVE", 4)
    _approve(app, order["id"])
    from services import operation_service
    with app.app_context():
        operation_service.reverse_order(order["id"], {"reason": "第一次"}, ADMIN)
        with pytest.raises(BizError) as exc:
            operation_service.reverse_order(order["id"], {"reason": "第二次"}, ADMIN)
    assert exc.value.code == 1014


def test_unposted_order_cannot_be_reversed(app, db_conn, seed):
    order = _create(app, "RECEIVE", 2)
    from services import operation_service
    with app.app_context(), pytest.raises(BizError) as exc:
        operation_service.reverse_order(order["id"], {"reason": "还没过账"}, ADMIN)
    assert exc.value.code == 1014


def test_posted_order_cannot_be_edited(app, db_conn, seed):
    order = _create(app, "RETURN_STOCK", 2)
    _approve(app, order["id"])
    from services import operation_service
    with app.app_context(), pytest.raises(BizError) as exc:
        operation_service.update_order(order["id"], {"name": "偷偷改"}, ADMIN)
    assert exc.value.code == 1014


# ---------- 4. 接口与权限 ----------

def _headers(token):
    return {"X-Auth-Token": token}


def test_api_operator_cannot_approve(client, admin_token, operator_token, db_conn, seed):
    created = client.post("/api/wms/operation-orders",
                          json={"name": "领用", "op_type": "RECEIVE", "warehouse_id": 1,
                                "lines": [{"product_id": 1, "qty": 1}]},
                          headers=_headers(operator_token)).get_json()["data"]
    client.post(f"/api/wms/operation-orders/{created['id']}/submit", json={},
                headers=_headers(operator_token))
    r = client.post(f"/api/wms/operation-orders/{created['id']}/approve",
                    json={"action": "APPROVE"}, headers=_headers(operator_token))
    assert r.status_code == 403 and r.get_json()["code"] == 1004


def test_api_list_filters_by_type(client, admin_token, db_conn, seed):
    for op_type in ("RECEIVE", "RETURN_STOCK"):
        client.post("/api/wms/operation-orders",
                    json={"name": op_type, "op_type": op_type, "warehouse_id": 1,
                          "lines": [{"product_id": 1, "qty": 1}]},
                    headers=_headers(admin_token))
    r = client.get("/api/wms/operation-orders?op_type=RECEIVE", headers=_headers(admin_token))
    data = r.get_json()["data"]
    assert data["total"] == 1 and data["list"][0]["op_type"] == "RECEIVE"
