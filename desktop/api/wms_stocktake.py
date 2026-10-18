"""盘点单接口 —— docs/03 第 12''' 章 · docs/14 第 4.6 节（9 个）
薄层：取参 → 调 services → 包络。状态机与过账全在 services/stocktake_service.py。
权限：查询/建单/编辑/提交 = 已登录；驳回/过账 = admin（与采购入库的审批同级）。
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from core.acl import require_admin, require_login
from core.response import ok
from services import stocktake_service, validators

bp = Blueprint("api_wms_stocktake", __name__)


def _filters() -> dict:
    args = request.args.to_dict()
    page, page_size = validators.clamp_page(args.get("page"), args.get("page_size"))
    args["page"], args["page_size"] = page, page_size
    return args


def _body() -> dict:
    return request.get_json(silent=True) or {}


@bp.get("/api/wms/stocktakes")
@require_login
def list_stocktakes():
    return jsonify(ok(stocktake_service.list_stocktakes(_filters()))[0]), 200


@bp.post("/api/wms/stocktakes")
@require_login
def create_stocktake():
    return jsonify(ok(stocktake_service.create_stocktake(_body(), g.user))[0]), 200


@bp.get("/api/wms/stocktakes/<int:stocktake_id>")
@require_login
def stocktake_detail(stocktake_id: int):
    return jsonify(ok(stocktake_service.get_stocktake(stocktake_id))[0]), 200


@bp.put("/api/wms/stocktakes/<int:stocktake_id>")
@require_login
def update_stocktake(stocktake_id: int):
    return jsonify(ok(stocktake_service.update_stocktake(stocktake_id, _body(), g.user))[0]), 200


@bp.delete("/api/wms/stocktakes/<int:stocktake_id>")
@require_login
def cancel_stocktake(stocktake_id: int):
    """作废（不是删除）：只置 CANCELED，明细与流水一律保留。"""
    return jsonify(ok(stocktake_service.cancel_stocktake(stocktake_id, g.user))[0]), 200


@bp.post("/api/wms/stocktakes/<int:stocktake_id>/submit")
@require_login
def submit_stocktake(stocktake_id: int):
    return jsonify(ok(stocktake_service.submit_stocktake(stocktake_id, g.user))[0]), 200


@bp.post("/api/wms/stocktakes/<int:stocktake_id>/reject")
@require_admin
def reject_stocktake(stocktake_id: int):
    return jsonify(ok(stocktake_service.reject_stocktake(stocktake_id, _body(), g.user))[0]), 200


@bp.post("/api/wms/stocktakes/<int:stocktake_id>/post")
@require_admin
def post_stocktake(stocktake_id: int):
    data = ok(stocktake_service.post_stocktake(stocktake_id, g.user,
                                               trace_id=getattr(g, "trace_id", None)))[0]
    return jsonify(data), 200


@bp.get("/api/wms/stocktakes/<int:stocktake_id>/print")
@require_login
def print_stocktake(stocktake_id: int):
    mode = request.args.get("mode") or "blank"
    return jsonify(ok(stocktake_service.print_data(stocktake_id, mode))[0]), 200
