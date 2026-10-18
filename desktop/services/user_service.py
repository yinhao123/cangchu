"""账号管理业务层 —— PRD 5.1 / 5.10 / 265

★ 账号**只停用、不删除**：stock_logs.operator_id 非空引用 users，删掉会让历史流水失去经办人，
  「这笔是谁做的」必须永远查得到——审计要求优先于页面整洁。
★ 管理员重置他人口令 / 停用他人账号，必须再次输入**自己**的密码（PRD 265）。
★ 重置口令与停用都要吊销该用户全部设备令牌：手机令牌有效 90 天，
  不吊销等于「人走了、旧手机还能继续出入库」。
"""
from __future__ import annotations

import logging
import re

from core.errors import BizError
from core.logging_setup import business
from models import token_repo, user_repo
from services import auth_service

log = logging.getLogger("scanable.user")

#: 用户名规则（PRD 5.1）：字母/数字/下划线/点，2~32 位。含中文或空格会在 URL、日志、APP 里到处出问题
USERNAME_RE = re.compile(r"^[0-9A-Za-z_.]{2,32}$")
ROLES = ("admin", "operator")
ROLE_TEXT = {"admin": "管理员", "operator": "操作员"}


def list_users(conn) -> list[dict]:
    return user_repo.list_all(conn)


def create_user(conn, *, operator: dict, username: str, password: str, role: str,
                display_name: str | None = None, confirm: str | None = None,
                must_change: bool = True) -> dict:
    """新增账号。must_change 默认开（PRD 首次登录强制改密），但允许管理员关掉。"""
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise BizError(1002, "参数校验失败",
                       {"errors": {"username": ["用户名只能用字母、数字、下划线、点，长度 2~32 位"]}})
    if role not in ROLES:
        raise BizError(1002, "参数校验失败", {"errors": {"role": ["角色只能是管理员或操作员"]}})
    if confirm is not None and password != confirm:
        raise BizError(1002, "参数校验失败", {"errors": {"password_confirm": ["两次输入的密码不一致"]}})
    auth_service.check_password_strength(password)
    if user_repo.find_by_username(conn, username) is not None:
        raise BizError(1002, "参数校验失败", {"errors": {"username": ["这个用户名已经存在"]}})
    user = user_repo.create(conn, username, auth_service.hash_password(password), role=role,
                            display_name=(display_name or None),
                            must_change_password=1 if must_change else 0)
    business(log, logging.INFO, f"新增账号 {username}", action="user.create",
             operator=operator.get("username"), target=username, role=role)
    return user


def reset_password(conn, *, operator: dict, user_id: int, new_password: str,
                   confirm: str | None = None, operator_password: str = "") -> dict:
    """管理员重置口令：必须再输自己的密码；重置后强制下次登录改密，并踢掉该用户所有手机。"""
    target = _require_user(conn, user_id)
    _require_operator_password(conn, operator, operator_password)
    if confirm is not None and new_password != confirm:
        raise BizError(1002, "参数校验失败", {"errors": {"new_password_confirm": ["两次输入的密码不一致"]}})
    auth_service.check_password_strength(new_password)
    user_repo.set_password(conn, user_id, auth_service.hash_password(new_password),
                           clear_must_change=False)
    revoked = auth_service.revoke_all_for_user(conn, int(user_id))
    business(log, logging.INFO, f"重置账号口令 {target['username']}", action="user.reset_password",
             operator=operator.get("username"), target=target["username"],
             revoked_tokens=revoked)
    return {"user": target, "revoked_tokens": revoked}


def set_active(conn, *, operator: dict, user_id: int, is_active: bool,
               operator_password: str = "") -> dict:
    """启用/停用。停用不删号（历史流水要留经办人），但要立刻踢掉该用户的手机令牌。"""
    target = _require_user(conn, user_id)
    if not is_active:
        if int(user_id) == int(operator.get("id") or 0):
            raise BizError(1002, "参数校验失败", {"errors": {"user": ["不能停用自己的账号，请找另一位管理员"]}})
        if target["role"] == "admin" and _active_admins(conn) <= 1:
            raise BizError(1002, "参数校验失败",
                           {"errors": {"user": ["这是最后一个启用的管理员，停用后就没人能管理账号了"]}})
    _require_operator_password(conn, operator, operator_password)
    user_repo.set_active(conn, int(user_id), 1 if is_active else 0)
    revoked = auth_service.revoke_all_for_user(conn, int(user_id)) if not is_active else 0
    business(log, logging.INFO, f"{'停用' if not is_active else '启用'}账号 {target['username']}",
             action="user.set_active", operator=operator.get("username"),
             target=target["username"], active=bool(is_active), revoked_tokens=revoked)
    return {"user": target, "revoked_tokens": revoked}


def revoke_device(conn, *, operator: dict, token_id: int) -> dict:
    """吊销一台已配对设备（换手机 / 手机丢了）。"""
    token = token_repo.find_by_id(conn, token_id)
    if token is None:
        raise BizError(1002, "参数校验失败", {"errors": {"device": ["设备不存在或已被移除"]}})
    token_repo.revoke_by_id(conn, token_id)
    business(log, logging.INFO, f"吊销设备 {token.get('device_name') or token_id}",
             action="user.revoke_device", operator=operator.get("username"), token_id=int(token_id))
    return {"token": token}


def _require_user(conn, user_id: int) -> dict:
    row = user_repo.find_by_id(conn, int(user_id))
    if row is None:
        raise BizError(1002, "参数校验失败", {"errors": {"user": ["账号不存在"]}})
    return row


def _require_operator_password(conn, operator: dict, password: str) -> None:
    """证明操作人就是本人。用 verify_password 而不是明文比对（PRD 5.1 口令必须哈希存储）。"""
    row = user_repo.find_by_id(conn, int(operator.get("id") or 0))
    if row is None or not auth_service.verify_password(password or "", row.get("password_hash")):
        raise BizError(1002, "参数校验失败", {"errors": {"password": ["你自己的密码不正确"]}})


def _active_admins(conn) -> int:
    return sum(1 for u in user_repo.list_all(conn) if u["role"] == "admin" and u["is_active"])
