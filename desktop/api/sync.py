"""POST /api/sync/stock-logs —— docs/03 5.4 / PRD 5.5、9.5
单批 ≤200；逐条按序返回；业务错必须 4xx（APP 靠状态码类别决定是否停止自动重试）。
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from core.acl import require_login
from core.response import ok
from services import stock_service

bp = Blueprint("api_sync", __name__)


@bp.post("/api/sync/stock-logs")
@require_login
def sync_logs():
    payload = request.get_json(silent=True) or {}
    data = ok(stock_service.sync_batch(payload.get("logs") or [], g.user,
                                       device_name=payload.get("device_name"),
                                       trace_id=getattr(g, "trace_id", None)))[0]
    return jsonify(data), 200
