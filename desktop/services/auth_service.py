"""认证服务 —— PRD 5.1 / 3.3 · docs/03 第 3 章

口令：pbkdf2:sha256:600000（纯 Python，避免 bcrypt 的 C 扩展打包麻烦）。
错误文案：账号不存在与密码错误必须是同一句话（防账号枚举）。
锁定：见 services/login_guard.py。
令牌：只存 SHA-256 哈希；同设备重登 → 作废旧的发新（哈希不可逆，物理上无法复用明文）。
"""
from __future__ import annotations

import re
import secrets as _secrets
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from core.errors import BizError
from services import login_guard, timeutil

PBKDF2_METHOD = "pbkdf2:sha256:600000"
GENERIC_LOGIN_ERROR = "账号或密码错误"
_PWD_MIN_LEN = 8
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"[0-9]")


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=PBKDF2_METHOD)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return check_password_hash(password_hash, password)
    except Exception:  # 哈希串损坏也判失败，不能让登录页 500
        return False


def hash_token(token: str) -> str:
    return hashlib_sha256(token)


def hashlib_sha256(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def check_password_strength(password: str) -> None:
    errs: list[str] = []
    text = password or ""
    if len(text) < _PWD_MIN_LEN:
        errs.append(f"密码至少 {_PWD_MIN_LEN} 位")
    if not _HAS_LETTER.search(text):
        errs.append("密码必须包含字母")
    if not _HAS_DIGIT.search(text):
        errs.append("密码必须包含数字")
    if len(text) > 64:
        errs.append("密码过长（最多 64 位）")
    if errs:
        raise BizError(1002, "参数校验失败", {"errors": {"new_password": errs}})


def login(conn, username: str, password: str, ip: str | None = None) -> dict:
    """网页与 API 共用的凭据校验；成功返回用户 dict，失败抛 BizError。"""
    wait = login_guard.check(username or "", ip)
    if wait:
        minutes = max(1, round(wait / 60))
        raise BizError(1005, f"失败次数过多，请 {minutes} 分钟后再试（约 {wait} 秒）",
                       {"retry_after": wait})
    user = _repo_find(conn, username)
    if user is None or not verify_password(password or "", user["password_hash"]):
        login_guard.record_failure(username or "", ip)
        raise BizError(1003, GENERIC_LOGIN_ERROR)  # 不区分「账号不存在」与「密码错」
    if not user["is_active"]:
        raise BizError(1004, "该账号已停用，请联系管理员")
    from models import user_repo
    user_repo.mark_login(conn, user["id"])
    login_guard.reset(username, ip)
    return user


def _repo_find(conn, username):
    from models import user_repo
    return user_repo.find_by_username(conn, username or "")


def change_password(conn, user_id: int, old_password: str, new_password: str) -> dict:
    from models import user_repo
    user = user_repo.find_by_id(conn, user_id)
    if user is None:
        raise BizError(1006, "用户不存在")
    if not verify_password(old_password or "", user["password_hash"]):
        # 不能用 1003：那个码的语义是「登录/Token 失效」，客户端收到会清 Token 回配对页——
        # 把「原密码打错」显示成「登录失效」会把用户直接踢出去（docs/03 1.3 错误码语义）。
        raise BizError(1002, "参数校验失败", {"errors": {"old_password": ["原密码不正确"]}})
    check_password_strength(new_password)
    if verify_password(new_password, user["password_hash"]):
        raise BizError(1002, "参数校验失败", {"errors": {"new_password": ["新密码不能与原密码相同"]}})
    user_repo.set_password(conn, user_id, hash_password(new_password), clear_must_change=True)
    return user_repo.find_by_id(conn, user_id)


def issue_token(conn, user_id: int, device_name: str | None = None,
                client_type: str = "android", days: int = 90) -> tuple[int, str]:
    """签发设备令牌，返回 (token_id, 明文)。明文只在这里出现一次。"""
    now = timeutil.now_iso()
    expires_at = (timeutil.parse_iso(now) + timedelta(days=days)).isoformat(timespec="seconds") if days else None
    if days > 0:
        conn.execute(
            """UPDATE api_tokens SET revoked_at = ?
                WHERE user_id = ? AND IFNULL(device_name,'') = ? AND revoked_at IS NULL""",
            (now, user_id, device_name or ""))
    plain = _secrets.token_hex(24)  # 48 位 hex
    cur = conn.execute(
        """INSERT INTO api_tokens(user_id, token_hash, client_type, device_name, created_at, expires_at)
           VALUES(?,?,?,?,?,?)""",
        (user_id, hash_token(plain), client_type, device_name, now, expires_at))
    return int(cur.lastrowid), plain


def revoke_token(conn, raw_token: str) -> int:
    """logout：已吊销再调返回 0 行，仍视为成功（幂等，docs/03 3.2）。"""
    return conn.execute(
        "UPDATE api_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
        (timeutil.now_iso(), hash_token(raw_token))).rowcount


def revoke_all_for_user(conn, user_id: int, except_token_id: int | None = None) -> int:
    sql = "UPDATE api_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL"
    args: list = [timeutil.now_iso(), user_id]
    if except_token_id is not None:
        sql += " AND id != ?"
        args.append(except_token_id)
    return conn.execute(sql, tuple(args)).rowcount


def list_active_tokens(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT t.id, t.device_name, t.client_type, t.created_at, t.last_used_at, t.expires_at, u.username
             FROM api_tokens t JOIN users u ON u.id = t.user_id
            WHERE t.revoked_at IS NULL ORDER BY t.created_at DESC""").fetchall()
    return [dict(r) for r in rows]
