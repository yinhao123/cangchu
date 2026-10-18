"""服务模式（无窗口服务端）支撑 —— PRD 14.2 / docs/12

交付形态是「Windows 7 上开机即服务、不弹任何窗口」，于是没有界面可以告诉管理员
「局域网里的机器该访问哪个地址」。本模块把这件事实现在一处：

1. `access_info()` —— 用**实际绑定端口**与 `bound.ip`（实际对外宣告的网卡）算访问地址。
   不能用配置里的 `HTTP_PORT`：端口被占后会自动顺延到 5001~5010，
   写错等于告诉用户一个没人监听的端口（T-13 / T-14 的同类错误）。
2. `announce()` —— 地址汇总 + 访问二维码（二维码内容与手机配对二维码同源，
   见 `net/qrcode_gen.build_payload`：只含连接信息，不含任何凭据）。
3. `publish()` —— 把二维码 PNG 与一份纯文本说明落到数据目录。
   无窗口的 exe 双击后「什么都不显示」，管理员需要一个能翻到的东西；
   写盘失败只记日志，绝不能让服务起不来。
4. 开机自启：不引入任何依赖，用「启动文件夹 + .cmd」实现。
   评估过 `schtasks`：它需要管理员权限、改端口/路径都要重新注册，对自用场景过重。

★本模块任何失败都必须降级为「日志 + 返回空值」，禁止抛异常 —— 服务模式没有 GUI 兜底。
"""
from __future__ import annotations

import base64
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("scanable.service")

#: 并发建议（PRD 9.2）：SQLite 是单写者，waitress 的 8 线程只是排队能力
CONCURRENCY_HINT = (
    "SQLite 为单写者：建议同时写入的客户端不超过 3~5 个；"
    "超出会先在 busy_timeout(5s) 排队，再超时报 database is locked"
)
#: 需要放行的端口（docs/12 §3.4）
FIREWALL_PORTS = "TCP 5000-5010（HTTP，端口被占自动顺延）+ UDP 55555（手机发现）"
AUTOSTART_FILE_NAME = "ScanableSelf-server.cmd"
ACCESS_QR_FILE_NAME = "access-qr.png"
ACCESS_NOTE_FILE_NAME = "access.txt"


def firewall_commands() -> list[str]:
    """给管理员的放行命令（管理员权限的 cmd/PowerShell 里粘贴执行）。"""
    return [
        'netsh advfirewall firewall add rule name="ScanableSelf HTTP" '
        "dir=in action=allow protocol=TCP localport=5000-5010",
        'netsh advfirewall firewall add rule name="ScanableSelf UDP" '
        "dir=in action=allow protocol=UDP localport=55555",
    ]


def access_info(bound, display_name: str | None = None) -> dict:
    """局域网访问信息汇总（供启动信息与状态接口复用）。

    ip 用 `bound.ip`（`core/server.py` 已按 T-14 挑好网卡），端口用**实际绑定成功**的那一个。
    """
    from net import local_ip

    port = int(getattr(bound, "port", 0) or 0)
    primary_ip = getattr(bound, "ip", None) or local_ip.pick_primary()
    urls = [f"http://{c['ip']}:{port}/" for c in local_ip.candidates()]
    primary = f"http://{primary_ip}:{port}/"
    if primary not in urls:
        urls.insert(0, primary)
    return {
        "url": primary,
        "urls": urls,
        "ip": primary_ip,
        "port": port,
        "name": display_name or "ScanableSelf",
        "candidates": local_ip.candidates(),
        "firewall_ports": FIREWALL_PORTS,
        "firewall_commands": firewall_commands(),
        "concurrency_hint": CONCURRENCY_HINT,
    }


def announce(bound, display_name: str | None = None) -> dict:
    """访问信息 + 访问二维码（base64 PNG）。

    二维码生成依赖 qrcode/Pillow 与所选字体，失败时只留空串并记日志：
    手机端仍可手工输入地址，不能让「画不出二维码」拖垮整个服务。
    """
    info = access_info(bound, display_name)
    from net import qrcode_gen

    payload = qrcode_gen.build_payload(info["ip"], info["port"], info["name"])
    info["qr_payload"] = payload
    try:
        info["qr_png_base64"] = qrcode_gen.to_png_base64(payload)
    except Exception as exc:
        log.warning("访问二维码生成失败（%s），请改用地址手工输入", exc)
        info["qr_png_base64"] = ""
    return info


def write_access_qr(info: dict) -> Path | None:
    """把访问二维码 PNG 落到数据目录；失败返回 None。"""
    png = info.get("qr_png_base64") or ""
    if not png:
        return None
    try:
        from core.paths import data_dir

        target = Path(data_dir()) / ACCESS_QR_FILE_NAME
        target.write_bytes(base64.b64decode(png))
        return target
    except Exception as exc:
        log.warning("访问二维码写盘失败：%s", exc)
        return None


def write_access_note(info: dict) -> Path | None:
    """把「该访问哪个地址」写成纯文本，供无窗口 exe 的使用者翻看；失败返回 None。"""
    lines = [
        "ScanableSelf 服务端 —— 局域网访问信息",
        "",
        f"主地址：{info['url']}",
        f"绑定端口：{info['port']}",
    ]
    alternates = [u for u in info.get("urls", []) if u != info["url"]]
    if alternates:
        lines.append("备选地址（多网卡机器，手机连不上时逐个试）：")
        lines.extend(f"  {u}" for u in alternates)
    lines += [
        "",
        f"访问二维码（手机扫码直达）：{ACCESS_QR_FILE_NAME}",
        "",
        f"需放行的端口：{info['firewall_ports']}",
        "放行命令：",
    ]
    lines += [f"  {c}" for c in info["firewall_commands"]]
    lines += ["", f"并发建议：{info['concurrency_hint']}", ""]
    try:
        from core.paths import data_dir

        target = Path(data_dir()) / ACCESS_NOTE_FILE_NAME
        target.write_text("\r\n".join(lines), encoding="utf-8-sig")
        return target
    except Exception as exc:
        log.warning("访问信息写盘失败：%s", exc)
        return None


def publish(bound, display_name: str | None = None) -> dict:
    """无窗口形态下的「启动展示」唯一出口：算地址 → 写二维码与说明 → 全部记日志。

    返回 `announce()` 的字典（额外带 `qr_path` / `note_path`），供启动流程与状态接口复用。
    """
    info = announce(bound, display_name)
    qr_path = write_access_qr(info)
    note_path = write_access_note(info)
    info["qr_path"] = str(qr_path) if qr_path else ""
    info["note_path"] = str(note_path) if note_path else ""

    if info["ip"] == "127.0.0.1":
        log.warning(
            "未发现可用的局域网网卡，仅本机可访问；手机端请改用扫码配对（HTTP 不受影响）",
            extra={"action": "service_mode.announce", "status": "loopback_only"},
        )

    log.info("服务已就绪｜局域网访问 %s（实际绑定端口 %s）", info["url"], info["port"])
    for url in info["urls"][1:]:
        log.info("备选访问地址 %s", url)
    if qr_path:
        log.info("访问二维码已生成：%s", qr_path)
    if note_path:
        log.info("访问信息说明：%s", note_path)
    log.info("需放行的端口：%s", FIREWALL_PORTS)
    log.info("%s", CONCURRENCY_HINT)
    if info["ip"] != "127.0.0.1" and not is_autostart_enabled():
        log.info("如需开机自启：python manage.py autostart on（或 exe 的「设置」页开关）")
    return info


# ---------------------------------------------------------------- 开机自启

def autostart_dir() -> Path | None:
    """当前用户的「启动」文件夹；非 Windows 或取不到 APPDATA 时返回 None。"""
    appdata = os.environ.get("APPDATA")
    if not appdata or not sys.platform.startswith("win"):
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_file() -> Path | None:
    base = autostart_dir()
    return None if base is None else base / AUTOSTART_FILE_NAME


def is_autostart_enabled() -> bool:
    path = autostart_file()
    return bool(path and path.exists())


def _autostart_target() -> str:
    """开机要拉起的命令：冻结态即 exe 自身。"""
    exe = sys.executable
    if not getattr(sys, "frozen", False):
        raise RuntimeError("开发态不注册开机自启（否则每次登录都会拉起调试进程），请在打包后的 exe 上操作")
    return f'"{exe}"'


def enable_autostart(target: str | None = None) -> Path:
    """写入「启动」文件夹里的 .cmd，开机自动拉起服务；返回写入的路径。

    Raises:
        RuntimeError: 非 Windows / 非冻结态且未显式给出 target。
    """
    path = autostart_file()
    if path is None:
        raise RuntimeError("当前环境没有「启动」文件夹，无法注册开机自启")
    command = target or _autostart_target()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\r\n".join([
        "@echo off",
        "rem ScanableSelf 服务端自启（由「设置 → 开机自启」或 manage.py autostart on 生成）",
        "rem 删除本文件即取消自启；端口/路径变更后需重新生成",
        f"start \"\" {command}",
        "",
    ])
    path.write_text(body, encoding="utf-8-sig")
    log.info("已注册开机自启：%s", path)
    return path


def disable_autostart() -> bool:
    """取消开机自启；返回是否真的删除了文件（原本没有则返回 False）。"""
    path = autostart_file()
    if path is None or not path.exists():
        return False
    path.unlink()
    log.info("已取消开机自启：%s", path)
    return True


__all__ = [
    "ACCESS_NOTE_FILE_NAME",
    "ACCESS_QR_FILE_NAME",
    "AUTOSTART_FILE_NAME",
    "CONCURRENCY_HINT",
    "FIREWALL_PORTS",
    "access_info",
    "announce",
    "autostart_dir",
    "autostart_file",
    "disable_autostart",
    "enable_autostart",
    "firewall_commands",
    "is_autostart_enabled",
    "publish",
    "write_access_note",
    "write_access_qr",
]
