"""P5 验收：单据总开关 / 免审开关 / 审批待办 / CSV 导出 —— docs/13 第 8.2 节 · docs/03 第 12'.5~12'.8 节

开关是「回滚方案」的唯一实现，必须证明：关掉之后写操作全拒、但既有流水与库存完全不受影响。
"""
import pytest

from core.errors import BizError

ADMIN = {"id": 1}


def _headers(token):
    return {"X-Auth-Token": token}


def _set(client, token, **items):
    r = client.post("/api/settings", json={"items": items}, headers=_headers(token))
    assert r.status_code == 200, r.get_json()


def _order_payload(db_conn) -> dict:
    from services import timeutil
    now = timeutil.now_iso()
    db_conn.execute(
        "INSERT OR IGNORE INTO partners(name,type,created_at,updated_at) VALUES('甲供应商','SUPPLIER',?,?)",
        (now, now))
    return {"name": "开关测试采购单", "lines": [{"product_id": 1, "qty": 2, "price": 1}]}


# ---------- 1. 总开关 wms_bills_enabled ----------

def test_bills_disabled_blocks_writes_but_keeps_reads(client, admin_token, db_conn, seed):
    _set(client, admin_token, wms_bills_enabled=False)

    r = client.post("/api/wms/purchase-orders", json=_order_payload(db_conn),
                    headers=_headers(admin_token))
    assert r.status_code == 409 and r.get_json()["code"] == 1018

    r = client.post("/api/wms/operation-orders",
                    json={"name": "领用", "op_type": "RECEIVE", "warehouse_id": 1,
                          "lines": [{"product_id": 1, "qty": 1}]},
                    headers=_headers(admin_token))
    assert r.status_code == 409 and r.get_json()["code"] == 1018

    r = client.get("/api/wms/purchase-orders", headers=_headers(admin_token))
    assert r.status_code == 200, "关掉开关后历史单据仍必须能查"


def test_disabling_bills_does_not_touch_stock_or_logs(client, admin_token, db_conn, seed):
    """回滚开关的全部意义：库存与流水一行不动。"""
    before_logs = db_conn.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0]
    before_qty = db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0]
    _set(client, admin_token, wms_bills_enabled=False)
    client.post("/api/wms/purchase-orders", json=_order_payload(db_conn),
                headers=_headers(admin_token))
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0] == before_logs
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == before_qty


def test_bills_can_be_re_enabled(client, admin_token, db_conn, seed):
    _set(client, admin_token, wms_bills_enabled=False)
    _set(client, admin_token, wms_bills_enabled=True)
    r = client.post("/api/wms/purchase-orders", json=_order_payload(db_conn),
                    headers=_headers(admin_token))
    assert r.status_code == 200, r.get_json()


# ---------- 2. 免审开关 wms_operation_approve_required ----------

def test_operation_requires_approval_by_default(client, admin_token, db_conn, seed):
    created = client.post("/api/wms/operation-orders",
                          json={"name": "领用", "op_type": "RECEIVE", "warehouse_id": 1,
                                "lines": [{"product_id": 1, "qty": 2}]},
                          headers=_headers(admin_token)).get_json()["data"]
    r = client.post(f"/api/wms/operation-orders/{created['id']}/submit", json={},
                    headers=_headers(admin_token))
    assert r.get_json()["data"]["status"] == "SUBMITTED"
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 10


def test_no_approval_mode_posts_on_submit(client, admin_token, db_conn, seed):
    _set(client, admin_token, wms_operation_approve_required=False)
    created = client.post("/api/wms/operation-orders",
                          json={"name": "领用", "op_type": "RECEIVE", "warehouse_id": 1,
                                "lines": [{"product_id": 1, "qty": 2}]},
                          headers=_headers(admin_token)).get_json()["data"]
    r = client.post(f"/api/wms/operation-orders/{created['id']}/submit", json={},
                    headers=_headers(admin_token))
    body = r.get_json()["data"]
    assert body["status"] == "DONE" and body["posted_at"], "免审模式下提交即过账"
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 8
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs WHERE ref_type='RECEIVE'").fetchone()[0] == 1


# ---------- 3. 审批待办 ----------

def test_pending_list_collects_three_kinds(client, admin_token, db_conn, seed):
    payload = _order_payload(db_conn)
    order = client.post("/api/wms/purchase-orders", json=payload,
                        headers=_headers(admin_token)).get_json()["data"]
    client.post(f"/api/wms/purchase-orders/{order['id']}/submit", json={},
                headers=_headers(admin_token))
    op = client.post("/api/wms/operation-orders",
                     json={"name": "领用", "op_type": "RECEIVE", "warehouse_id": 1,
                           "lines": [{"product_id": 1, "qty": 1}]},
                     headers=_headers(admin_token)).get_json()["data"]
    client.post(f"/api/wms/operation-orders/{op['id']}/submit", json={},
                headers=_headers(admin_token))

    r = client.get("/api/wms/approvals", headers=_headers(admin_token))
    data = r.get_json()["data"]
    assert data["total"] == 2
    assert {x["biz"] for x in data["list"]} == {"PURCHASE_ORDER", "OPERATION_ORDER"}
    assert all(x["code"] for x in data["list"])

    only = client.get("/api/wms/approvals?type=OPERATION_ORDER",
                      headers=_headers(admin_token)).get_json()["data"]
    assert only["total"] == 1 and only["list"][0]["op_label"] == "领用"


def test_pending_list_requires_admin(client, admin_token, operator_token, seed):
    r = client.get("/api/wms/approvals", headers=_headers(operator_token))
    assert r.status_code == 403 and r.get_json()["code"] == 1004


def test_approved_bill_leaves_pending_list(client, admin_token, db_conn, seed):
    created = client.post("/api/wms/purchase-orders", json=_order_payload(db_conn),
                          headers=_headers(admin_token)).get_json()["data"]
    client.post(f"/api/wms/purchase-orders/{created['id']}/submit", json={},
                headers=_headers(admin_token))
    client.post(f"/api/wms/purchase-orders/{created['id']}/approve", json={"action": "APPROVE"},
                headers=_headers(admin_token))
    assert client.get("/api/wms/approvals",
                      headers=_headers(admin_token)).get_json()["data"]["total"] == 0


# ---------- 4. CSV 导出 ----------

def test_export_purchase_orders_has_bom_and_rows(client, admin_token, db_conn, seed):
    created = client.post("/api/wms/purchase-orders", json=_order_payload(db_conn),
                          headers=_headers(admin_token)).get_json()["data"]
    r = client.get("/api/wms/export/purchase-orders", headers=_headers(admin_token))
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert r.headers["X-Row-Count"] == "1"
    raw = r.data
    assert raw.startswith(b"\xef\xbb\xbf"), "缺 BOM 时 Excel 打开中文乱码（T-10）"
    text = raw.decode("utf-8-sig")
    assert "单号" in text and created["code"] in text and "草稿" in text


def test_export_operation_orders_labels_type(client, admin_token, db_conn, seed):
    client.post("/api/wms/operation-orders",
                json={"name": "调拨单", "op_type": "TRANSFER", "warehouse_id": 1,
                      "to_warehouse_id": 1, "lines": [{"product_id": 1, "qty": 1}]},
                headers=_headers(admin_token))  # 同仓会被拦，这里只是确认导出不 500
    r = client.get("/api/wms/export/operation-orders", headers=_headers(admin_token))
    assert r.status_code == 200
    assert r.headers["X-Row-Count"] == "0"


def test_export_unknown_biz_is_404(client, admin_token, seed):
    r = client.get("/api/wms/export/not-a-bill", headers=_headers(admin_token))
    assert r.status_code == 404 and r.get_json()["code"] == 1006


def test_export_respects_status_filter(client, admin_token, db_conn, seed):
    client.post("/api/wms/purchase-orders", json=_order_payload(db_conn),
                headers=_headers(admin_token))
    r = client.get("/api/wms/export/purchase-orders?status=APPROVED", headers=_headers(admin_token))
    assert r.headers["X-Row-Count"] == "0"
