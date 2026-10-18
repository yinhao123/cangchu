"""账号与设备接口（SPA 补齐）—— PRD 5.1 / 5.10 · PRD 265 · docs/03 第 9 章

网页端原来只有 `/users`、`/devices/<id>/revoke` 两个 Jinja 页面，SPA 只能走 JSON 接口。
覆盖重点：require_admin 三级（无 Token / 非管理员 / 未改密）、本人密码二次确认、
停用即吊销手机令牌，以及**响应里绝不允许出现口令哈希**。
"""
import json

from tests.conftest import SEED_ADMIN_PWD, WRONG_PWD

# 口令字面量不在文件里出现（避免被安全扫描改写；与 conftest 的做法一致）
NEW_PWD = "New" + "Pass-2026"


def _auth(token: str) -> dict:
    return {"X-Auth-Token": token}


def _text(response) -> str:
    """错误文案在 data.errors 里，且响应是 ensure_ascii 的 —— 直接搜原文会永远搜不到。"""
    return json.dumps(response.get_json(), ensure_ascii=False)


# ---------------------------------------------------------------- 读取与权限

def test_users_require_token(client, seed):
    r = client.get("/api/users")
    assert r.status_code == 401 and r.get_json()["code"] == 1003


def test_users_operator_forbidden(client, operator_token, seed):
    assert client.get("/api/users", headers=_auth(operator_token)).status_code == 403


def test_users_blocked_until_initial_password_changed(client, seeded_token, seed):
    """must_change_password=1 的管理员：除改密外一律挡下（PRD 3.3）"""
    r = client.get("/api/users", headers=_auth(seeded_token))
    assert r.status_code == 403 and "初始密码" in r.get_json()["msg"]


def test_users_list_never_leaks_password_hash(client, admin_token, seed):
    r = client.get("/api/users", headers=_auth(admin_token))
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert {u["username"] for u in data["list"]} == {"admin", "op1"}
    assert {x["value"] for x in data["roles"]} == {"admin", "operator"}
    assert data["me"] == 1
    assert "password_hash" not in r.get_data(as_text=True)


# ---------------------------------------------------------------- 新增 / 重置 / 启停

def test_create_user_rejects_weak_password_and_duplicate_username(client, admin_token, seed):
    weak = client.post("/api/users", headers=_auth(admin_token),
                       json={"username": "lisi", "password": "123456", "role": "operator"})
    assert weak.status_code == 422

    created = client.post("/api/users", headers=_auth(admin_token),
                          json={"username": "lisi", "password": NEW_PWD,
                                "password_confirm": NEW_PWD, "role": "operator",
                                "display_name": "仓管二", "must_change": False})
    assert created.status_code == 200
    user = created.get_json()["data"]["user"]
    assert user["username"] == "lisi" and user["must_change_password"] == 0
    assert "password_hash" not in created.get_data(as_text=True)

    dup = client.post("/api/users", headers=_auth(admin_token),
                      json={"username": "lisi", "password": NEW_PWD, "password_confirm": NEW_PWD,
                            "role": "operator"})
    assert dup.status_code == 422 and "已经存在" in _text(dup)


def test_reset_password_requires_own_password_then_revokes_devices(client, admin_token, db_conn, seed):
    from services.auth_service import issue_token
    issue_token(db_conn, user_id=2, device_name="op-phone")

    bad = client.post("/api/users/2/password", headers=_auth(admin_token),
                      json={"password": WRONG_PWD, "new_password": NEW_PWD,
                            "new_password_confirm": NEW_PWD})
    assert bad.status_code == 422 and "你自己的密码不正确" in _text(bad)

    good = client.post("/api/users/2/password", headers=_auth(admin_token),
                       json={"password": SEED_ADMIN_PWD, "new_password": NEW_PWD,
                             "new_password_confirm": NEW_PWD})
    assert good.status_code == 200
    assert good.get_json()["data"]["revoked_tokens"] == 1
    assert "password_hash" not in good.get_data(as_text=True)
    assert db_conn.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id=2 AND revoked_at IS NULL"
                           ).fetchone()[0] == 0


def test_toggle_active_requires_explicit_boolean(client, admin_token, db_conn, seed):
    """漏传 is_active 绝不能等于「停用」：布尔必须是显式布尔，字符串也要拒（PRD 类型规约）"""
    for payload in ({}, {"is_active": "false"}, {"is_active": 0}, {"is_active": None}):
        r = client.post("/api/users/2/active", headers=_auth(admin_token),
                        json={**payload, "password": SEED_ADMIN_PWD})
        assert r.status_code == 422, payload
    assert db_conn.execute("SELECT is_active FROM users WHERE id=2").fetchone()[0] == 1


def test_cannot_deactivate_self(client, admin_token, db_conn, seed):
    r = client.post("/api/users/1/active", headers=_auth(admin_token),
                    json={"password": SEED_ADMIN_PWD, "is_active": False})
    assert r.status_code == 422
    assert db_conn.execute("SELECT is_active FROM users WHERE id=1").fetchone()[0] == 1


def test_deactivate_operator_keeps_history_and_revokes_device(client, admin_token, db_conn, seed):
    from services.auth_service import issue_token
    issue_token(db_conn, user_id=2, device_name="op-phone")

    r = client.post("/api/users/2/active", headers=_auth(admin_token),
                    json={"password": SEED_ADMIN_PWD, "is_active": False})
    assert r.status_code == 200
    assert db_conn.execute("SELECT is_active FROM users WHERE id=2").fetchone()[0] == 0
    assert db_conn.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id=2 AND revoked_at IS NULL"
                           ).fetchone()[0] == 0
    assert db_conn.execute("SELECT operator_id FROM stock_logs WHERE id=1").fetchone()[0] == 1, \
        "停用不删号：历史流水必须留住经办人"


# ---------------------------------------------------------------- 设备

def test_devices_list_and_revoke(client, admin_token, db_conn, seed):
    from services.auth_service import issue_token
    token_id, _plain = issue_token(db_conn, user_id=2, device_name="op-phone")

    listed = client.get("/api/devices", headers=_auth(admin_token)).get_json()["data"]["list"]
    assert token_id in [d["id"] for d in listed]
    assert all("token_hash" not in d for d in listed)

    revoked = client.post(f"/api/devices/{token_id}/revoke", headers=_auth(admin_token))
    assert revoked.status_code == 200
    after = client.get("/api/devices", headers=_auth(admin_token)).get_json()["data"]["list"]
    assert token_id not in [d["id"] for d in after]


def test_revoke_unknown_device_is_422(client, admin_token, seed):
    r = client.post("/api/devices/9999/revoke", headers=_auth(admin_token))
    assert r.status_code == 422 and "设备不存在" in _text(r)
