"""请求级鉴权 —— PRD 3.3 · docs/03 1.2

浏览器端与手机端**共用**同一套令牌（`X-Auth-Token`）：Flask-Login 会话随 Jinja 页面一起下线，
两套认证并存时最容易出的问题是「一边拦住了、另一边漏了」。
authenticate_token 必须在请求上下文内调用（需要 current_app 拿 DB 连接）。
返回 dict（含 id/username/role/display_name/must_change_password）或 None。
"""
from __future__ import annotations

from typing import Any

from models import database
from services.auth_service import hash_token
from services import timeutil


def authenticate_token(raw_token: str | None) -> dict[str, Any] | None:
    """校验设备令牌并返回用户；任何不通过（空/错/吊销/过期/账号停用）都返回 None。"""
    if not raw_token:
        return None
    conn = database.get_connection()
    row = conn.execute(
        """SELECT u.id, u.username, u.role, u.display_name, u.must_change_password, u.is_active,
                  t.id AS token_id, t.expires_at
             FROM api_tokens t
             JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ? AND t.revoked_at IS NULL""",
        (hash_token(raw_token),),
    ).fetchone()
    if row is None:
        return None
    if not row["is_active"]:
        return None
    expires = row["expires_at"]
    if expires and expires < timeutil.now_iso():  # ISO-8601 同时区，字典序即时间序
        return None
    conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (timeutil.now_iso(), row["token_id"]))
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "display_name": row["display_name"],
        "must_change_password": bool(row["must_change_password"]),
        "token_id": row["token_id"],
    }
