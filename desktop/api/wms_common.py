"""WMS 公共接口：审批待办 + CSV 导出 —— docs/03 第 12'.5 / 12'.6 节 · docs/13 第 6 章

薄层：取参 → 调 service → 包络 / 直接回 CSV。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request, Response

from core.acl import require_admin, require_login
from core.response import ok
from services import approval_service, wms_export_service

bp = Blueprint("api_wms_common", __name__)


@bp.get("/api/wms/approvals")
@require_admin
def pending_approvals():
    """审批待办（admin）：三类单据里 status=SUBMITTED 的合并视图，按提交时间倒序。"""
    return jsonify(ok(approval_service.list_pending(request.args.to_dict()))[0]), 200


@bp.get("/api/wms/export/<biz>")
@require_login
def export_bills(biz: str):
    """CSV 导出：biz ∈ purchase-orders / purchase-inbounds / operation-orders。

    ★ 不套 JSON 包络（与 /api/stock/logs/export 一致）：成功直接回文件，失败才回包络。
    """
    body, total, name = wms_export_service.export(biz, request.args.to_dict())
    return Response(body, mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-Row-Count": str(total),
    })
