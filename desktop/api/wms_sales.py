"""销售订单 / 发货 / 销售退货 / 客户对账 接口 —— docs/03 第 12''''' 章 · docs/14 第 6 章（P1-4）

薄层：取参 → 调 services → 包络。状态机、超发拦截、过账都在 services/sales_service.py。
权限：查询/开单/编辑/提交/发货 = 已登录；审批/驳回/作废 = admin（与采购侧一致）。
"""
from __future__ import annotations

from flask import Blueprint, Response, g, jsonify, request

from core.acl import require_admin, require_login
from core.response import ok
from services import sales_service, validators

bp = Blueprint("api_wms_sales", __name__)


def _filters() -> dict:
    args = request.args.to_dict()
    page, page_size = validators.clamp_page(args.get("page"), args.get("page_size"))
    args["page"], args["page_size"] = page, page_size
    return args


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _trace() -> str | None:
    return getattr(g, "trace_id", None)


# ---------------- 销售订单（销货单） ----------------

@bp.get("/api/wms/sales-orders")
@require_login
def list_orders():
    return jsonify(ok(sales_service.list_orders(_filters()))[0]), 200


@bp.post("/api/wms/sales-orders")
@require_login
def create_order():
    return jsonify(ok(sales_service.create_order(_body(), g.user))[0]), 200


@bp.get("/api/wms/sales-orders/<int:order_id>")
@require_login
def order_detail(order_id: int):
    return jsonify(ok(sales_service.get_order(order_id))[0]), 200


@bp.put("/api/wms/sales-orders/<int:order_id>")
@require_login
def update_order(order_id: int):
    return jsonify(ok(sales_service.update_order(order_id, _body(), g.user))[0]), 200


@bp.post("/api/wms/sales-orders/<int:order_id>/submit")
@require_login
def submit_order(order_id: int):
    return jsonify(ok(sales_service.submit_order(order_id, g.user))[0]), 200


@bp.post("/api/wms/sales-orders/<int:order_id>/approve")
@require_admin
def approve_order(order_id: int):
    # 与采购侧同构：/approve 也接受 body.action（缺省 APPROVE），前端一套 review 调用就够
    body = dict(_body())
    body.setdefault("action", "APPROVE")
    return jsonify(ok(sales_service.review_order(order_id, body, g.user))[0]), 200


@bp.post("/api/wms/sales-orders/<int:order_id>/reject")
@require_admin
def reject_order(order_id: int):
    body = dict(_body())
    body["action"] = "REJECT"
    return jsonify(ok(sales_service.review_order(order_id, body, g.user))[0]), 200


@bp.post("/api/wms/sales-orders/<int:order_id>/ship")
@require_login
def ship_order(order_id: int):
    """发货出库：可多次（部分发货），发满自动 CLOSED；超发 409/1016。"""
    data = ok(sales_service.ship_order(order_id, _body(), g.user, trace_id=_trace()))[0]
    return jsonify(data), 200


@bp.post("/api/wms/sales-orders/<int:order_id>/cancel")
@require_admin
def cancel_order(order_id: int):
    return jsonify(ok(sales_service.cancel_order(order_id, g.user))[0]), 200


# ---------------- 销售退货 ----------------

@bp.get("/api/wms/sales-returns")
@require_login
def list_returns():
    return jsonify(ok(sales_service.list_returns(_filters()))[0]), 200


@bp.post("/api/wms/sales-returns")
@require_login
def create_return():
    return jsonify(ok(sales_service.create_return(_body(), g.user))[0]), 200


@bp.get("/api/wms/sales-returns/<int:return_id>")
@require_login
def return_detail(return_id: int):
    return jsonify(ok(sales_service.get_return(return_id))[0]), 200


@bp.put("/api/wms/sales-returns/<int:return_id>")
@require_login
def update_return(return_id: int):
    return jsonify(ok(sales_service.update_return(return_id, _body(), g.user))[0]), 200


@bp.post("/api/wms/sales-returns/<int:return_id>/submit")
@require_login
def submit_return(return_id: int):
    return jsonify(ok(sales_service.submit_return(return_id, g.user))[0]), 200


@bp.post("/api/wms/sales-returns/<int:return_id>/approve")
@require_admin
def approve_return(return_id: int):
    body = dict(_body())
    body.setdefault("action", "APPROVE")
    return jsonify(ok(sales_service.review_return(return_id, body, g.user, trace_id=_trace()))[0]), 200


@bp.post("/api/wms/sales-returns/<int:return_id>/reject")
@require_admin
def reject_return(return_id: int):
    body = dict(_body())
    body["action"] = "REJECT"
    return jsonify(ok(sales_service.review_return(return_id, body, g.user))[0]), 200


@bp.post("/api/wms/sales-returns/<int:return_id>/cancel")
@require_admin
def cancel_return(return_id: int):
    return jsonify(ok(sales_service.cancel_return(return_id, g.user))[0]), 200


# ---------------- 客户对账 ----------------

@bp.get("/api/wms/customer-statement")
@require_login
def customer_statement():
    data = sales_service.customer_statement(request.args.to_dict())
    if str(request.args.get("export") or "").lower() == "csv":
        payload = sales_service.statement_csv(data)
        filename = f"customer-statement-{data['customer']['id']}-{data['from']}_{data['to']}.csv"
        resp = Response(payload, mimetype="text/csv; charset=utf-8")
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        resp.headers["X-Row-Count"] = str(data["totals"]["line_count"])
        return resp
    return jsonify(ok(data)[0]), 200
