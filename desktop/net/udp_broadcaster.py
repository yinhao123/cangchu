"""UDP 广播线程 —— PRD 5.8 / docs/03 第 8 章 · 陷阱 T-12 / T-13 / T-14

★ 只在 HTTP **绑定成功之后**才启动，报文里的端口必须是实际端口。
★ 发送目标顺序：子网定向广播（192.168.1.255）→ 全局广播（255.255.255.255）。
  部分路由器/网卡不回全局广播，先试定向的命中率更高（T-13）。
★ 任何发送失败都只降级状态（黄条提示改用扫码配对），**线程不得退出、不得把异常抛给主流程**。
★ 测试环境 enabled=False：一个包都不许发出去。
"""
from __future__ import annotations

import logging
import socket
import threading
import time

from net.local_ip import subnet_broadcast_ip
from net.protocol import build_payload

log = logging.getLogger("scanable.udp")
GLOBAL_TARGET = "255.255.255.255"
DEFAULT_BROADCAST_PORT = 55555

# 可被测试替换的 socket 工厂（真实代码路径不变，只替掉最外层 IO）
_socket_factory = socket.socket


class Broadcaster(threading.Thread):
    def __init__(self, ip: str, port: int, name: str | None = None, interval_ms: int = 3000,
                 broadcast_port: int = DEFAULT_BROADCAST_PORT, targets: list[str] | None = None,
                 enabled: bool = True):
        super().__init__(name="udp-broadcaster", daemon=True)
        self._ip = ip
        self._port = int(port)
        self._name = name
        self._interval = max(50, int(interval_ms)) / 1000.0
        self._broadcast_port = int(broadcast_port)
        self._targets = targets  # None = 按 IP 自动推算（定向 → 全局）
        self._enabled = bool(enabled)
        self._stop = threading.Event()
        self._payload = build_payload(ip=self._ip, port=self._port, name=self._name)
        self._status = {"enabled": self._enabled, "active": False, "error": None,
                        "last_sent_at": None, "target": None, "fallback": False, "sent": 0}
        self._lock = threading.Lock()

    # ---------- 对外 ----------

    def current_payload(self) -> bytes:
        with self._lock:
            return self._payload

    def set_endpoint(self, ip: str | None = None, port: int | None = None) -> None:
        """HTTP 换端口或本机 IP 变化后立刻刷新，并马上补发一次（旧 APP 才能尽快发现）。"""
        with self._lock:
            self._ip = ip or self._ip
            if port is not None:
                self._port = int(port)
            self._payload = build_payload(ip=self._ip, port=self._port, name=self._name)
        if self._enabled and self.is_alive():
            self.run_once()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def stop(self) -> None:
        self._stop.set()

    # ---------- 发送 ----------

    def candidate_targets(self) -> list[str]:
        if self._targets:
            return list(self._targets)
        subnet = subnet_broadcast_ip(self._ip)
        out = []
        if subnet and subnet != GLOBAL_TARGET:
            out.append(subnet)
        out.append(GLOBAL_TARGET)
        return out

    def run_once(self) -> dict:
        """发一轮。返回本轮状态；任何失败都被吞成状态字段，绝不抛出（自用系统不能因为广播崩掉）。"""
        if not self._enabled:
            with self._lock:
                self._status["active"] = False
            return self.status()
        payload = self.current_payload()
        last_error = None
        for target in self.candidate_targets():
            sock = None
            try:
                sock = _socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(payload, (target, self._broadcast_port))
                with self._lock:
                    self._status.update({"active": True, "error": None, "target": target,
                                         "last_sent_at": _now(), "sent": self._status["sent"] + 1,
                                         "fallback": target == GLOBAL_TARGET and len(self.candidate_targets()) > 1})
                return self.status()
            except OSError as exc:
                last_error = f"{target}: {exc}"
                continue  # 试下一个目标（定向被拒 → 全局）
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
        with self._lock:
            self._status.update({"active": False, "error": last_error or "无可用广播目标",
                                 "last_attempt_at": _now()})
        log.warning("UDP 广播发送失败（HTTP 不受影响，可改用扫码配对）：%s", last_error,
                    extra={"action": "udp.broadcast", "status": "failure"})
        return self.status()

    def run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self._interval)


def _now() -> str:
    from services import timeutil
    return timeutil.now_iso()


def start_broadcaster(app, bound) -> Broadcaster | None:
    """按配置决定是否启动；广播起不来也不能影响 HTTP（PRD 2.4 步骤 3 的失败语义）。"""
    cfg = app.config
    if not cfg.get("BROADCAST_ENABLED", True):
        return None
    interval = int(cfg.get("BROADCAST_INTERVAL_MS", 3000))
    targets = None
    override = cfg.get("UDP_TARGET")
    if override and override not in ("", "auto"):
        targets = [override, GLOBAL_TARGET]
    broadcaster = Broadcaster(ip=bound.ip, port=bound.port, name=cfg.get("DISPLAY_NAME"),
                              interval_ms=interval,
                              broadcast_port=int(cfg.get("BROADCAST_PORT", DEFAULT_BROADCAST_PORT)),
                              targets=targets)
    broadcaster.start()
    cfg["BROADCAST_ACTIVE"] = True
    app.config["BROADCASTER"] = broadcaster
    return broadcaster
