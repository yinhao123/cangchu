"""标签打印接口（SPA 补齐）—— PRD 5.7 · docs/03 第 10 章

SPA 不做 HTML 注入，条码图改为独立图片资源（`<img src>`），
白名单校验仍由 `net/barcode_gen.py` 兜底 —— 这里断言它确实生效、且无条码商品回落内码。
"""
from net.barcode_gen import internal_code

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _auth(token: str) -> dict:
    return {"X-Auth-Token": token}


def test_labels_require_login(client, seed):
    assert client.get("/api/labels").status_code == 401


def test_labels_list_carries_barcode_text_and_label_size(client, admin_token, seed):
    r = client.get("/api/labels", headers=_auth(admin_token))
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["total"] == 1 and d["max_copies"] == 50
    assert d["label_w"] > 0 and d["label_h"] > 0
    item = d["list"][0]
    assert item["barcode_text"] == "6901028100001" and item["narrow"] is False


def test_labels_keyword_filter(client, admin_token, seed):
    hit = client.get("/api/labels?keyword=可乐", headers=_auth(admin_token)).get_json()["data"]
    miss = client.get("/api/labels?keyword=不存在的商品", headers=_auth(admin_token)).get_json()["data"]
    assert hit["total"] == 1 and miss["total"] == 0


def test_barcode_svg_is_direct_image(client, admin_token, seed):
    r = client.get("/api/products/1/barcode.svg", headers=_auth(admin_token))
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"
    body = r.get_data(as_text=True)
    assert body.startswith("<svg") and body.endswith("</svg>")
    assert "script" not in body.lower(), "白名单字符集之外的内容不可能出现在响应里"


def test_barcode_png_is_direct_image(client, admin_token, seed):
    r = client.get("/api/products/1/barcode.png", headers=_auth(admin_token))
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert r.get_data().startswith(PNG_MAGIC)


def test_barcode_falls_back_to_internal_code(client, admin_token, db_conn, seed):
    """无条码商品也要能打标签：用内码 'S' + 10 位补零 id（PRD 5.7）"""
    now = seed["now"]
    db_conn.execute(
        "INSERT INTO products(name,unit,cost_price,sale_price,min_stock,created_at,updated_at) "
        "VALUES('无码商品','件',0,0,0,?,?)", (now, now))
    product_id = db_conn.execute("SELECT id FROM products WHERE name='无码商品'").fetchone()[0]

    assert client.get(f"/api/products/{product_id}/barcode.svg",
                      headers=_auth(admin_token)).status_code == 200
    items = client.get("/api/labels", headers=_auth(admin_token)).get_json()["data"]["list"]
    row = next(x for x in items if x["id"] == product_id)
    assert row["barcode_text"] == internal_code(product_id)


def test_barcode_for_unknown_product_is_404(client, admin_token, seed):
    r = client.get("/api/products/9999/barcode.svg", headers=_auth(admin_token))
    assert r.status_code == 404 and r.get_json()["code"] == 1006


def test_labels_response_is_json_safe(client, admin_token, seed):
    """列表要能直接进 JSON：条码图绝不内联在列表里（会把响应撑到几百 KB）"""
    d = client.get("/api/labels", headers=_auth(admin_token)).get_json()["data"]
    assert "image_png_base64" not in d and "svg" not in d
