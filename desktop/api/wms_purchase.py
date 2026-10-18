"""采购订单 / 采购入库单 接口 —— docs/03 第 13 章 · docs/13 第 6 章

薄层：取参 → 调 services → 包络。业务规则与事务都在 services/purchase_service.py。
权限沿用两级：查询/新增/编辑/提交 = 已登录；审批/作废 = admin。
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from core.acl import require_admin, require_login
from core.response import ok
from services import purchase_service, validators

bp = Blueprint("api_wms_purchase", __name__)


def _filters() -> dict:
    args = request.args.to_dict()
    page, page_size = validators.clamp_page(args.get("page"), args.get("page_size"))
    args["page"], args["page_size"] = page, page_size
    return args


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _trace() -> str | None:
    return getattr(g, "trace_id", None)


# ---------------- 采购订单 ----------------

@bp.get("/api/wms/purchase-orders")
@require_login
def list_orders():
    return jsonify(ok(purchase_service.list_orders(_filters()))[0]), 200


@bp.get("/api/wms/purchase-orders/<int:order_id>")
@require_login
def order_detail(order_id: int):
    return jsonify(ok(purchase_service.get_order(order_id))[0]), 200


@bp.post("/api/wms/purchase-orders")
@require_login
def create_order():
    return jsonify(ok(purchase_service.create_order(_body(), g.user))[0]), 200


@bp.put("/api/wms/purchase-orders/<int:order_id>")
@require_login
def update_order(order_id: int):
    return jsonify(ok(purchase_service.update_order(order_id, _body(), g.user))[0]), 200


@bp.post("/api/wms/purchase-orders/<int:order_id>/submit")
@require_login
def submit_order(order_id: int):
    return jsonify(ok(purchase_service.submit_order(order_id, g.user))[0]), 200


@bp.post("/api/wms/purchase-orders/<int:order_id>/approve")
@require_admin
def review_order(order_id: int):
    return jsonify(ok(purchase_service.review_order(order_id, _body(), g.user))[0]), 200


@bp.post("/api/wms/purchase-orders/<int:order_id>/cancel")
@require_admin
def cancel_order(order_id: int):
    return jsonify(ok(purchase_service.cancel_order(order_id, g.user))[0]), 200


# ---------------- 采购入库单 ----------------

@bp.get("/api/wms/purchase-inbounds")
@require_login
def list_inbounds():
    return jsonify(ok(purchase_service.list_inbounds(_filters()))[0]), 200


@bp.get("/api/wms/purchase-inbounds/<int:inbound_id>")
@require_login
def inbound_detail(inbound_id: int):
    return jsonify(ok(purchase_service.get_inbound(inbound_id))[0]), 200


@bp.post("/api/wms/purchase-inbounds")
@require_login
def create_inbound():
    return jsonify(ok(purchase_service.create_inbound(_body(), g.user))[0]), 200


@bp.put("/api/wms/purchase-inbounds/<int:inbound_id>")
@require_login
def update_inbound(inbound_id: int):
    return jsonify(ok(purchase_service.update_inbound(inbound_id, _body(), g.user))[0]), 200


@bp.post("/api/wms/purchase-inbounds/<int:inbound_id>/submit")
@require_login
def submit_inbound(inbound_id: int):
    return jsonify(ok(purchase_service.submit_inbound(inbound_id, g.user))[0]), 200


@bp.post("/api/wms/purchase-inbounds/<int:inbound_id>/approve")
@require_admin
def review_inbound(inbound_id: int):
    data = ok(purchase_service.review_inbound(inbound_id, _body(), g.user, trace_id=_trace()))[0]
    return jsonify(data), 200


@bp.post("/api/wms/purchase-inbounds/<int:inbound_id>/cancel")
@require_admin
def cancel_inbound(inbound_id: int):
    return jsonify(ok(purchase_service.cancel_inbound(inbound_id, g.user))[0]), 200
