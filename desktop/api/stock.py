"""库存业务接口 —— docs/03 5.1~5.3
POST /api/stock/in | /out | /adjust | /reverse/<log_id>
★ 方向由服务端固定（type 不接受客户端传入），来源由 Header 判定（APP=其余=WEB）。
★ client_msg_id 必填（缺失 422，T-09）；幂等命中返回 200 + duplicated。
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from core.acl import require_admin, require_login
from core.response import ok
from services import stock_service

bp = Blueprint("api_stock", __name__)


def _source() -> str:
    return "APP" if request.headers.get("X-Auth-Token") else "WEB"


def _device() -> str | None:
    return (request.headers.get("X-Device-Name") or request.json.get("device_name")
            if request.is_json else None)


@bp.post("/api/stock/in")
@require_login
def stock_in():
    data = ok(stock_service.stock_in(request.get_json(silent=True) or {}, g.user,
                                     source=_source(), device_name=_device(),
                                     trace_id=getattr(g, "trace_id", None)))[0]
    return jsonify(data), 200


@bp.post("/api/stock/out")
@require_login
def stock_out():
    data = ok(stock_service.stock_out(request.get_json(silent=True) or {}, g.user,
                                      source=_source(), device_name=_device(),
                                      trace_id=getattr(g, "trace_id", None)))[0]
    return jsonify(data), 200


@bp.post("/api/stock/adjust")
@require_admin
def stock_adjust():
    data = ok(stock_service.stock_adjust(request.get_json(silent=True) or {}, g.user,
                                         source=_source(), device_name=_device(),
                                         trace_id=getattr(g, "trace_id", None)))[0]
    return jsonify(data), 200


@bp.post("/api/stock/reverse/<int:log_id>")
@require_admin
def reverse(log_id: int):
    data = ok(stock_service.reverse_log(log_id, request.get_json(silent=True) or {}, g.user,
                                        trace_id=getattr(g, "trace_id", None)))[0]
    return jsonify(data), 200
