"""业务单据打印接口 —— docs/03 第 12'''' 章 · docs/14 第 5.3 节（2 个）

薄层：取参 → 调 services → 包络。版式在前端渲染（红线 8：不在服务端新增渲染页面）。
权限：打印也是数据出口，与详情接口同级 —— 已登录即可；未激活/未登录照常被全局中间件拦。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from core.acl import require_login
from core.response import ok
from services import bill_print_service

bp = Blueprint("api_print", __name__)


@bp.get("/api/print/meta")
@require_login
def print_meta():
    return jsonify(ok(bill_print_service.meta())[0]), 200


@bp.get("/api/print/<biz>/<ids>")
@require_login
def print_document(biz: str, ids: str):
    """`ids` 支持逗号分隔批量打印（最多 20 张）；`mode` 仅盘点单用（blank / diff）。"""
    mode = (request.args.get("mode") or "").strip().lower() or None
    return jsonify(ok(bill_print_service.documents(biz, ids, mode))[0]), 200
