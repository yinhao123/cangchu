"""业务单据打印（P0-3）—— docs/14 第 5.6 节验收项。

打印数据必须满足：合计与详情接口完全一致（同一服务端字段）、枚举是中文、签字栏由服务端下发、
批量打印一次多张、未登录不可导出。
"""
HEADER = "X-Auth-Token"


def _h(token):
    return {HEADER: token}


def _make_order(client, token, name="打印测试采购单", qty=3, price=5):
    resp = client.post("/api/wms/purchase-orders", headers=_h(token), json={
        "name": name,
        "lines": [{"product_id": 1, "qty": qty, "price": price}],
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["data"]


def _make_inbound(client, token, order_id=None):
    resp = client.post("/api/wms/purchase-inbounds", headers=_h(token), json={
        "name": "打印测试入库单",
        "warehouse_id": 1,
        "order_id": order_id,
        "lines": [{"product_id": 1, "qty": 2, "price": 5}],
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["data"]


def _make_operation(client, token, op_type="RECEIVE", to_warehouse_id=None):
    payload = {
        "name": "打印测试领用单",
        "op_type": op_type,
        "warehouse_id": 1,
        "lines": [{"product_id": 1, "qty": 1}],
    }
    if to_warehouse_id:
        payload["to_warehouse_id"] = to_warehouse_id
    resp = client.post("/api/wms/operation-orders", headers=_h(token), json=payload)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["data"]


def _print(client, token, biz, ids, **query):
    return client.get(f"/api/print/{biz}/{ids}", headers=_h(token), query_string=query)


# ==================== 元信息 ====================

def test_meta_has_company_papers_and_sign_off(client, admin_token):
    resp = client.get("/api/print/meta", headers=_h(admin_token))

    assert resp.status_code == 200
    data = resp.json["data"]
    assert data["company"]["name"]          # 未配置公司名时回退设备名，不能是空串
    assert [p["key"] for p in data["papers"]] == ["a4", "dot"]
    assert {b["key"] for b in data["biz"]} == {
        "purchase-orders", "purchase-inbounds", "operation-orders", "stocktakes",
        "sales-orders"}   # P1-4：销货单要能打给客户签收
    order_biz = next(b for b in data["biz"] if b["key"] == "purchase-orders")
    assert order_biz["sign_off"] == ["采购经办", "主管审批", "供应商确认"]
    assert data["paper"] in ("a4", "dot")


def test_company_fields_follow_settings(client, admin_token):
    client.post("/api/settings", headers=_h(admin_token),
                json={"items": {"company_name": "示例仓储", "company_phone": "010-00000000"}})

    data = client.get("/api/print/meta", headers=_h(admin_token)).json["data"]

    assert data["company"]["name"] == "示例仓储"
    assert data["company"]["phone"] == "010-00000000"
    assert data["company"]["title_from_setting"] is True


def test_print_requires_login(client):
    assert client.get("/api/print/meta").status_code == 401
    assert client.get("/api/print/purchase-orders/1").status_code == 401


# ==================== 各单据 ====================

def test_purchase_order_print_matches_detail_totals(client, admin_token):
    order = _make_order(client, admin_token, qty=3, price=5)
    detail = client.get(f"/api/wms/purchase-orders/{order['id']}",
                        headers=_h(admin_token)).json["data"]

    data = _print(client, admin_token, "purchase-orders", order["id"]).json["data"]
    doc = data["docs"][0]

    assert doc["code"] == order["code"]
    assert doc["status_label"] == "草稿"                 # 打印页不许出现英文枚举
    assert doc["totals"]["total_qty"] == detail["total_qty"] == 3
    assert doc["totals"]["total_amount"] == detail["total_amount"] == 15.0
    assert doc["lines"][0]["product_name"] == "测试可乐"
    assert doc["lines"][0]["amount"] == 15.0
    assert doc["sign_off"] == ["采购经办", "主管审批", "供应商确认"]
    assert data["company"]["name"]


def test_purchase_inbound_print(client, admin_token):
    inbound = _make_inbound(client, admin_token)

    doc = _print(client, admin_token, "purchase-inbounds", inbound["id"]).json["data"]["docs"][0]

    assert doc["warehouse"] == "主仓库"
    assert doc["date_label"] == "入库日期"
    assert doc["totals"]["total_qty"] == inbound["total_qty"] == 2
    assert doc["sign_off"] == ["仓管", "送货人", "复核"]


def test_operation_print_sign_off_depends_on_op_type(client, admin_token, db_conn):
    from services import timeutil
    now = timeutil.now_iso()
    db_conn.execute("INSERT INTO warehouses(name,created_at,updated_at) VALUES('二号仓',?,?)", (now, now))
    receive = _make_operation(client, admin_token, "RECEIVE")
    transfer = _make_operation(client, admin_token, "TRANSFER", to_warehouse_id=2)

    receive_doc = _print(client, admin_token, "operation-orders", receive["id"]).json["data"]["docs"][0]
    transfer_doc = _print(client, admin_token, "operation-orders", transfer["id"]).json["data"]["docs"][0]

    assert receive_doc["sign_off"] == ["领用人", "仓管"]
    assert transfer_doc["sign_off"] == ["调出仓管", "调入仓管"]
    assert receive_doc["op_label"] == "领用"


def test_stocktake_blank_and_diff_print(client, admin_token):
    created = client.post("/api/wms/stocktakes", headers=_h(admin_token),
                          json={"name": "打印盘点", "warehouse_id": 1})
    doc_id = created.json["data"]["id"]
    detail_id = created.json["data"]["details"][0]["id"]
    client.put(f"/api/wms/stocktakes/{doc_id}", headers=_h(admin_token),
               json={"lines": [{"detail_id": detail_id, "real_qty": 7}]})

    blank = _print(client, admin_token, "stocktakes", doc_id, mode="blank").json["data"]["docs"][0]
    diff = _print(client, admin_token, "stocktakes", doc_id, mode="diff").json["data"]["docs"][0]

    assert blank["kind"] == "stocktake"
    assert blank["lines"][0]["real_qty"] is None          # 空白盘点表：实盘列留空供手填
    assert blank["sign_off"] == ["盘点人", "复核人"]
    assert diff["lines"][0]["real_qty"] == 7 and diff["lines"][0]["diff_qty"] == -3
    assert diff["summary"][2] == {"label": "差异行数", "value": 1}


# ==================== 批量与异常 ====================

def test_batch_print_two_documents(client, admin_token):
    first = _make_order(client, admin_token, name="批量一")
    second = _make_order(client, admin_token, name="批量二")

    data = _print(client, admin_token, "purchase-orders",
                  f"{first['id']},{second['id']}").json["data"]

    assert data["total"] == 2
    assert [d["code"] for d in data["docs"]] == [first["code"], second["code"]]


def test_batch_limit_and_bad_input(client, admin_token):
    too_many = ",".join(str(index) for index in range(1, 22))
    assert _print(client, admin_token, "purchase-orders", too_many).status_code == 422
    assert _print(client, admin_token, "purchase-orders", "abc").status_code == 422
    assert _print(client, admin_token, "purchase-orders", "").status_code == 404  # 路径不匹配


def test_unknown_biz_and_missing_id(client, admin_token):
    bad_biz = _print(client, admin_token, "invoices", "1")
    assert bad_biz.status_code == 404 and bad_biz.json["code"] == 1006

    missing = _print(client, admin_token, "purchase-orders", "9999")
    assert missing.status_code == 404 and missing.json["code"] == 1006


def test_operator_can_print(client, operator_token):
    """打印是只读动作，操作员也要能打（现场签字的就是他们）。"""
    resp = client.get("/api/print/meta", headers=_h(operator_token))

    assert resp.status_code == 200
    order_biz = next(b for b in resp.json["data"]["biz"] if b["key"] == "purchase-orders")
    assert order_biz["sign_off"] == ["采购经办", "主管审批", "供应商确认"]
