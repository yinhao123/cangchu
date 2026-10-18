"""基础数据 11 个接口 —— docs/03 第 4 章（商品 6 + 仓库 3 + 往来单位 2）"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from core.acl import require_admin, require_login
from core.response import ok
from services import product_service, warehouse_service, partner_service

bp = Blueprint("api_products", __name__)


@bp.get("/api/products")
@require_login
def list_products():
    return jsonify(ok(product_service.list_products(request.args.to_dict()))[0]), 200


@bp.get("/api/products/by-barcode/<barcode>")
@require_login
def by_barcode(barcode: str):
    return jsonify(ok(product_service.get_for_scan(barcode))[0]), 200


@bp.get("/api/products/<int:product_id>")
@require_login
def detail(product_id: int):
    return jsonify(ok(product_service.get(product_id))[0]), 200


@bp.post("/api/products")
@require_admin
def create():
    return jsonify(ok(product_service.create(request.get_json(silent=True) or {}))[0]), 200


@bp.put("/api/products/<int:product_id>")
@require_admin
def update(product_id: int):
    return jsonify(ok(product_service.update(product_id, request.get_json(silent=True) or {}))[0]), 200


@bp.delete("/api/products/<int:product_id>")
@require_admin
def delete(product_id: int):
    hard = str(request.args.get("hard", "")).lower() == "true"
    return jsonify(ok(product_service.soft_delete(product_id, hard=hard))[0]), 200


@bp.post("/api/products/<int:product_id>/restore")
@require_admin
def restore(product_id: int):
    return jsonify(ok(product_service.restore(product_id))[0]), 200


@bp.get("/api/warehouses")
@require_login
def list_warehouses():
    return jsonify(ok(warehouse_service.list_warehouses(
        include_inactive=str(request.args.get("all", "")).lower() == "true"))[0]), 200


@bp.post("/api/warehouses")
@require_admin
def create_warehouse():
    return jsonify(ok(warehouse_service.create(request.get_json(silent=True) or {}))[0]), 200


@bp.put("/api/warehouses/<int:warehouse_id>")
@require_admin
def update_warehouse(warehouse_id: int):
    return jsonify(ok(warehouse_service.update(warehouse_id, request.get_json(silent=True) or {}))[0]), 200


@bp.get("/api/partners")
@require_login
def list_partners():
    return jsonify(ok(partner_service.list_partners(request.args.get("type")))[0]), 200


@bp.post("/api/partners")
@require_login
def create_partner():
    return jsonify(ok(partner_service.create(request.get_json(silent=True) or {}))[0]), 200
