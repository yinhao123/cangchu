"""通用操作单接口（领用/退库/归还/借用/调拨）—— docs/03 第 12'' 章 · docs/13 第 6 章

薄层：取参 → 调 services → 包络。过账方向与库存扣减全在 services/operation_service.py。
权限：查询/新增/编辑/提交 = 已登录；审批/冲销/作废 = admin。
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from core.acl import require_admin, require_login
from core.response import ok
from services import operation_service, validators

bp = Blueprint("api_wms_operations", __name__)


def _filters() -> dict:
    args = request.args.to_dict()
    page, page_size = validators.clamp_page(args.get("page"), args.get("page_size"))
    args["page"], args["page_size"] = page, page_size
    return args


def _body() -> dict:
    return request.get_json(silent=True) or {}


@bp.get("/api/wms/operation-orders")
@require_login
def list_orders():
    return jsonify(ok(operation_service.list_orders(_filters()))[0]), 200


@bp.get("/api/wms/operation-orders/<int:order_id>")
@require_login
def order_detail(order_id: int):
    return jsonify(ok(operation_service.get_order(order_id))[0]), 200


@bp.post("/api/wms/operation-orders")
@require_login
def create_order():
    return jsonify(ok(operation_service.create_order(_body(), g.user))[0]), 200


@bp.put("/api/wms/operation-orders/<int:order_id>")
@require_login
def update_order(order_id: int):
    return jsonify(ok(operation_service.update_order(order_id, _body(), g.user))[0]), 200


@bp.post("/api/wms/operation-orders/<int:order_id>/submit")
@require_login
def submit_order(order_id: int):
    data = ok(operation_service.submit_order(order_id, g.user,
                                             trace_id=getattr(g, "trace_id", None)))[0]
    return jsonify(data), 200


@bp.post("/api/wms/operation-orders/<int:order_id>/approve")
@require_admin
def review_order(order_id: int):
    data = ok(operation_service.review_order(order_id, _body(), g.user,
                                             trace_id=getattr(g, "trace_id", None)))[0]
    return jsonify(data), 200


@bp.post("/api/wms/operation-orders/<int:order_id>/reverse")
@require_admin
def reverse_order(order_id: int):
    data = ok(operation_service.reverse_order(order_id, _body(), g.user,
                                              trace_id=getattr(g, "trace_id", None)))[0]
    return jsonify(data), 200


@bp.post("/api/wms/operation-orders/<int:order_id>/cancel")
@require_admin
def cancel_order(order_id: int):
    return jsonify(ok(operation_service.cancel_order(order_id, g.user))[0]), 200
