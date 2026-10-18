"""登录与商品写接口的契约测试 —— PRD 5.1 / 5.3 / 3.4 · docs/03 第 3、4 章 · UT-09/10/11/15/17

覆盖：登录换令牌与强制改密标记、登录失败锁定与账号枚举防护、管理员接口越权、
      条码规范化、商品唯一性与软删语义、字段校验不返回 500。

★登录相关用例原本断言网页会话（302 跳转 + 重定向地址），界面已改成浏览器端 SPA、
  统一走 `POST /api/login` 换 Token，因此断言对象同步改为**接口契约**：
  身份是否可信由 `must_change_password` 标志 + 全量 `/api/*` 拦截保证，
  而不是靠服务端把浏览器从 /login 重定向到 /change_password。
"""
import json

import pytest

from tests.conftest import SEED_ADMIN_PWD, WRONG_PWD


# ---------- 登录换令牌 ----------

def test_login_returns_token_and_must_change_flag(client, seed):
    """初始弱口令账号（must_change_password=1）登录后必须带着「先去改密」的标记（PRD 5.1）"""
    r = client.post("/api/login", json={"username": "admin", "password": SEED_ADMIN_PWD})
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["token"], "必须返回明文令牌（唯一一次出现）"
    assert data["user"]["must_change_password"] == 1


def test_login_after_password_change_clears_flag(client, seed, db_conn):
    db_conn.execute("UPDATE users SET must_change_password=0 WHERE id=1")
    r = client.post("/api/login", json={"username": "admin", "password": SEED_ADMIN_PWD})
    assert r.get_json()["data"]["user"]["must_change_password"] == 0


def test_login_response_never_leaks_password_hash(client, seed):
    raw = client.post("/api/login", json={"username": "admin", "password": SEED_ADMIN_PWD}
                      ).get_data(as_text=True)
    assert "password_hash" not in raw and "pbkdf2" not in raw


def test_wrong_password_is_generic_not_account_enumeration(client, seed):
    """账号不存在与密码错误必须同一句话，否则可被用来枚举有效账号（PRD 5.1）"""
    bad_pw = client.post("/api/login", json={"username": "admin", "password": WRONG_PWD})
    bad_user = client.post("/api/login", json={"username": "nosuchuser", "password": SEED_ADMIN_PWD})
    assert bad_pw.status_code == 401 and bad_user.status_code == 401
    assert bad_pw.get_json()["msg"] == bad_user.get_json()["msg"]
    assert "不存在" not in json.dumps(bad_user.get_json(), ensure_ascii=False)


def test_login_locks_after_five_failures(client, seed):
    """PRD 5.1：同用户名连续失败 5 次锁 15 分钟，返回剩余秒数"""
    for _ in range(5):
        client.post("/api/login", json={"username": "admin", "password": WRONG_PWD})
    r = client.post("/api/login", json={"username": "admin", "password": WRONG_PWD})
    assert r.status_code == 429 and r.get_json()["code"] == 1005
    assert r.get_json()["data"]["retry_after"] > 0


@pytest.mark.parametrize("payload", [
    {},
    {"username": "admin"},
    {"username": "", "password": "x"},
    {"username": "admin", "password": ""},
])
def test_login_missing_fields_is_422_not_500(client, seed, payload):
    r = client.post("/api/login", json=payload)
    assert r.status_code == 422 and r.get_json()["code"] == 1002, payload


def test_operator_token_is_rejected_by_admin_only_apis(client, seed, operator_token):
    """operator 越权访问管理员接口 → 403/1004（界面藏入口不等于挡住，服务端才是裁判，PRD 3.1）"""
    for path in ("/api/users", "/api/devices", "/api/settings", "/api/backup/list",
                 "/api/system/info"):
        r = client.get(path, headers={"X-Auth-Token": operator_token})
        assert r.status_code == 403 and r.get_json()["code"] == 1004, path


def test_frontend_route_is_served_by_spa_instead_of_redirect(client):
    """SPA 化后不再有「未登录 → 302 /login」：页面由前端路由渲染，接口层才回 401/403"""
    r = client.get("/stock/logs?date_from=2026-10-01")
    assert r.status_code == 200
    assert "id=\"root\"" in r.get_data(as_text=True)


def test_foreign_next_param_is_ignored_not_reflected(client):
    """SPA 不再使用 `?next=`：外部地址既不被跳转也不被回显（防开放重定向，PRD 7.4）"""
    r = client.get("/login?next=http://evil.example.com")
    assert r.status_code == 200
    assert "evil.example.com" not in r.get_data(as_text=True)


# ---------- 条码规范化与商品唯一性 ----------

@pytest.mark.parametrize("raw,expect", [
    (" 6901028100001 ", "6901028100001"),
    ("6901028100001", "6901028100001"),
])
def test_barcode_normalization_strips_spaces(raw, expect):
    from services.validators import normalize_barcode
    assert normalize_barcode(raw) == expect


def test_fullwidth_digits_are_converted():
    from services.validators import normalize_barcode
    assert normalize_barcode("６９０１２３") == "690123"


def test_internal_code_generation():
    from services.validators import normalize_barcode
    assert normalize_barcode("S0000000123") == "S0000000123"


@pytest.mark.parametrize("bad", ["ab", "1" * 49, "6901-ABC#", "中文条码", "6901 028"])
def test_invalid_barcodes_are_rejected_not_silently_fixed(bad):
    """★ 不合规必须报错，禁止「自动修正后静默通过」——否则用户以为存进去了却查不到（PRD 5.3）"""
    from core.errors import BizError
    from services.validators import normalize_barcode
    with pytest.raises(BizError) as ei:
        normalize_barcode(bad)
    assert ei.value.code == 1002


def test_api_duplicate_barcode_returns_conflict_detail(client, seed, admin_token):
    r = client.post("/api/products", json={"name": "重名可乐", "barcode": "6901028100001"},
                    headers={"X-Auth-Token": admin_token})
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == 1010
    assert body["data"]["conflict"]["id"] == 1
    assert body["data"]["conflict"]["name"] == "测试可乐"


def test_api_soft_deleted_barcode_still_occupies_unique(client, seed, admin_token, db_conn):
    """软删商品的条码永久占用（PRD 4.3.5：防一码两品）"""
    client.delete("/api/products/1", headers={"X-Auth-Token": admin_token})
    r = client.post("/api/products", json={"name": "复活", "barcode": "6901028100001"},
                    headers={"X-Auth-Token": admin_token})
    assert r.status_code == 409, "软删除后不得复用同一条码，必须走「恢复」"


def test_api_product_put_absent_barcode_keeps_it(client, seed, admin_token, db_conn):
    """语义锁定（docs/03 4.4）：不传=不改，传 null=清空"""
    version = client.get("/api/products/1", headers={"X-Auth-Token": admin_token}).get_json()["data"]["updated_at"]
    r1 = client.put("/api/products/1", json={"name": "改名了", "expected_updated_at": version},
                    headers={"X-Auth-Token": admin_token})
    assert r1.status_code == 200, f"不传 barcode 不应清掉条码：{r1.get_json()}"
    assert db_conn.execute("SELECT barcode FROM products WHERE id=1").fetchone()[0] == "6901028100001"
    assert db_conn.execute("SELECT name FROM products WHERE id=1").fetchone()[0] == "改名了"


def test_api_product_put_null_barcode_clears_it(client, seed, admin_token, db_conn):
    version = client.get("/api/products/1", headers={"X-Auth-Token": admin_token}).get_json()["data"]["updated_at"]
    r = client.put("/api/products/1", json={"barcode": None, "expected_updated_at": version},
                   headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200, f"传 null 应清空条码：{r.get_json()}"
    assert db_conn.execute("SELECT barcode FROM products WHERE id=1").fetchone()[0] is None


def test_api_product_put_stale_version_conflicts(client, seed, admin_token, db_conn):
    """乐观锁：版本戳过期 → 1011，且不覆盖他人修改（PRD 5.3）"""
    r = client.put("/api/products/1", json={"name": "抢写", "expected_updated_at": "2020-01-01T00:00:00+08:00"},
                   headers={"X-Auth-Token": admin_token})
    assert r.status_code == 409 and r.get_json()["code"] == 1011
    assert db_conn.execute("SELECT name FROM products WHERE id=1").fetchone()[0] == "测试可乐"
    assert "current_updated_at" in r.get_json()["data"]


def _now():
    from services import timeutil
    return timeutil.now_iso()


def test_api_operator_cannot_create_product(client, seed, operator_token):
    r = client.post("/api/products", json={"name": "x", "barcode": "999888777"},
                    headers={"X-Auth-Token": operator_token})
    assert r.status_code == 403 and r.get_json()["code"] == 1004


def test_api_missing_token_is_401(client):
    r = client.post("/api/products", json={"name": "x"})
    assert r.status_code == 401 and r.get_json()["code"] == 1003


@pytest.mark.parametrize("payload", [
    {"name": ""},
    {"name": "x" * 101},
    {"name": "ok", "cost_price": -1},
    {"name": "ok", "min_stock": "abc"},
    {"name": "ok", "min_stock": 1.5},
])
def test_api_invalid_product_payload_is_422_not_500(client, seed, admin_token, payload):
    r = client.post("/api/products", json=payload, headers={"X-Auth-Token": admin_token})
    assert r.status_code == 422, f"非法输入必须是 422，实际 {r.status_code}：{payload}"
    assert r.get_json()["code"] == 1002


def test_api_by_barcode_returns_null_keys_not_omitted(client, seed, admin_token):
    """安卓 data class 依赖键存在（docs/03 1.1）：可空字段返回 null 而非省略"""
    r = client.get("/api/products/by-barcode/6901028100001", headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200
    d = r.get_json()["data"]
    for key in ("id", "name", "barcode", "internal_code", "category", "unit",
                "cost_price", "sale_price", "min_stock", "is_active", "remark",
                "total_qty", "stock_by_warehouse", "created_at", "updated_at"):
        assert key in d, f"缺键 {key}（值为 null 也必须存在）"
    assert d["total_qty"] == 10


def test_api_by_barcode_unknown_is_404_with_code_1007(client, seed, admin_token):
    """未登记条码：HTTP 404（查找未命中的正确语义）+ 业务码 1007（驱动 APP「去电脑端新增」流程）"""
    r = client.get("/api/products/by-barcode/0000000000000", headers={"X-Auth-Token": admin_token})
    assert r.status_code == 404 and r.is_json and r.get_json()["code"] == 1007
    assert r.get_json()["data"]["barcode"] == "0000000000000"
