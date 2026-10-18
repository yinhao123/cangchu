"""账号与设备接口 —— PRD 5.1 / 5.10 · docs/03 第 9 章

全部 `require_admin`：账号与设备属管理员专属（PRD 3.1 权限矩阵）。
「重置口令 / 停用账号」必须再输**操作人自己**的密码（PRD 265），因此入参里透传 `password`；
校验留在 `services/user_service`（api 层不判业务规则，CODEBUDDY.md 分层约束）。
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from core.acl import require_admin
from core.errors import BizError
from core.response import ok
from models import database
from services import auth_service, user_service

bp = Blueprint("api_users", __name__)


def _role_options() -> list[dict]:
    return [{"value": role, "label": user_service.ROLE_TEXT[role]} for role in user_service.ROLES]


def _public(user: dict | None) -> dict | None:
    """剥掉 `password_hash` 再出网。

    ★ `user_service` 的写操作返回的是 `user_repo.find_by_id()` 的完整行（含哈希），
    网页端不回显所以看不出来；接口层直接透传就等于把口令哈希发给浏览器（T-19 / PRD 10.4）。
    """
    if user is None:
        return None
    return {k: v for k, v in user.items() if k != "password_hash"}


@bp.get("/api/users")
@require_admin
def list_users():
    conn = database.get_connection()
    return jsonify(ok({
        "list": user_service.list_users(conn),
        "roles": _role_options(),
        "me": int(g.user.get("id") or 0),
    })[0]), 200


@bp.post("/api/users")
@require_admin
def create_user():
    payload = request.get_json(silent=True) or {}
    user = user_service.create_user(
        database.get_connection(),
        operator=g.user,
        username=payload.get("username") or "",
        password=payload.get("password") or "",
        confirm=payload.get("password_confirm"),
        role=payload.get("role") or "operator",
        display_name=payload.get("display_name"),
        must_change=bool(payload.get("must_change", True)),
    )
    return jsonify(ok({"user": _public(user)})[0]), 200


@bp.post("/api/users/<int:user_id>/password")
@require_admin
def reset_password(user_id: int):
    payload = request.get_json(silent=True) or {}
    result = user_service.reset_password(
        database.get_connection(),
        operator=g.user,
        user_id=user_id,
        new_password=payload.get("new_password") or "",
        confirm=payload.get("new_password_confirm"),
        operator_password=payload.get("password") or "",
    )
    return jsonify(ok({**result, "user": _public(result.get("user"))})[0]), 200


@bp.post("/api/users/<int:user_id>/active")
@require_admin
def toggle_active(user_id: int):
    payload = request.get_json(silent=True) or {}
    # ★不能用 bool(payload.get(...))：字段漏传会被当成 False 静默停用账号，
    #   字符串 "false" 也会被判成 True。布尔必须是**显式**的布尔（PRD 规约：布尔只认 0/1 与真布尔）。
    if not isinstance(payload.get("is_active"), bool):
        raise BizError(1002, "参数校验失败",
                       {"errors": {"is_active": ["必须是布尔值 true / false"]}})
    result = user_service.set_active(
        database.get_connection(),
        operator=g.user,
        user_id=user_id,
        is_active=payload["is_active"],
        operator_password=payload.get("password") or "",
    )
    return jsonify(ok({**result, "user": _public(result.get("user"))})[0]), 200


@bp.get("/api/devices")
@require_admin
def list_devices():
    return jsonify(ok({"list": auth_service.list_active_tokens(database.get_connection())})[0]), 200


@bp.post("/api/devices/<int:token_id>/revoke")
@require_admin
def revoke_device(token_id: int):
    return jsonify(ok(user_service.revoke_device(
        database.get_connection(), operator=g.user, token_id=token_id))[0]), 200
