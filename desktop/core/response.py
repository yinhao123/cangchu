"""统一响应包络 —— PRD 8.2 / docs/03 1.3 · 规则 20-stack-conventions.md

所有 API/网页出口必须经本模块，禁止散落 jsonify 手写字典（保证 trace_id 与包络一致）。
可空字段值保留为 None（JSON 输出 null），**永不省略键** —— 安卓 Retrofit data class 依赖键存在。
trace_id：优先复用 before_request 注入的请求级 ID，脱离请求上下文（脚本/测试）时随机生成 8 位。
"""
from __future__ import annotations

import secrets
from typing import Any

from core.errors import HTTP_BY_CODE

try:  # 请求上下文内用同一个 trace_id，让响应与日志能对上（PRD 10.4）
    from flask import g, has_request_context
except ImportError:  # pragma: no cover - 非 Flask 环境（纯脚本）下仍可构造响应字典
    g = None

    def has_request_context() -> bool:
        return False


def _trace_id() -> str:
    if has_request_context():
        tid = getattr(g, "trace_id", None)
        if tid:
            return tid
    return secrets.token_hex(4)


def envelope(data: Any = None, code: int = 0, msg: str = "ok", trace_id: str | None = None) -> dict:
    return {
        "code": code,
        "msg": msg,
        "data": {} if data is None else data,
        "trace_id": trace_id or _trace_id(),
    }


def ok(data: Any = None, msg: str = "ok", trace_id: str | None = None) -> tuple[dict, int]:
    """成功响应：HTTP 200 + code 0。"""
    return envelope(data, 0, msg, trace_id), 200


def fail(code: int, msg: str, data: Any = None, http_status: int | None = None,
         trace_id: str | None = None) -> tuple[dict, int]:
    """失败响应：code != 0 时 HTTP 必为非 2xx（未显式指定则查映射表，PRD 10.3）。"""
    status = http_status if http_status is not None else HTTP_BY_CODE.get(code, 500)
    if 200 <= status < 300:  # 不允许错误码配 2xx，防止 APP 误判成功
        status = 500
    return envelope(data, code, msg, trace_id), status
