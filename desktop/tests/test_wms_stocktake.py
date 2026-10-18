"""盘点单（P0-2）—— docs/14 第 4.10 节验收项。

重点锁死的三件事：
1. 快照口径：建单 `book_qty` == 建单时刻 `stock_current.qty`
2. 过账口径：`quantity = 实盘 - 实时当前`（B 口径），账最终一定等于实盘数
3. 原子与幂等：一行失败整单回滚；重复过账 409 且流水不增
"""
import json
import uuid

HEADER = "X-Auth-Token"


def _h(token: str) -> dict:
    return {HEADER: token}


def _create(client, token, **body):
    return client.post("/api/wms/stocktakes", json=body, headers=_h(token))


def _put(client, token, stocktake_id, **body):
    return client.put(f"/api/wms/stocktakes/{stocktake_id}", json=body, headers=_h(token))


def _submit(client, token, stocktake_id):
    return client.post(f"/api/wms/stocktakes/{stocktake_id}/submit", json={}, headers=_h(token))


def _post(client, token, stocktake_id):
    return client.post(f"/api/wms/stocktakes/{stocktake_id}/post", json={}, headers=_h(token))


def _make(client, token, **extra):
    resp = _create(client, token, name="月末盘点", warehouse_id=1, **extra)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["data"]


def _qty(db_conn, product_id=1, warehouse_id=1):
    row = db_conn.execute("SELECT qty FROM stock_current WHERE product_id=? AND warehouse_id=?",
                          (product_id, warehouse_id)).fetchone()
    return int(row["qty"]) if row else 0


def _log_count(db_conn):
    return int(db_conn.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0])


# ==================== 建单 / 快照 ====================

def test_create_snapshot_matches_current(client, admin_token, db_conn):
    doc = _make(client, admin_token)

    assert doc["code"].startswith("ST")
    assert doc["status"] == "DRAFT"
    assert doc["line_count"] == 1
    detail = doc["details"][0]
    assert detail["product_id"] == 1
    assert detail["book_qty"] == _qty(db_conn) == 10
    assert detail["real_qty"] is None and detail["diff_qty"] == 0


def test_create_rejects_warehouse_without_stock(client, admin_token, db_conn):
    from services import timeutil
    now = timeutil.now_iso()
    db_conn.execute("INSERT INTO warehouses(name,created_at,updated_at) VALUES('空仓库',?,?)", (now, now))

    resp = _create(client, admin_token, name="空盘", warehouse_id=2)

    assert resp.status_code == 409
    assert resp.get_json()["code"] == 1014


def test_create_rejects_inactive_warehouse(client, admin_token, db_conn):
    db_conn.execute("UPDATE warehouses SET is_active=0 WHERE id=1")

    resp = _create(client, admin_token, name="停用仓", warehouse_id=1)

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1008


# ==================== 录入 / 提交 ====================

def test_submit_requires_at_least_one_real_qty(client, admin_token):
    doc = _make(client, admin_token)

    resp = _submit(client, admin_token, doc["id"])

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1015


def test_update_records_real_qty_and_diff(client, admin_token):
    doc = _make(client, admin_token)
    detail_id = doc["details"][0]["id"]

    body = _put(client, admin_token, doc["id"], lines=[{"detail_id": detail_id, "real_qty": 7}])
    data = body.get_json()["data"]

    assert data["details"][0]["real_qty"] == 7
    assert data["details"][0]["diff_qty"] == -3   # 快照口径：7 - 10
    assert data["diff_count"] == 1 and data["diff_abs_qty"] == 3


def test_update_rejects_foreign_detail_id(client, admin_token, db_conn):
    first = _make(client, admin_token)
    second = _make(client, admin_token)
    foreign_id = second["details"][0]["id"]

    resp = _put(client, admin_token, first["id"], lines=[{"detail_id": foreign_id, "real_qty": 1}])

    assert resp.status_code == 404
    assert resp.get_json()["code"] == 1006


def test_posted_document_is_not_editable(client, admin_token):
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"], lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 9}])
    _submit(client, admin_token, doc["id"])
    _post(client, admin_token, doc["id"])

    resp = _put(client, admin_token, doc["id"], name="改名")

    assert resp.status_code == 409
    assert resp.get_json()["code"] == 1014


# ==================== 过账（B 口径） ====================

def test_post_sets_stock_to_real_qty(client, admin_token, db_conn):
    doc = _make(client, admin_token)
    detail_id = doc["details"][0]["id"]
    _put(client, admin_token, doc["id"], lines=[{"detail_id": detail_id, "real_qty": 8}])
    _submit(client, admin_token, doc["id"])

    resp = _post(client, admin_token, doc["id"])
    data = resp.get_json()["data"]

    assert resp.status_code == 200
    assert data["status"] == "POSTED" and data["posted_at"]
    assert _qty(db_conn) == 8
    log = db_conn.execute("SELECT type, quantity, before_qty, after_qty, ref_type, ref_id "
                          "FROM stock_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert (log["type"], log["quantity"], log["before_qty"], log["after_qty"]) == ("ADJUST", -2, 10, 8)
    assert log["ref_type"] == "STOCKTAKE" and log["ref_id"] == doc["id"]


def test_post_uses_realtime_qty_not_snapshot(client, admin_token, db_conn):
    """盘点期间出库 5 件：过账必须让库存等于实盘数，并把期间变动写进 remark（B 口径）。"""
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"],
         lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 10}])

    out = client.post("/api/stock/out", headers=_h(admin_token), json={
        "client_msg_id": str(uuid.uuid4()), "product_id": 1, "warehouse_id": 1, "quantity": 5})
    assert out.status_code == 200
    assert _qty(db_conn) == 5

    _submit(client, admin_token, doc["id"])
    _post(client, admin_token, doc["id"])

    assert _qty(db_conn) == 10  # 实盘 10，不是「10 + 5」
    log = db_conn.execute("SELECT quantity, remark FROM stock_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert log["quantity"] == 5            # 10 - 实时 5
    assert "期间变动 -5" in log["remark"]   # 盘面 10 → 实时 5


def test_unrecorded_lines_do_not_create_logs(client, admin_token, db_conn):
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"],
         lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 10}])  # 与账面一致
    _submit(client, admin_token, doc["id"])

    resp = _post(client, admin_token, doc["id"])

    assert resp.status_code == 409
    assert resp.get_json()["code"] == 1014        # 全无差异，不落账
    assert _log_count(db_conn) == 1               # 只有种子那一条


def test_post_is_idempotent(client, admin_token, db_conn):
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"], lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 4}])
    _submit(client, admin_token, doc["id"])
    _post(client, admin_token, doc["id"])
    before = _log_count(db_conn)

    resp = _post(client, admin_token, doc["id"])

    assert resp.status_code == 409
    assert resp.get_json()["code"] == 1014
    assert _log_count(db_conn) == before
    assert _qty(db_conn) == 4


def test_post_rolls_back_whole_bill_when_one_product_disabled(client, admin_token, db_conn):
    """一行失败（商品停用）→ 整单回滚，stock_current 一行未变（docs/14 4.10）。"""
    from services import timeutil
    now = timeutil.now_iso()
    db_conn.execute(
        "INSERT INTO products(name,barcode,category,unit,cost_price,sale_price,min_stock,"
        "created_at,updated_at) VALUES('测试雪碧','6901028100002','饮料','瓶',2,3,5,?,?)", (now, now))
    db_conn.execute("INSERT INTO stock_current(product_id,warehouse_id,qty,updated_at) VALUES(2,1,6,?)",
                    (now,))
    doc = _make(client, admin_token)
    by_product = {d["product_id"]: d["id"] for d in doc["details"]}
    assert set(by_product) == {1, 2}

    _put(client, admin_token, doc["id"], lines=[
        {"detail_id": by_product[1], "real_qty": 3},
        {"detail_id": by_product[2], "real_qty": 1},
    ])
    _submit(client, admin_token, doc["id"])
    db_conn.execute("UPDATE products SET is_active=0 WHERE id=2")

    resp = _post(client, admin_token, doc["id"])

    # 1004 在 core/errors.py 的 HTTP_BY_CODE 里固定映射 403（与既有入库/出库的「商品停用」一致），
    # docs/14 4.7 写的「422/1004」与错误码表冲突，以错误码表为准
    assert resp.status_code == 403
    assert resp.get_json()["code"] == 1004
    assert _qty(db_conn, 1) == 10 and _qty(db_conn, 2) == 6
    status = db_conn.execute("SELECT status FROM wms_stocktake WHERE id=?", (doc["id"],)).fetchone()[0]
    assert status == "SUBMITTED"


def test_append_line_creates_positive_adjust(client, admin_token, db_conn):
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"], append=[{"barcode": "6901028100001", "real_qty": 3}])
    # 同一商品已在明细里 → 拒（防止两行共用一个幂等键）
    assert _put(client, admin_token, doc["id"],
                append=[{"barcode": "6901028100001", "real_qty": 3}]).status_code == 422

    from services import timeutil
    now = timeutil.now_iso()
    db_conn.execute(
        "INSERT INTO products(name,barcode,category,unit,cost_price,sale_price,min_stock,"
        "created_at,updated_at) VALUES('未入账商品','6901028100003','饮料','瓶',1,2,0,?,?)", (now, now))
    body = _put(client, admin_token, doc["id"],
                append=[{"barcode": "6901028100003", "real_qty": 3, "remark": "货架底层发现"}])
    added = [d for d in body.get_json()["data"]["details"] if d["barcode"] == "6901028100003"]
    assert len(added) == 1 and added[0]["book_qty"] == 0 and added[0]["diff_qty"] == 3

    _submit(client, admin_token, doc["id"])
    _post(client, admin_token, doc["id"])

    assert _qty(db_conn, product_id=added[0]["product_id"]) == 3
    log = db_conn.execute("SELECT type, quantity FROM stock_logs WHERE product_id=?",
                          (added[0]["product_id"],)).fetchone()
    assert (log["type"], log["quantity"]) == ("ADJUST", 3)


def test_post_batches_but_stays_atomic(client, admin_token, db_conn, monkeypatch):
    """分批（每批 1 行）仍是一单原子：两行差异 → 两条流水，最终库存都等于实盘数。"""
    from services import timeutil
    monkeypatch.setattr("services.stocktake_service.POST_CHUNK", 1)
    now = timeutil.now_iso()
    db_conn.execute(
        "INSERT INTO products(name,barcode,category,unit,cost_price,sale_price,min_stock,"
        "created_at,updated_at) VALUES('测试雪碧','6901028100002','饮料','瓶',2,3,5,?,?)", (now, now))
    db_conn.execute("INSERT INTO stock_current(product_id,warehouse_id,qty,updated_at) VALUES(2,1,6,?)",
                    (now,))

    doc = _make(client, admin_token)
    by_product = {d["product_id"]: d["id"] for d in doc["details"]}
    _put(client, admin_token, doc["id"], lines=[
        {"detail_id": by_product[1], "real_qty": 3},
        {"detail_id": by_product[2], "real_qty": 4},
    ])
    _submit(client, admin_token, doc["id"])
    _post(client, admin_token, doc["id"])

    assert _qty(db_conn, 1) == 3 and _qty(db_conn, 2) == 4
    refs = db_conn.execute(
        "SELECT ref_type, ref_id FROM stock_logs WHERE ref_type='STOCKTAKE'").fetchall()
    assert len(refs) == 2 and all(r["ref_id"] == doc["id"] for r in refs)


def test_recalc_has_no_diff_after_posting(client, app, admin_token, db_conn):
    """盘点后 `recalc-stock` 必须无输出（docs/02 不变式 1）。"""
    from services import stock_service
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"], lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 3}])
    _submit(client, admin_token, doc["id"])
    _post(client, admin_token, doc["id"])

    with app.app_context():
        assert stock_service.recalc_preview() == []


# ==================== 驳回 / 作废 ====================

def test_reject_requires_reason_and_allows_resubmit(client, admin_token):
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"], lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 9}])
    _submit(client, admin_token, doc["id"])

    bad = client.post(f"/api/wms/stocktakes/{doc['id']}/reject", json={}, headers=_h(admin_token))
    assert bad.status_code == 422 and bad.get_json()["code"] in (1002, 1019)

    ok = client.post(f"/api/wms/stocktakes/{doc['id']}/reject", json={"reason": "复核数量不符"},
                     headers=_h(admin_token))
    data = ok.get_json()["data"]
    assert data["status"] == "REJECTED"
    assert "复核数量不符" in data["remark"]

    assert _submit(client, admin_token, doc["id"]).get_json()["data"]["status"] == "SUBMITTED"


def test_cancel_keeps_details_and_blocks_post(client, admin_token, db_conn):
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"], lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 9}])

    resp = client.delete(f"/api/wms/stocktakes/{doc['id']}", headers=_h(admin_token))
    data = resp.get_json()["data"]

    assert data["status"] == "CANCELED"
    assert len(data["details"]) == 1
    assert _submit(client, admin_token, doc["id"]).status_code == 409


# ==================== 权限 / 列表 / 打印 / 导出 ====================

def test_operator_cannot_post_but_can_create(client, operator_token, db_conn):
    doc = _make(client, operator_token)
    _put(client, operator_token, doc["id"],
         lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 9}])
    _submit(client, operator_token, doc["id"])

    resp = _post(client, operator_token, doc["id"])

    assert resp.status_code == 403
    assert resp.get_json()["code"] == 1004       # 权限不足（require_admin）


def test_list_and_print_and_export(client, admin_token, db_conn):
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"], lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 9}])

    listing = client.get("/api/wms/stocktakes?status=DRAFT", headers=_h(admin_token)).get_json()["data"]
    assert listing["total"] == 1 and listing["list"][0]["diff_count"] == 1

    blank = client.get(f"/api/wms/stocktakes/{doc['id']}/print?mode=blank",
                       headers=_h(admin_token)).get_json()["data"]
    assert blank["mode"] == "blank" and blank["lines"][0]["real_qty"] is None

    diff = client.get(f"/api/wms/stocktakes/{doc['id']}/print?mode=diff",
                      headers=_h(admin_token)).get_json()["data"]
    assert diff["total"] == 1 and diff["lines"][0]["diff_qty"] == -1

    csv = client.get("/api/wms/export/stocktakes", headers=_h(admin_token))
    assert csv.status_code == 200
    assert csv.data.startswith(b"\xef\xbb\xbf")
    assert doc["code"].encode() in csv.data


def test_bills_switch_blocks_stocktake_writes(client, admin_token, db_conn):
    """单据总开关关闭后盘点单写操作一律 409/1018（盘点单也是单据），但历史仍可查。"""
    r = client.post("/api/settings", json={"items": {"wms_bills_enabled": False}},
                    headers=_h(admin_token))
    assert r.status_code == 200, r.get_json()

    created = _create(client, admin_token, name="关开关后建单", warehouse_id=1)

    assert created.status_code == 409
    assert created.get_json()["code"] == 1018
    assert client.get("/api/wms/stocktakes", headers=_h(admin_token)).status_code == 200


def test_pending_approvals_include_submitted_stocktake(client, admin_token):
    doc = _make(client, admin_token)
    _put(client, admin_token, doc["id"], lines=[{"detail_id": doc["details"][0]["id"], "real_qty": 6}])
    _submit(client, admin_token, doc["id"])

    data = client.get("/api/wms/approvals?type=STOCKTAKE",
                      headers=_h(admin_token)).get_json()["data"]

    assert data["total"] == 1
    assert data["list"][0]["biz"] == "STOCKTAKE"
    assert data["list"][0]["code"] == doc["code"]
    assert data["list"][0]["total_qty"] == 0
