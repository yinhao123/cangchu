"""销售订单 / 发货 / 退货 / 客户对账（P1-4）—— docs/14 第 6.5 节验收项。

锁死四件事：
1. 发货数量必须让库存**精确**减少，发满自动 CLOSED，流水 ref_type='SALES_OUT'
2. 超发 409/1016 且整单回滚（库存与流水都不动）
3. 同一 shipment_id 重放幂等（不二次扣减、不二次回写 shipped_qty）
4. 退货审批生成 IN 流水，对账单净额 = 出库 − 退货
"""
import uuid

HEADER = "X-Auth-Token"


def _h(token):
    return {HEADER: token}


def _create_order(client, token, qty=3, price=None, customer_id=1, warehouse_id=1, lines=None):
    body = {
        "name": "销售测试单",
        "customer_id": customer_id,
        "warehouse_id": warehouse_id,
        "lines": lines or [{"product_id": 1, "qty": qty}],
    }
    if price is not None:
        body["lines"][0]["price"] = price
    return client.post("/api/wms/sales-orders", headers=_h(token), json=body)


def _approved_order(client, admin_token, qty=3):
    resp = _create_order(client, admin_token, qty=qty)
    assert resp.status_code == 200, resp.get_json()
    order = resp.get_json()["data"]
    client.post(f"/api/wms/sales-orders/{order['id']}/submit", headers=_h(admin_token), json={})
    approved = client.post(f"/api/wms/sales-orders/{order['id']}/approve",
                           headers=_h(admin_token), json={})
    assert approved.status_code == 200, approved.get_json()
    return approved.get_json()["data"]


def _ship(client, token, order_id, lines, shipment_id=None):
    return client.post(f"/api/wms/sales-orders/{order_id}/ship", headers=_h(token), json={
        "shipment_id": shipment_id or str(uuid.uuid4()), "lines": lines})


def _qty(db_conn, product_id=1):
    row = db_conn.execute("SELECT qty FROM stock_current WHERE product_id=? AND warehouse_id=1",
                          (product_id,)).fetchone()
    return int(row["qty"]) if row else 0


def _sales_logs(db_conn, ref_type="SALES_OUT"):
    return db_conn.execute("SELECT * FROM stock_logs WHERE ref_type=? ORDER BY id",
                           (ref_type,)).fetchall()


# ==================== 开单 / 权限 ====================

def test_create_order_defaults_price_and_totals(client, admin_token, db_conn):
    order = _create_order(client, admin_token, qty=4).get_json()["data"]

    assert order["code"].startswith("SO") and order["status"] == "DRAFT"
    assert order["customer_name_snap"] == "甲客户"
    assert order["total_qty"] == 4
    assert order["total_amount"] == 14.0          # 默认取商品售价 3.5
    assert order["details"][0]["price"] == 3.5
    assert order["details"][0]["shipped_qty"] == 0
    assert order["details"][0]["remaining_qty"] == 4


def test_supplier_cannot_be_customer(client, admin_token, db_conn):
    from services import timeutil
    now = timeutil.now_iso()
    db_conn.execute("INSERT INTO partners(name,type,created_at,updated_at) "
                    "VALUES('乙供应商','SUPPLIER',?,?)", (now, now))

    resp = _create_order(client, admin_token, customer_id=2)

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1002


def test_inactive_customer_is_rejected(client, admin_token, db_conn):
    db_conn.execute("UPDATE partners SET is_active=0 WHERE id=1")

    resp = _create_order(client, admin_token)

    assert resp.status_code == 403
    assert resp.get_json()["code"] == 1004


def test_operator_cannot_approve(client, operator_token):
    resp = _create_order(client, operator_token)
    order = resp.get_json()["data"]
    client.post(f"/api/wms/sales-orders/{order['id']}/submit", headers=_h(operator_token), json={})

    review = client.post(f"/api/wms/sales-orders/{order['id']}/approve",
                         headers=_h(operator_token), json={})

    assert review.status_code == 403


def test_approved_order_cannot_change_lines(client, admin_token):
    order = _approved_order(client, admin_token)
    detail_id = order["details"][0]["id"]

    resp = client.put(f"/api/wms/sales-orders/{order['id']}", headers=_h(admin_token), json={
        "lines": [{"product_id": 1, "qty": 9}]})

    assert resp.status_code == 409 and resp.get_json()["code"] == 1014
    assert detail_id


# ==================== 发货 ====================

def test_ship_deducts_stock_and_writes_sales_out_log(client, admin_token, db_conn):
    order = _approved_order(client, admin_token, qty=3)
    detail_id = order["details"][0]["id"]

    resp = _ship(client, admin_token, order["id"], [{"detail_id": detail_id, "qty": 1}])
    data = resp.get_json()["data"]

    assert resp.status_code == 200
    assert _qty(db_conn) == 9
    assert data["details"][0]["shipped_qty"] == 1
    assert data["details"][0]["remaining_qty"] == 2
    log = _sales_logs(db_conn)[0]
    assert (log["type"], log["quantity"], log["ref_id"]) == ("OUT", 1, order["id"])
    assert log["partner_id"] == 1


def test_partial_ship_three_times_then_closed(client, admin_token, db_conn):
    order = _approved_order(client, admin_token, qty=3)
    detail_id = order["details"][0]["id"]

    for _ in range(3):
        resp = _ship(client, admin_token, order["id"], [{"detail_id": detail_id, "qty": 1}])
        assert resp.status_code == 200, resp.get_json()

    assert _qty(db_conn) == 7
    assert len(_sales_logs(db_conn)) == 3
    final = client.get(f"/api/wms/sales-orders/{order['id']}",
                       headers=_h(admin_token)).get_json()["data"]
    assert final["status"] == "CLOSED"
    assert final["remaining_qty"] == 0


def test_overship_is_rejected_and_rolled_back(client, admin_token, db_conn):
    order = _approved_order(client, admin_token, qty=3)
    detail_id = order["details"][0]["id"]

    resp = _ship(client, admin_token, order["id"], [{"detail_id": detail_id, "qty": 4}])

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == 1016
    assert body["data"]["receivable"] == 3
    assert _qty(db_conn) == 10 and _sales_logs(db_conn) == []


def test_ship_rolls_back_when_stock_insufficient(client, admin_token, db_conn):
    order = _approved_order(client, admin_token, qty=8)
    detail_id = order["details"][0]["id"]
    db_conn.execute("UPDATE stock_current SET qty=5 WHERE product_id=1 AND warehouse_id=1")

    resp = _ship(client, admin_token, order["id"], [{"detail_id": detail_id, "qty": 8}])

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1009
    assert _qty(db_conn) == 5 and _sales_logs(db_conn) == []
    shipped = db_conn.execute("SELECT shipped_qty FROM wms_sales_order_detail WHERE id=?",
                              (detail_id,)).fetchone()[0]
    assert shipped == 0


def test_ship_is_idempotent_by_shipment_id(client, admin_token, db_conn):
    order = _approved_order(client, admin_token, qty=3)
    detail_id = order["details"][0]["id"]
    shipment_id = str(uuid.uuid4())

    first = _ship(client, admin_token, order["id"], [{"detail_id": detail_id, "qty": 2}], shipment_id)
    second = _ship(client, admin_token, order["id"], [{"detail_id": detail_id, "qty": 2}], shipment_id)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["data"]["shipment"]["duplicated"] is True
    assert _qty(db_conn) == 8
    assert len(_sales_logs(db_conn)) == 1
    shipped = db_conn.execute("SELECT shipped_qty FROM wms_sales_order_detail WHERE id=?",
                              (detail_id,)).fetchone()[0]
    assert shipped == 2


def test_ship_requires_shipment_id(client, admin_token):
    order = _approved_order(client, admin_token)
    detail_id = order["details"][0]["id"]

    resp = client.post(f"/api/wms/sales-orders/{order['id']}/ship", headers=_h(admin_token),
                       json={"lines": [{"detail_id": detail_id, "qty": 1}]})

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1002


# ==================== 退货 ====================

def test_return_posts_in_log_and_updates_statement(client, admin_token, db_conn):
    order = _approved_order(client, admin_token, qty=3)
    detail_id = order["details"][0]["id"]
    _ship(client, admin_token, order["id"], [{"detail_id": detail_id, "qty": 3}])

    created = client.post("/api/wms/sales-returns", headers=_h(admin_token), json={
        "name": "退货测试", "customer_id": 1, "warehouse_id": 1,
        "source_order_id": order["id"], "return_reason": "客户反馈包装破损",
        "lines": [{"product_id": 1, "qty": 1, "price": 3.5}],
    })
    assert created.status_code == 200, created.get_json()
    ret = created.get_json()["data"]
    assert ret["code"].startswith("SR")
    assert ret["return_reason"] == "客户反馈包装破损"

    client.post(f"/api/wms/sales-returns/{ret['id']}/submit", headers=_h(admin_token), json={})
    approved = client.post(f"/api/wms/sales-returns/{ret['id']}/approve",
                           headers=_h(admin_token), json={})

    assert approved.status_code == 200
    assert approved.get_json()["data"]["posted_at"]
    assert _qty(db_conn) == 8                       # 10 - 3 + 1
    log = _sales_logs(db_conn, "SALES_RETURN")[0]
    assert (log["type"], log["quantity"], log["ref_id"]) == ("IN", 1, ret["id"])

    statement = client.get("/api/wms/customer-statement?partner_id=1",
                           headers=_h(admin_token)).get_json()["data"]
    assert statement["totals"]["out_qty"] == 3
    assert statement["totals"]["return_qty"] == 1
    assert statement["totals"]["net_qty"] == 2
    assert statement["totals"]["net_amount"] == 10.5 - 3.5

def test_return_requires_reason(client, admin_token):
    resp = client.post("/api/wms/sales-returns", headers=_h(admin_token), json={
        "name": "无原因退货", "customer_id": 1, "warehouse_id": 1,
        "lines": [{"product_id": 1, "qty": 1}],
    })

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1002


def test_return_customer_must_own_source_order(client, admin_token, db_conn):
    from services import timeutil
    now = timeutil.now_iso()
    db_conn.execute("INSERT INTO partners(name,type,created_at,updated_at) "
                    "VALUES('丙客户','CUSTOMER',?,?)", (now, now))
    order = _approved_order(client, admin_token)

    resp = client.post("/api/wms/sales-returns", headers=_h(admin_token), json={
        "name": "串客户退货", "customer_id": 2, "warehouse_id": 1,
        "source_order_id": order["id"], "return_reason": "测试",
        "lines": [{"product_id": 1, "qty": 1}],
    })

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1002


# ==================== 对账 / 导出 / 打印 / 待办 ====================

def test_statement_csv_has_bom_and_totals(client, admin_token):
    order = _approved_order(client, admin_token, qty=2)
    _ship(client, admin_token, order["id"],
          [{"detail_id": order["details"][0]["id"], "qty": 2}])

    resp = client.get("/api/wms/customer-statement?partner_id=1&export=csv",
                      headers=_h(admin_token))

    assert resp.status_code == 200
    assert resp.data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in resp.data
    text = resp.data.decode("utf-8-sig")
    assert "甲客户" in text and "净额" in text


def test_statement_rejects_supplier_as_customer(client, admin_token, db_conn):
    from services import timeutil
    now = timeutil.now_iso()
    db_conn.execute("INSERT INTO partners(name,type,created_at,updated_at) "
                    "VALUES('丁供应商','SUPPLIER',?,?)", (now, now))

    resp = client.get("/api/wms/customer-statement?partner_id=2", headers=_h(admin_token))

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1002


def test_export_sales_orders_and_returns(client, admin_token):
    order = _approved_order(client, admin_token)
    csv_order = client.get("/api/wms/export/sales-orders", headers=_h(admin_token))
    csv_return = client.get("/api/wms/export/sales-returns", headers=_h(admin_token))

    assert csv_order.status_code == 200
    assert csv_order.data.startswith(b"\xef\xbb\xbf")
    assert order["code"].encode() in csv_order.data
    assert csv_return.status_code == 200


def test_sales_order_print(client, admin_token):
    order = _approved_order(client, admin_token, qty=2)

    data = client.get(f"/api/print/sales-orders/{order['id']}",
                      headers=_h(admin_token)).json["data"]
    doc = data["docs"][0]

    assert doc["code"] == order["code"]
    assert doc["counterparty"] == "甲客户"
    assert doc["status_label"] == "已审批"
    assert doc["totals"]["total_qty"] == 2
    assert doc["sign_off"] == ["销售经办", "仓管", "客户签收"]


def test_pending_approvals_include_sales(client, admin_token):
    order = _create_order(client, admin_token).get_json()["data"]
    client.post(f"/api/wms/sales-orders/{order['id']}/submit", headers=_h(admin_token), json={})

    data = client.get("/api/wms/approvals?type=SALES_ORDER",
                      headers=_h(admin_token)).get_json()["data"]

    assert data["total"] == 1
    assert data["list"][0]["biz"] == "SALES_ORDER"
    assert data["list"][0]["code"] == order["code"]
