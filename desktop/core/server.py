"""waitress 启动与优雅关闭 —— PRD 2.4 / 5.8 / 14.2 · 陷阱 T-15

★ 端口占用必须自动换（5000 常被其它软件占），且回报**实际端口**：
  广播报文与 /api/ping 都用这个值，否则 APP 连到没人监听的端口（PRD 2.4 关键约束）。
★ 优雅关闭要 checkpoint WAL，避免 -wal 残留导致下次启动读到脏状态（PRD 2.4）。
★ 纯 Python、Windows 友好、无 C 扩展 —— PyInstaller 打包友好（T-15 需 hidden-imports）。
"""
from __future__ import annotations

import logging
import socket
from dataclasses import dataclass

from waitress import create_server

log = logging.getLogger("scanable.server")

DEFAULT_PORT_MAX = 5010
THREADS = 8  # 8 线程 × 每请求一连接，在 SQLite 写串行下是安全上限（PRD 9.6）


@dataclass
class Bound:
    host: str
    port: int
    ip: str
    attempted: tuple[int, ...] = ()


def is_port_free(host: str, port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def pick_port(host: str, port: int, port_max: int) -> tuple[int, tuple[int, ...]]:
    """在 [port, port_max] 内找第一个可用端口；全被占则抛 OSError（调用方弹窗报错，禁止静默）。"""
    tried = []
    for candidate in range(port, port_max + 1):
        tried.append(candidate)
        if is_port_free(host, candidate):
            return candidate, tuple(tried)
    raise OSError(f"端口 {port}~{port_max} 全部被占用，请关闭占用程序或改配置端口（已尝试：{tried}）")


def serve(app, host: str = "0.0.0.0", port: int | None = None, port_max: int | None = None,
          threads: int = THREADS):
    """返回 (server, Bound)。server 需由调用方 .run() 阻塞（waitress 无 run_forever）。"""
    from core.paths import data_dir  # 只在需要时引用，避免导入环
    # port=0 是「让 OS 分配」的合法语义，不能被当成「没传」而退回配置值
    if port is not None and int(port) == 0:
        return _serve_ephemeral(app, host, threads)
    start = int(port) if port is not None else int(app.config.get("HTTP_PORT", 5000))
    ceiling = int(port_max if port_max else app.config.get("HTTP_PORT_MAX", DEFAULT_PORT_MAX))
    if bool(app.config.get("AUTO_SWITCH_PORT", True)) is False:
        chosen, attempted = start, (start,)
    else:
        chosen, attempted = pick_port(host, start, ceiling)
    server = create_server(app, host=host, port=chosen, threads=threads)
    app.config["BOUND_PORT"] = chosen
    app.config["BOUND_HOST"] = host
    if chosen != start:
        log.warning("端口 %s 被占用，已自动改用 %s", start, chosen,
                    extra={"action": "server.bind", "status": "fallback"})
    bound = Bound(host=host, port=chosen, ip=_announce_ip(app, host), attempted=attempted)
    # ★必须写回 config：配对二维码（api/system.py）读 BOUND_IP，不写就会渲染成 127.0.0.1，
    #   手机扫到的地址根本连不上（实测踩到）。
    app.config["BOUND_IP"] = bound.ip
    return server, bound


def _serve_ephemeral(app, host: str, threads: int):
    """port=0：交给 OS 分配端口。

    ★ 必须自己 bind 再把 socket 交给 waitress：waitress 到 run() 才绑定，
      届时读 _server_ports 还是空的，回报的端口会是 0 —— APP 就会连不上（实测踩到）。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    real = int(sock.getsockname()[1])
    server = create_server(app, threads=threads, sockets=[sock])  # sockets 与 host/port 互斥
    app.config["BOUND_PORT"] = real
    bound = Bound(host=host, port=real, ip=_announce_ip(app, host))
    app.config["BOUND_IP"] = bound.ip
    return server, bound


def _announce_ip(app, host: str) -> str:
    """对外宣告的 IP：绑定具体网卡时用该网卡，0.0.0.0 时取枚举优先级结果（T-14）。"""
    if host not in ("0.0.0.0", "::"):
        return host
    from net.local_ip import pick_primary
    return pick_primary() or "127.0.0.1"


def shutdown(server, broadcaster, app=None) -> None:
    """停广播 → 关 HTTP → checkpoint WAL。任一步失败都不能阻断后面的清理。"""
    if broadcaster is not None:
        try:
            broadcaster.stop()
        except Exception as exc:
            log.warning("停止广播线程失败：%s", exc)
    try:
        server.close()
    except Exception as exc:
        log.warning("关闭 HTTP 服务失败：%s", exc)
    if app is not None:
        try:
            from models.database import connect
            conn = connect(app.config["DB_PATH"])
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception as exc:
            log.warning("WAL checkpoint 失败：%s", exc)
