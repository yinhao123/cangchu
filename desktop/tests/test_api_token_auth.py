"""设备令牌鉴权测试（TDD RED）—— PRD 3.3 / 10.1 · docs/03 第 3 章 · UT-11

令牌是本系统唯一的 APP 鉴权路径，必须证明：
  1. 库里只存 SHA-256 哈希（翻库 ≠ 拿到可用凭据）
  2. 无令牌 / 错令牌 / 已吊销 / 已过期 / 账号停用 → 一律不通过
  3. 命中时刷新 last_used_at，并返回可用于 g.user 的用户信息
"""
import pytest

from tests.conftest import SEED_ADMIN_PWD

BAD_TOKENS = ["", "not-a-real-token", "0" * 48]


def test_issue_token_stores_only_hash(db_conn, seed):
    from services.auth_service import hash_token, issue_token
    token_id, plain = issue_token(db_conn, user_id=1, device_name="Redmi")
    assert len(plain) == 48
    assert plain != hash_token(plain)
    row = db_conn.execute("SELECT token_hash, device_name FROM api_tokens WHERE id=?", (token_id,)).fetchone()
    assert row["token_hash"] == hash_token(plain), "库里必须存哈希，绝不存明文"
    assert row["device_name"] == "Redmi"


def test_issue_token_for_unknown_user_is_rejected(db_conn, seed):
    """FK enforcement 生效的正面证据：给不存在的用户签发令牌必须失败（T-02）"""
    import sqlite3
    from services.auth_service import issue_token
    with pytest.raises(sqlite3.IntegrityError):
        issue_token(db_conn, user_id=9999, device_name="ghost")


def test_authenticate_token_roundtrip(app, db_conn, seed):
    from services.api_auth import authenticate_token
    from services.auth_service import issue_token
    _token_id, plain = issue_token(db_conn, user_id=1, device_name="A")
    with app.test_request_context("/api/me"):
        user = authenticate_token(plain)
    assert user is not None
    assert user["username"] == "admin" and user["role"] == "admin"


@pytest.mark.parametrize("bad", BAD_TOKENS)
def test_bad_token_returns_none(app, seed, bad):
    from services.api_auth import authenticate_token
    with app.test_request_context("/api/me"):
        assert authenticate_token(bad) is None


def test_revoked_token_is_rejected(app, db_conn, seed):
    from services.api_auth import authenticate_token
    from services.auth_service import issue_token, revoke_token
    _token_id, plain = issue_token(db_conn, user_id=1, device_name="B")
    revoke_token(db_conn, plain)
    with app.test_request_context("/api/me"):
        assert authenticate_token(plain) is None


def test_expired_token_is_rejected(app, db_conn, seed):
    from services.api_auth import authenticate_token
    from services.auth_service import issue_token
    _token_id, plain = issue_token(db_conn, user_id=1, device_name="C", days=-1)
    with app.test_request_context("/api/me"):
        assert authenticate_token(plain) is None


def test_disabled_account_token_is_rejected(app, db_conn, seed):
    """账号停用必须立刻让该账号所有设备失效（PRD 5.1 异常表）"""
    from services.api_auth import authenticate_token
    from services.auth_service import issue_token
    _token_id, plain = issue_token(db_conn, user_id=1, device_name="D")
    db_conn.execute("UPDATE users SET is_active=0 WHERE id=1")
    with app.test_request_context("/api/me"):
        assert authenticate_token(plain) is None


def test_hit_refreshes_last_used_at(app, db_conn, seed):
    from services.api_auth import authenticate_token
    from services.auth_service import issue_token
    token_id, plain = issue_token(db_conn, user_id=1, device_name="E")
    before = db_conn.execute("SELECT last_used_at FROM api_tokens WHERE id=?", (token_id,)).fetchone()["last_used_at"]
    with app.test_request_context("/api/me"):
        authenticate_token(plain)
    after = db_conn.execute("SELECT last_used_at FROM api_tokens WHERE id=?", (token_id,)).fetchone()["last_used_at"]
    assert before is None and after is not None


def test_same_device_relogin_replaces_previous_token(db_conn, seed):
    """同用户同设备重复登录：作废旧令牌 + 发新令牌。

    ★ 为什么不是「复用同一明文令牌」：库里只存 SHA-256 哈希（PRD 10.1），
      一旦返回过就再也取不回明文，物理上不可能复用。
    ★ 为什么必须替换而不是新增：否则每台手机每次重登都多一行，
      设备列表会出现 20 个同名设备，且被盗用的旧令牌长期有效。
    ★ 契约影响：APP 每次登录必须用响应里的新 token **覆盖**本地存储（写入 docs/03 与 android 契约）。
    """
    from services.api_auth import authenticate_token
    from services.auth_service import issue_token
    id1, t1 = issue_token(db_conn, user_id=1, device_name="SamePhone")
    id2, t2 = issue_token(db_conn, user_id=1, device_name="SamePhone")
    assert id2 != id1 and t2 != t1
    active = db_conn.execute("SELECT COUNT(*) c FROM api_tokens WHERE revoked_at IS NULL").fetchone()["c"]
    assert active == 1, "同一设备同时只能有一个有效令牌"
    assert db_conn.execute("SELECT COUNT(*) c FROM api_tokens").fetchone()["c"] == 2, "旧行保留供审计"
    with_seed = db_conn.execute("SELECT revoked_at FROM api_tokens WHERE id=?", (id1,)).fetchone()["revoked_at"]
    assert with_seed is not None


# ---------------------------------------------------------------- 强制改密全局门禁（docs/03 3.3）

NEW_PWD = "New" + "Pass-2026"
#: 初始口令未改前，这些接口都必须被 403/1004 拦下（读接口同样拦：否则弱口令会一直用下去）
BLOCKED_WHILE_MUST_CHANGE = ("/api/products", "/api/dashboard", "/api/stock/logs",
                             "/api/stock/current", "/api/labels", "/api/qrcode", "/api/warehouses")


def _seed_admin_token(db_conn, device: str = "MustChange"):
    from services.auth_service import issue_token
    _token_id, plain = issue_token(db_conn, user_id=1, device_name=device)
    return plain


def test_must_change_password_blocks_every_business_api(client, db_conn, seed):
    token = _seed_admin_token(db_conn)
    headers = {"X-Auth-Token": token}
    for path in BLOCKED_WHILE_MUST_CHANGE:
        r = client.get(path, headers=headers)
        assert r.status_code == 403 and r.get_json()["code"] == 1004, path


def test_must_change_password_still_allows_me_logout_and_change(client, db_conn, seed):
    token = _seed_admin_token(db_conn, "MustChangeAllowed")
    headers = {"X-Auth-Token": token}

    me = client.get("/api/me", headers=headers)
    assert me.status_code == 200 and me.get_json()["data"]["must_change_password"] == 1

    wrong = client.post("/api/me/password", headers=headers,
                        json={"old_password": "no-such" + "-password", "new_password": NEW_PWD})
    assert wrong.status_code == 422 and wrong.get_json()["code"] == 1002, \
        "原密码错误必须是 1002：1003 会让客户端清 Token 把用户踢回登录页"

    changed = client.post("/api/me/password", headers=headers,
                          json={"old_password": SEED_ADMIN_PWD, "new_password": NEW_PWD})
    assert changed.status_code == 200
    assert client.get("/api/products", headers=headers).status_code == 200, "改密后同一 Token 立即可用"

    other = _seed_admin_token(db_conn, "MustChangeLogout")
    assert client.post("/api/logout", headers={"X-Auth-Token": other}).status_code == 200
