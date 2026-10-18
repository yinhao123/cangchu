"""批量导入 / 导出接口 —— docs/14 第 3.5 节（4 个）

本层只做「取参 → 调 service → 包络」，解析与校验全在 `services/import_service.py`：
preview 与 commit 必须共用同一套校验，否则会出现「预检说没问题、提交却报错」。
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from core.acl import current_user, require_login
from core.errors import BizError
from core.response import ok
from services import import_service

bp = Blueprint("api_imports", __name__)


@bp.get("/api/imports/templates/<kind>")
@require_login
def template(kind: str):
    """下载 CSV 模板：utf-8-sig + CRLF，Excel 双击打开中文不乱码（T-10）。"""
    body, filename = import_service.template_csv(kind)
    return _csv(body, filename)


@bp.post("/api/imports/preview")
@require_login
def preview():
    """预检（不落库）：返回总行数、合法/非法行数与逐行错误。"""
    kind, raw = _upload()
    return jsonify(ok(import_service.preview(kind, raw))[0]), 200


@bp.post("/api/imports/commit")
@require_login
def commit():
    """正式导入。`skip_invalid=false`（默认）整批回滚；`true` 逐行 SAVEPOINT 隔离。"""
    kind, raw = _upload()
    skip_invalid = str(request.form.get("skip_invalid", "")).lower() in ("1", "true", "yes")
    user = current_user()
    if kind == "opening-stock" and user.get("role") != "admin":
        # 期初建账决定整个账本的起点，只允许管理员做（docs/14 3.5）
        raise BizError(1004, "期初建账仅管理员可执行")
    return jsonify(ok(import_service.commit(kind, raw, skip_invalid, user))[0]), 200


@bp.get("/api/imports/export/<kind>")
@require_login
def export(kind: str):
    body, filename, total = import_service.export_csv(kind)
    return _csv(body, import_service.export_filename(filename), total)


def _upload():
    """取 multipart 里的 `kind` 与 `file`；缺文件直接 1019（不猜、不给默认值）。"""
    kind = str(request.form.get("kind") or "").strip()
    storage = request.files.get("file")
    if storage is None:
        raise BizError(1019, "没有收到文件", {"errors": {"file": ["请选择 CSV 文件后再上传"]}})
    return kind, storage.read()


def _csv(body: bytes, filename: str, total=None):
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if total is not None:
        headers["X-Row-Count"] = str(total)
    return Response(body, mimetype="text/csv", headers=headers)
