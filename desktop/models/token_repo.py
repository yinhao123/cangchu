"""api_tokens 单条读写 —— 唯一 SQL 出口（user_repo 只管 users 的列，两处各写一半容易漏条件）

★ 只按 id 吊销「仍有效」的那一条（revoked_at IS NULL）：重复吊销返回 0 行视为成功，
  与 auth_service.revoke_token 的幂等口径一致（docs/03 3.2）。
"""
from __future__ import annotations

from services import timeutil

COLUMNS = "id, user_id, client_type, device_name, created_at, expires_at, last_used_at, revoked_at"


def find_by_id(conn, token_id: int) -> dict | None:
    row = conn.execute(f"SELECT {COLUMNS} FROM api_tokens WHERE id = ?", (int(token_id),)).fetchone()
    return dict(row) if row else None


def revoke_by_id(conn, token_id: int) -> int:
    return conn.execute(
        "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (timeutil.now_iso(), int(token_id))).rowcount
