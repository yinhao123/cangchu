"""采购订单 + 采购入库 + 过账（P2/P3 验收） —— docs/13 第 4.2 / 4.3 / 4.6 节

重点验证「单据 → 账本」这条链路：审批通过必须真的加库存、流水带 ref、
订单可收量回写正确、超收整体回滚（docs/13 第 2 章铁律）。
"""
import pytest

from core.errors import BizError

ADMIN = {"id": 1}
OPERATOR = {"id": 2}


def _supplier(db_conn) -> int:
    """同一测试里可能开多张单，供应商只建一次（(name,type) 唯一）。"""
    from services import timeutil
    now = timeutil.now_iso()
    db_conn.execute(
        "INSERT OR IGNORE INTO partners(name,type,created_at,updated_at) VALUES('甲供应商','SUPPLIER',?,?)",
        (now, now))
    return int(db_conn.execute("SELECT id FROM partners WHERE type='SUPPLIER'").fetchone()["id"])


def _order_payload(db_conn, qty: int = 5, price: float = 2.5) -> dict:
    return {"name": "10月首批采购", "partner_id": _supplier(db_conn),
            "lines": [{"product_id": 1, "qty": qty, "price": price}]}


def _create_order(app, db_conn, payload) -> dict:
    from services import purchase_service
    with app.app_context():
        return purchase_service.create_order(payload, ADMIN)


def _approve_order(app, order_id: int) -> dict:
    from services import purchase_service
    with app.app_context():
        purchase_service.submit_order(order_id, ADMIN)
        return purchase_service.review_order(order_id, {"action": "APPROVE"}, ADMIN)


def _detail_id(app, order_id: int) -> int:
    from services import purchase_service
    with app.app_context():
        return purchase_service.get_order(order_id)["details"][0]["id"]


def _inbound(app, order_id: int, detail_id: int, qty: int) -> dict:
    from services import purchase_service
    with app.app_context():
        return purchase_service.create_inbound(
            {"name": "入库单", "order_id": order_id, "warehouse_id": 1,
             "lines": [{"product_id": 1, "qty": qty, "price": 2.5,
                        "order_detail_id": detail_id}]}, ADMIN)


# ---------- 订单：编号 / 金额 / 状态机 ----------

def test_create_order_generates_code_and_totals(app, db_conn, seed):
    order = _create_order(app, db_conn, _order_payload(db_conn, qty=6, price=3.0))
    assert order["code"].startswith("PO") and order["code"].endswith("0001")
    assert (order["status"], order["total_qty"], order["total_amount"]) == ("DRAFT", 6, 18.0)
    assert len(order["details"]) == 1 and order["details"][0]["received_qty"] == 0


def test_order_code_increments_within_day(app, db_conn, seed):
    first = _create_order(app, db_conn, _order_payload(db_conn))
    second = _create_order(app, db_conn, _order_payload(db_conn))
    assert first["code"][-4:] == "0001" and second["code"][-4:] == "0002"


def test_order_requires_supplier_partner(app, db_conn, seed):
    from services import purchase_service
    with app.app_context(), pytest.raises(BizError) as exc:
        purchase_service.create_order(
            {"name": "错单位", "partner_id": 1,  # seed 里的甲客户是 CUSTOMER
             "lines": [{"product_id": 1, "qty": 1, "price": 1}]}, ADMIN)
    assert exc.value.code == 1002


def test_empty_lines_is_rejected(app, db_conn, seed):
    from services import purchase_service
    with app.app_context(), pytest.raises(BizError) as exc:
        purchase_service.create_order({"name": "空单", "lines": []}, ADMIN)
    assert exc.value.code == 1015


def test_submit_then_double_submit_is_rejected(app, db_conn, seed):
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn))
    with app.app_context():
        purchase_service.submit_order(order["id"], ADMIN)
        with pytest.raises(BizError) as exc:
            purchase_service.submit_order(order["id"], ADMIN)
    assert exc.value.code == 1014


def test_reject_requires_reason(app, db_conn, seed):
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn))
    with app.app_context():
        purchase_service.submit_order(order["id"], ADMIN)
        with pytest.raises(BizError) as exc:
            purchase_service.review_order(order["id"], {"action": "REJECT"}, ADMIN)
    assert exc.value.code == 1002


def test_approved_order_can_be_edited_again(app, db_conn, seed):
    """已审批订单不允许再改（要改先取消），否则审批形同虚设。"""
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn))
    _approve_order(app, order["id"])
    with app.app_context(), pytest.raises(BizError) as exc:
        purchase_service.update_order(order["id"], {"name": "偷偷改"}, ADMIN)
    assert exc.value.code == 1014


# ---------- 入库单：只能引用已审批订单 ----------

def test_inbound_requires_approved_order(app, db_conn, seed):
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn))
    with app.app_context(), pytest.raises(BizError) as exc:
        purchase_service.create_inbound(
            {"name": "入库单", "order_id": order["id"], "warehouse_id": 1,
             "lines": [{"product_id": 1, "qty": 1, "price": 1}]}, ADMIN)
    assert exc.value.code == 1014


def test_inbound_to_disabled_warehouse_is_rejected(app, db_conn, seed):
    from services import purchase_service
    with app.app_context():
        db_conn.execute("UPDATE warehouses SET is_active=0 WHERE id=1")
        with pytest.raises(BizError) as exc:
            purchase_service.create_inbound(
                {"name": "入库单", "warehouse_id": 1,
                 "lines": [{"product_id": 1, "qty": 1, "price": 1}]}, ADMIN)
    assert exc.value.code == 1008


# ---------- 过账：单据 → 账本 ----------

def test_approve_inbound_posts_stock_and_writes_ref(app, db_conn, seed):
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn, qty=5))
    _approve_order(app, order["id"])
    inbound = _inbound(app, order["id"], _detail_id(app, order["id"]), 5)
    with app.app_context():
        purchase_service.submit_inbound(inbound["id"], ADMIN)
        purchase_service.review_inbound(inbound["id"], {"action": "APPROVE"}, ADMIN)

    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 15
    log = db_conn.execute(
        "SELECT * FROM stock_logs WHERE ref_type='PURCHASE_IN'").fetchone()
    assert log["ref_id"] == inbound["id"] and log["type"] == "IN" and log["quantity"] == 5
    assert log["product_name_snapshot"] == "测试可乐", "过账也必须写名称快照"


def test_approved_inbound_bumps_received_qty(app, db_conn, seed):
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn, qty=5))
    _approve_order(app, order["id"])
    inbound = _inbound(app, order["id"], _detail_id(app, order["id"]), 3)
    with app.app_context():
        purchase_service.submit_inbound(inbound["id"], ADMIN)
        purchase_service.review_inbound(inbound["id"], {"action": "APPROVE"}, ADMIN)
        fresh = purchase_service.get_order(order["id"])
    assert fresh["details"][0]["received_qty"] == 3
    assert fresh["status"] == "APPROVED", "未收满不关闭"


def test_order_closes_when_fully_received(app, db_conn, seed):
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn, qty=5))
    _approve_order(app, order["id"])
    did = _detail_id(app, order["id"])
    for qty in (3, 2):
        inbound = _inbound(app, order["id"], did, qty)
        with app.app_context():
            purchase_service.submit_inbound(inbound["id"], ADMIN)
            purchase_service.review_inbound(inbound["id"], {"action": "APPROVE"}, ADMIN)
    with app.app_context():
        assert purchase_service.get_order(order["id"])["status"] == "CLOSED"


def test_over_receiving_is_rejected_and_rolls_back(app, db_conn, seed):
    """超收必须整体回滚：既不多加库存，也不产生流水。"""
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn, qty=5))
    _approve_order(app, order["id"])
    did = _detail_id(app, order["id"])
    with app.app_context(), pytest.raises(BizError) as exc:   # 建单时即拦
        _inbound(app, order["id"], did, 6)
    assert exc.value.code == 1016
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 10


def test_posted_inbound_cannot_be_modified(app, db_conn, seed):
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn, qty=5))
    _approve_order(app, order["id"])
    inbound = _inbound(app, order["id"], _detail_id(app, order["id"]), 5)
    with app.app_context():
        purchase_service.submit_inbound(inbound["id"], ADMIN)
        purchase_service.review_inbound(inbound["id"], {"action": "APPROVE"}, ADMIN)
        with pytest.raises(BizError) as exc:
            purchase_service.update_inbound(inbound["id"], {"name": "改一下"}, ADMIN)
    assert exc.value.code == 1014


def test_repeated_approval_does_not_double_post(app, db_conn, seed):
    """重复点「通过」：第二次被状态机拦下，库存只加一次。"""
    from services import purchase_service
    order = _create_order(app, db_conn, _order_payload(db_conn, qty=5))
    _approve_order(app, order["id"])
    inbound = _inbound(app, order["id"], _detail_id(app, order["id"]), 5)
    with app.app_context():
        purchase_service.submit_inbound(inbound["id"], ADMIN)
        purchase_service.review_inbound(inbound["id"], {"action": "APPROVE"}, ADMIN)
        with pytest.raises(BizError) as exc:
            purchase_service.review_inbound(inbound["id"], {"action": "APPROVE"}, ADMIN)
    assert exc.value.code == 1014
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 15


# ---------- 接口层与权限 ----------

def _headers(token):
    return {"X-Auth-Token": token}


def test_api_create_and_list_orders(client, admin_token, db_conn, seed):
    payload = _order_payload(db_conn, qty=4)
    r = client.post("/api/wms/purchase-orders", json=payload, headers=_headers(admin_token))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["data"]
    assert body["status"] == "DRAFT"

    r = client.get("/api/wms/purchase-orders", headers=_headers(admin_token))
    data = r.get_json()["data"]
    assert (data["total"], data["page_size"], data["has_more"]) == (1, 20, False)


def test_api_approve_requires_admin(client, admin_token, operator_token, db_conn, seed):
    r = client.post("/api/wms/purchase-orders", json=_order_payload(db_conn),
                    headers=_headers(admin_token))
    order_id = r.get_json()["data"]["id"]
    client.post(f"/api/wms/purchase-orders/{order_id}/submit", json={},
                headers=_headers(operator_token))
    r = client.post(f"/api/wms/purchase-orders/{order_id}/approve", json={"action": "APPROVE"},
                    headers=_headers(operator_token))
    assert r.status_code == 403 and r.get_json()["code"] == 1004


def test_api_full_inbound_flow(client, admin_token, db_conn, seed):
    created = client.post("/api/wms/purchase-orders", json=_order_payload(db_conn, qty=5),
                          headers=_headers(admin_token)).get_json()["data"]
    client.post(f"/api/wms/purchase-orders/{created['id']}/submit", json={},
                headers=_headers(admin_token))
    client.post(f"/api/wms/purchase-orders/{created['id']}/approve", json={"action": "APPROVE"},
                headers=_headers(admin_token))
    did = created["details"][0]["id"] if created.get("details") else None
    if did is None:
        did = client.get(f"/api/wms/purchase-orders/{created['id']}",
                         headers=_headers(admin_token)).get_json()["data"]["details"][0]["id"]
    inbound = client.post("/api/wms/purchase-inbounds",
                          json={"name": "入库", "order_id": created["id"], "warehouse_id": 1,
                                "lines": [{"product_id": 1, "qty": 5, "price": 2.5,
                                           "order_detail_id": did}]},
                          headers=_headers(admin_token)).get_json()["data"]
    client.post(f"/api/wms/purchase-inbounds/{inbound['id']}/submit", json={},
                headers=_headers(admin_token))
    r = client.post(f"/api/wms/purchase-inbounds/{inbound['id']}/approve",
                    json={"action": "APPROVE"}, headers=_headers(admin_token))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["posted_at"], "审批通过必须同步过账"
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 15
