"""GET /api/ping（免鉴权、零 DB 查询）与 GET /healthz —— PRD 5.8 / docs/03 2.1、2.3

★ 端口取 BOUND_PORT（实际绑定值），不是配置里的 HTTP_PORT —— 否则自动换端口后 APP 连到空气。
★ 不查数据库：探测必须极快且不受慢库阻塞；db_ready 用启动阶段确定的标志位。
"""
from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify

from core.response import envelope, ok

bp = Blueprint("api_ping", __name__)


@bp.get("/api/ping")
def api_ping():
    cfg = current_app.config
    return ok({
        "name": cfg.get("DISPLAY_NAME", "ScanableSelf"),
        "version": cfg.get("APP_VERSION", "0.0.0"),
        "protocol": "inventory_server",
        "protocol_version": "1.0",
        "ip": cfg.get("BOUND_IP") or "127.0.0.1",
        "port": int(cfg.get("BOUND_PORT") or cfg.get("HTTP_PORT", 5000)),
        "server_time": _now(),
        "db_ready": bool(cfg.get("DB_READY", False)),
        "broadcast_active": bool(cfg.get("BROADCAST_ACTIVE", False)),
    })


@bp.get("/healthz")
def healthz():
    """进程健康检查（供 bat/脚本判活）。DB 不可用 → degraded + 503。"""
    if current_app.config.get("DB_READY"):
        return Response("ok", status=200, mimetype="text/plain")
    return Response("degraded", status=503, mimetype="text/plain")


def _now() -> str:
    from services import timeutil
    return timeutil.now_iso()
