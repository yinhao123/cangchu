"""账号接口 —— docs/03 第 3 章
POST /api/login（换令牌）、POST /api/logout（吊销当前令牌）、GET /api/me、POST /api/me/password
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from core.acl import require_login
from core.errors import BizError
from core.response import ok
from models import database, user_repo
from services import auth_service, validators
from services.api_auth import authenticate_token

bp = Blueprint("api_auth", __name__)


@bp.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = validators.require_str(payload.get("username"), "username", max_len=32)
    password = payload.get("password")
    if not isinstance(password, str) or not (1 <= len(password) <= 64):
        raise BizError(1002, "参数校验失败", {"errors": {"password": ["长度需在 1~64 之间"]}})
    conn = database.get_connection()
    user = auth_service.login(conn, username, password, ip=request.remote_addr)
    _token_id, plain = auth_service.issue_token(
        conn, user["id"], device_name=validators.require_str(payload.get("device_name"), "device_name",
                                                             max_len=40, required=False),
        client_type=str(payload.get("client_type") or "android").lower())
    expires = conn.execute("SELECT expires_at FROM api_tokens WHERE token_hash = ?",
                           (auth_service.hash_token(plain),)).fetchone()["expires_at"]
    return jsonify(ok({"token": plain, "expires_at": expires, "user": _user_view(user)})[0]), 200


@bp.post("/api/logout")
@require_login
def logout():
    raw = request.headers.get("X-Auth-Token", "")
    auth_service.revoke_token(database.get_connection(), raw)
    return jsonify(ok({"revoked": True})[0]), 200  # 幂等：已吊销也算成功（docs/03 3.2）


@bp.get("/api/me")
@require_login
def me():
    row = user_repo.find_by_id(database.get_connection(), g.user["id"])
    expires = database.get_connection().execute(
        "SELECT expires_at FROM api_tokens WHERE id = ?", (g.user.get("token_id"),)).fetchone()
    return jsonify(ok({**_user_view(row), "token_expires_at": expires["expires_at"] if expires else None})[0]), 200


@bp.post("/api/me/password")
@require_login
def change_password():
    payload = request.get_json(silent=True) or {}
    conn = database.get_connection()
    updated = auth_service.change_password(conn, g.user["id"], payload.get("old_password") or "",
                                           payload.get("new_password") or "")
    return jsonify(ok(_user_view(updated))[0]), 200


def _user_view(row: dict) -> dict:
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
            "role": row["role"], "must_change_password": int(row["must_change_password"] or 0),
            "is_active": int(row["is_active"]), "last_login_at": row["last_login_at"]}
