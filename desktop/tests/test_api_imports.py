"""批量导入/导出 —— docs/14 第 3 章（P0-1）验收项。

覆盖四个最容易出事的点：
1. 编码（GBK 的 Excel CSV 不能变乱码，T-39）；
2. preview 的行号与字段能精确指到出问题的行；
3. 期初库存的幂等（同一文件两次不翻倍，T-40）与「账本非空拒绝」；
4. 导出的 BOM + CRLF（Excel 打开不乱码，T-10）。
"""
import io

import pytest


def _csv(text: str) -> bytes:
    return text.encode("utf-8-sig")


def _upload(client, token, kind: str, raw: bytes, path="/api/imports/preview",
            skip_invalid=None, filename="data.csv"):
    data = {"kind": kind, "file": (io.BytesIO(raw), filename)}
    if skip_invalid is not None:
        data["skip_invalid"] = "true" if skip_invalid else "false"
    return client.post(path, data=data, content_type="multipart/form-data",
                       headers={"X-Auth-Token": token})


@pytest.fixture
def empty_book(app, db_conn):
    """只有账号和仓库、**没有任何流水**的账本 —— 期初建账的唯一合法场景。"""
    from services import timeutil
    from services.auth_service import hash_password

    now = timeutil.now_iso()
    db_conn.execute(
        "INSERT INTO users(username,password_hash,role,must_change_password,created_at,updated_at) "
        "VALUES('admin',?,'admin',0,?,?)", (hash_password("Adm1n-Test-Pwd"), now, now))
    db_conn.execute(
        "INSERT INTO users(username,password_hash,role,created_at,updated_at) "
        "VALUES('op1',?,'operator',?,?)", (hash_password("Op1-Test-Pwd"), now, now))
    db_conn.execute(
        "INSERT INTO warehouses(name, code, created_at, updated_at) VALUES('主仓库','WH-MAIN',?,?)",
        (now, now))
    db_conn.execute(
        "INSERT INTO products(name,barcode,category,unit,cost_price,sale_price,min_stock,"
        "created_at,updated_at) VALUES('期初测试商品','6901111111111','测试','瓶',2.5,3.5,5,?,?)",
        (now, now))
    return app


@pytest.fixture
def empty_admin_token(app, empty_book, db_conn):
    from services.auth_service import issue_token
    return issue_token(db_conn, user_id=1, device_name="admin-test")[1]


@pytest.fixture
def empty_operator_token(app, empty_book, db_conn):
    from services.auth_service import issue_token
    return issue_token(db_conn, user_id=2, device_name="op-test")[1]


# ==================== 模板与导出 ====================

def test_template_has_bom_and_examples(client, operator_token):
    resp = client.get("/api/imports/templates/products", headers={"X-Auth-Token": operator_token})

    assert resp.status_code == 200
    assert resp.data.startswith(b"\xef\xbb\xbf")          # BOM：Excel 中文不乱码
    text = resp.data.decode("utf-8-sig")
    assert "name,barcode,category" in text
    assert "\r\n" in text                                  # CRLF
    assert "#" in text                                     # 注释行


def test_export_products_bom(client, operator_token):
    resp = client.get("/api/imports/export/products", headers={"X-Auth-Token": operator_token})

    assert resp.status_code == 200
    assert resp.data.startswith(b"\xef\xbb\xbf")
    assert "测试可乐" in resp.data.decode("utf-8-sig")


# ==================== preview ====================

def test_preview_reports_row_field_and_msg(client, operator_token):
    raw = _csv(
        "name,barcode,category,unit,cost_price,sale_price,min_stock,remark\r\n"
        "正常商品 A,6900000000011,饮料,瓶,1.5,2.5,10,\r\n"
        ",6900000000012,饮料,瓶,1.5,2.5,10,缺名称\r\n"
        "条码冲突商品,6901028100001,饮料,瓶,1.5,2.5,10,与种子商品撞码\r\n"
    )

    body = _upload(client, operator_token, "products", raw).get_json()["data"]

    assert body["total"] == 3 and body["valid"] == 1 and body["invalid"] == 2
    rows = {item["row"]: item for item in body["errors"]}
    assert rows[3]["field"] == "name"
    assert rows[4]["field"] == "barcode" and "测试可乐" in rows[4]["msg"]


def test_preview_accepts_gbk(client, operator_token):
    raw = (
        "name,type,contact,phone,address,remark\r\n"
        "GBK 编码客户,CUSTOMER,张经理,13800000000,城东区,\r\n"
    ).encode("gbk")

    body = _upload(client, operator_token, "partners", raw).get_json()["data"]

    assert body["valid"] == 1 and body["invalid"] == 0


def test_preview_rejects_broken_header(client, operator_token):
    raw = _csv("foo,bar\r\n1,2\r\n")

    resp = _upload(client, operator_token, "products", raw)

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1019


# ==================== commit（主数据） ====================

def test_commit_skip_invalid_keeps_good_rows(client, operator_token, db_conn):
    before = db_conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    raw = _csv(
        "name,barcode,category,unit,cost_price,sale_price,min_stock,remark\r\n"
        "导入商品一,6900000000021,饮料,瓶,1,2,3,\r\n"
        ",6900000000022,饮料,瓶,1,2,3,缺名称\r\n"
    )

    body = _upload(client, operator_token, "products", raw,
                   path="/api/imports/commit", skip_invalid=True).get_json()["data"]

    assert body["ok_count"] == 1 and body["failed_count"] == 0
    # 口径锁死：commit 的 total/valid/invalid 必须与 preview 完全一致（都是「文件数据行数」），
    # 否则同一份文件在预检（共 2 行）与提交（共 1 行）时给出两个不同的总数。
    assert body["total"] == 2 and body["valid"] == 1 and body["invalid"] == 1
    after = db_conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    assert after == before + 1


def test_commit_strict_rolls_back_whole_file(client, operator_token, db_conn):
    before = db_conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    raw = _csv(
        "name,barcode,category,unit,cost_price,sale_price,min_stock,remark\r\n"
        "不该入库的商品,6900000000031,饮料,瓶,1,2,3,\r\n"
        ",6900000000032,饮料,瓶,1,2,3,缺名称\r\n"
    )

    resp = _upload(client, operator_token, "products", raw,
                   path="/api/imports/commit", skip_invalid=False)

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1019
    assert db_conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == before


def test_commit_empty_file(client, operator_token):
    resp = _upload(client, operator_token, "products", _csv("name,barcode\r\n"),
                   path="/api/imports/commit", skip_invalid=False)

    assert resp.status_code == 422
    assert resp.get_json()["code"] == 1019


def test_commit_all_rows_invalid_is_not_empty_file(client, operator_token, db_conn):
    """有数据行但全部不合法 ≠ 空文件：要回 ok_count=0 + invalid>0，而不是 1019「没有数据行」。"""
    before = db_conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    raw = _csv(
        "name,barcode,category,unit,cost_price,sale_price,min_stock,remark\r\n"
        ",6900000000041,饮料,瓶,1,2,3,缺名称\r\n"
    )

    body = _upload(client, operator_token, "products", raw,
                   path="/api/imports/commit", skip_invalid=True).get_json()["data"]

    assert body["ok_count"] == 0 and body["failed_count"] == 0
    assert body["total"] == 1 and body["valid"] == 0 and body["invalid"] == 1
    assert db_conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == before


# ==================== 期初库存 ====================

def test_opening_stock_is_idempotent(client, empty_admin_token, db_conn):
    raw = _csv(
        "barcode,warehouse_code,qty,remark\r\n"
        "6901111111111,WH-MAIN,100,期初\r\n"
    )

    first = _upload(client, empty_admin_token, "opening-stock", raw,
                    path="/api/imports/commit", skip_invalid=False).get_json()["data"]
    qty_after_first = db_conn.execute("SELECT qty FROM stock_current").fetchone()[0]

    second = _upload(client, empty_admin_token, "opening-stock", raw,
                     path="/api/imports/commit", skip_invalid=False).get_json()["data"]

    assert first["ok_count"] == 1
    assert second["duplicated_count"] == 1 and second["ok_count"] == 0
    assert second["duplicated"] is True
    assert db_conn.execute("SELECT qty FROM stock_current").fetchone()[0] == qty_after_first == 100
    # 溯源与「不挂供应商」两条硬约束
    log = db_conn.execute("SELECT type, ref_type, partner_id, remark FROM stock_logs").fetchone()
    assert tuple(log) == ("IN", "OPENING", None, "期初")


def test_opening_stock_rejected_on_non_empty_ledger(client, admin_token, db_conn):
    raw = _csv(
        "barcode,warehouse_code,qty,remark\r\n"
        "6901028100001,主仓库,10,\r\n"
    )

    resp = _upload(client, admin_token, "opening-stock", raw,
                   path="/api/imports/commit", skip_invalid=False)

    assert resp.status_code == 409
    assert resp.get_json()["code"] == 1021
    # 原有 10 件不能被改动
    assert db_conn.execute("SELECT qty FROM stock_current").fetchone()[0] == 10


def test_opening_stock_requires_admin(client, empty_operator_token):
    raw = _csv("barcode,warehouse_code,qty,remark\r\n6901111111111,WH-MAIN,5,\r\n")

    resp = _upload(client, empty_operator_token, "opening-stock", raw,
                   path="/api/imports/commit", skip_invalid=False)

    assert resp.status_code == 403
    assert resp.get_json()["code"] == 1004


def test_opening_stock_rejects_unknown_barcode(client, empty_admin_token, db_conn):
    raw = _csv("barcode,warehouse_code,qty,remark\r\n6099999999999,WH-MAIN,5,\r\n")

    body = _upload(client, empty_admin_token, "opening-stock", raw).get_json()["data"]

    assert body["valid"] == 0
    assert "未登记" in body["errors"][0]["msg"]
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0] == 0


def test_opening_stock_duplicate_rows_in_one_file(client, empty_admin_token, db_conn):
    raw = _csv(
        "barcode,warehouse_code,qty,remark\r\n"
        "6901111111111,WH-MAIN,10,\r\n"
        "6901111111111,WH-MAIN,20,同一商品同一仓库重复\r\n"
    )

    body = _upload(client, empty_admin_token, "opening-stock", raw).get_json()["data"]

    assert body["valid"] == 1 and body["invalid"] == 1
    assert "第 2 行" in body["errors"][0]["msg"]
