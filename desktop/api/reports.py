"""查询与报表接口 —— docs/03 第 6 章（5 个，含 CSV 导出）"""
from __future__ import annotations

from flask import Blueprint, Response, g, jsonify, request

from core.acl import require_admin, require_login
from core.errors import BizError
from core.response import ok
from services import dashboard_service, report_service

bp = Blueprint("api_reports", __name__)


def _args() -> dict:
    return request.args.to_dict()


@bp.get("/api/stock/logs")
@require_login
def logs():
    return jsonify(ok(report_service.query_logs(_args()))[0]), 200


@bp.get("/api/stock/logs/export")
@require_login
def export():
    body, total = report_service.export_csv(_args())
    from services import timeutil
    name = f"stock_logs_{timeutil.today_prefix()}.csv"
    return Response(body, mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-Row-Count": str(total),
    })


@bp.get("/api/stock/logs/<int:log_id>")
@require_login
def log_detail(log_id: int):
    """单条流水详情（冲销确认展示用）：复用列表页的列清单，字段名与列表完全一致。"""
    return jsonify(ok(report_service.get_log(log_id))[0]), 200


@bp.get("/api/stock/operators")
@require_login
def operators():
    """流水筛选的操作员下拉。只回 id/username/display_name，敏感列绝不出库（T-19）。"""
    return jsonify(ok({"list": report_service.list_operators()})[0]), 200


@bp.get("/api/stock/current")
@require_login
def current():
    args = _args()
    if str(args.get("negative_only", "")).lower() == "true" and g.user.get("role") != "admin":
        raise BizError(1004, "负库存排查仅供管理员使用")
    return jsonify(ok(report_service.query_current(args))[0]), 200


@bp.get("/api/stock/summary")
@require_login
def summary():
    return jsonify(ok(report_service.summarize(_args()))[0]), 200


@bp.get("/api/dashboard")
@require_login
def dashboard():
    return jsonify(ok(dashboard_service.summary())[0]), 200
