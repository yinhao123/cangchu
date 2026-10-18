"""系统与配对接口 —— docs/03 2.2 / 第 7 章（qrcode、settings 读写、backup 三个）"""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, g, jsonify, request

from core.acl import require_admin, require_login
from core.errors import BizError
from core.response import ok
from models import database
from net import local_ip, qrcode_gen
from services import backup_service, dashboard_service, settings_service

bp = Blueprint("api_system", __name__)
log = logging.getLogger("scanable.api.system")

#: 需要再次输入本人口令才能改的配置项（改错会让手机连不上本机，PRD 265）
NETWORK_KEYS: frozenset[str] = frozenset({"http_port", "broadcast_enabled"})


@bp.get("/api/qrcode")
@require_login
def qrcode():
    """`?host=` 可指定网卡（多网卡机器，PRD 5.8），但只在候选表内取值（见 local_ip.pick_advertised）。"""
    cfg = current_app.config
    port = int(cfg.get("BOUND_PORT") or cfg.get("HTTP_PORT", 5000))
    host = local_ip.pick_advertised(request.args.get("host"), cfg.get("BOUND_IP"))
    payload = qrcode_gen.build_payload(host, port, cfg.get("DISPLAY_NAME"))
    from services import timeutil
    return jsonify(ok({"payload_json": payload, "image_png_base64": qrcode_gen.to_png_base64(payload),
                       "host": host, "port": port, "expires_at": _expires(),
                       "candidates": local_ip.candidates(),
                       "text": f"{host}:{port}"})[0]), 200


def _expires() -> str:
    from datetime import timedelta
    from services import timeutil
    return (timeutil.now_dt() + timedelta(minutes=10)).isoformat(timespec="seconds")


@bp.get("/api/settings")
@require_admin
def get_settings():
    return jsonify(ok(settings_service.get_all_view())[0]), 200


@bp.post("/api/settings")
@bp.put("/api/settings")
@require_admin
def put_settings():
    payload = request.get_json(silent=True) or {}
    _reject_network_keys(payload.get("items"))
    return jsonify(ok(settings_service.update_many(payload, g.user))[0]), 200


def _reject_network_keys(items) -> None:
    """把 `http_port`/`broadcast_enabled` 从通用设置口赶走（PRD 3.5 / docs/01 267）。

    ★这是**唯一**有效的收口点：网页端用 `exclude=NETWORK_KEYS` 挡过一次，
      但 `/api/settings` 不挡的话，那道「改端口要再输本人口令」的闸一条请求就能绕过 ——
      后果是全屋手机静默失联，要重启服务才发现。二者必须走 `/api/settings/network`。
    """
    if not isinstance(items, dict):
        return
    hits = sorted(set(items) & NETWORK_KEYS)
    if hits:
        raise BizError(1002, "参数校验失败", {"errors": {"items": [
            "这些项必须走 /api/settings/network（需要再输一次本人口令）：" + "、".join(hits)]}})


@bp.post("/api/backup")
@require_admin
def do_backup():
    info = backup_service.create_backup(trigger="manual")
    return jsonify(ok({k: info[k] for k in ("filename", "size_bytes", "created_at", "trigger")})[0]), 200


@bp.get("/api/backup/list")
@require_admin
def backup_list():
    items = backup_service.list_backups()
    return jsonify(ok({"list": items, "total": len(items),
                       "status": backup_service.status()})[0]), 200


@bp.post("/api/backup/restore")
@require_admin
def backup_restore():
    """二次确认：必须输入当前账号密码（PRD 3.5），且恢复前自动保住现库。"""
    from core.errors import BizError
    from services import auth_service
    payload = request.get_json(silent=True) or {}
    conn = database.get_connection()
    user = conn.execute("SELECT password_hash FROM users WHERE id=?", (g.user["id"],)).fetchone()
    if not auth_service.verify_password(payload.get("password") or "", user["password_hash"]):
        # 1002/422 而非 1003/401：1003 被客户端定义为「登录已失效」，会清 Token 跳登录页；
        # 口令打错却把人踢下线，是最容易被误报成「系统乱踢人」的一类缺陷。
        raise BizError(1002, "参数校验失败",
                       {"errors": {"password": ["密码不正确，无法执行恢复"]}})
    filename = payload.get("filename")
    if not filename:
        raise BizError(1002, "参数校验失败", {"errors": {"filename": ["必须指定备份文件"]}})
    import os
    from core.paths import backups_dir, db_path
    if "/" in filename or ".." in filename or not filename.startswith("inventory_"):
        raise BizError(1002, "参数校验失败", {"errors": {"filename": ["文件名不合法"]}})
    source = os.path.join(backups_dir(), filename)
    backup_service.create_backup(trigger="pre_restore")
    return jsonify(ok(backup_service.restore_backup(source, target_db=db_path()))[0]), 200


@bp.post("/api/settings/network")
@require_admin
def put_network_settings():
    """端口 / 自动发现：必须再输一次自己的密码（PRD 265）。

    ★ 与 `/api/settings` 分开是**安全设计**：那一个同为 require_admin 但不校验本人口令，
    把 `http_port`/`broadcast_enabled` 混在里面改，等于把网页上那道闸悄悄拆掉。
    """
    from services import auth_service

    payload = request.get_json(silent=True) or {}
    items = payload.get("items")
    if not isinstance(items, dict) or not items:
        raise BizError(1002, "参数校验失败", {"errors": {"items": ["没有需要保存的改动"]}})
    illegal = sorted(set(items) - NETWORK_KEYS)
    if illegal:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"items": ["这些项不属于网络设置：" + "、".join(illegal)]}})

    conn = database.get_connection()
    row = conn.execute("SELECT password_hash FROM users WHERE id=?", (g.user["id"],)).fetchone()
    if row is None or not auth_service.verify_password(payload.get("password") or "",
                                                       row["password_hash"]):
        # 用 1002/422 而不是 1003/401：1003 的语义是「登录失效」（HTTP_BY_CODE 里映射到 401），
        # 会把「本人口令输错」误导成「需要重新登录」，且与 user_service 的同名校验口径不一致。
        raise BizError(1002, "参数校验失败",
                       {"errors": {"password": ["密码不正确：修改服务端口或关闭自动发现前，"
                                                "需要再次确认是你本人操作"]}})
    return jsonify(ok(settings_service.update_many({"items": items}, g.user))[0]), 200


@bp.get("/api/system/info")
@require_admin
def system_info():
    """服务端「系统信息」卡：访问地址 / 绑定端口 / 防火墙与并发提示 / 磁盘与备份状态。"""
    from core import migrator, service_mode
    from core.paths import backups_dir, data_dir, db_path, logs_dir
    from core.server import Bound
    from services import timeutil

    cfg = current_app.config
    # 端口必须取**实际绑定**的那个（被占会顺延到 5001~5010），IP 取实际宣告的网卡（T-13/T-14）
    bound = Bound(host=cfg.get("BOUND_HOST", "0.0.0.0"),
                  port=int(cfg.get("BOUND_PORT") or cfg.get("HTTP_PORT", 5000)),
                  ip=cfg.get("BOUND_IP") or local_ip.pick_primary())
    info = service_mode.access_info(bound, cfg.get("DISPLAY_NAME"))
    info.update({
        "version": cfg.get("APP_VERSION", "-"),
        "device_name": cfg.get("DISPLAY_NAME", "ScanableSelf"),
        "schema_version": migrator.current_version(db_path()),
        "now": timeutil.now_iso(),
        "data_dir": data_dir(),
        "db_path": db_path(),
        "backups_dir": backups_dir(),
        "logs_dir": logs_dir(),
        "disk": _disk_usage(),
        "backup": backup_service.status(),
        "autostart": service_mode.is_autostart_enabled(),
        # 与仪表盘共用同一处推导，避免两屏显示相反的广播状态
        "broadcast_active": dashboard_service.server_status()["broadcast_active"],
    })
    return jsonify(ok(info)[0]), 200


def _disk_usage() -> dict:
    """数据盘容量（MB）。全库无现成工具函数，就地算 —— 磁盘快满必须能提前预警。"""
    import shutil
    from core.paths import data_dir

    try:
        usage = shutil.disk_usage(data_dir())
    except OSError as exc:
        log.warning("磁盘容量读取失败：%s", exc)
        return {"total_mb": 0, "free_mb": 0, "used_percent": 0}
    total = usage.total or 1
    return {"total_mb": round(usage.total / 1048576),
            "free_mb": round(usage.free / 1048576),
            "used_percent": round(usage.used * 100.0 / total, 1)}


@bp.get("/api/backup/recalc")
@require_admin
def recalc_preview():
    """库存一致性自检：返回「账面 vs 流水聚合」不一致的明细（只读，不写库）。

    ★口径与 docs/02 检查 1 完全一致：只逐行比对 `stock_current` **已有**行。
      「有流水但余额行缺失」不在本项检出范围内，需要时用 `manage.py recalc-stock` 全量对账。
    """
    from services import stock_service

    diffs = stock_service.recalc_preview()
    return jsonify(ok({"count": len(diffs), "list": diffs[:50],
                       "truncated": max(0, len(diffs) - 50)})[0]), 200


@bp.post("/api/backup/recalc")
@require_admin
def recalc_apply():
    """按流水全量重算 stock_current（写前自动备份，执行期持互斥锁，PRD 4.5 / 9.2）。"""
    from services import stock_service

    return jsonify(ok(stock_service.recalc_apply())[0]), 200
