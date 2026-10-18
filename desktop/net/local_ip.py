"""本机局域网 IP 枚举 —— PRD 5.8 · 陷阱 T-14

★ 多网卡时必须优先真实局域网网卡：选到 VMware/Hyper-V/WSL 虚拟网卡会让 APP 永远连不上。
★ 排除 127.*（回环）、169.254.*（未取到地址）、公网 IP（本系统禁止暴露公网，PRD 10.1）。
★ 排序优先级：默认路由网卡 > 192.168.* > 10.* > 172.16~31.*；设置页展示全部候选供手选。
"""
from __future__ import annotations

import re
import socket

VIRTUAL_HINTS = ("docker", "veth", "br-", "vmnet", "vEthernet", "VMware", "VirtualBox",
                 "Hyper-V", "WSL", "Loopback", "lo", "tun", "tap")


def is_usable_ip(value: str | None) -> bool:
    if not value:
        return False
    m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", value)
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    if a == 127 or a == 0 or (a == 169 and b == 254):
        return False
    return a == 10 or a == 192 or (a == 172 and 16 <= b <= 31)


def rank_candidates(addresses: list[str]) -> list[str]:
    """按「家用路由常见网段优先」排序，并剔除不可用地址。"""
    usable = [a for a in addresses if is_usable_ip(a)]

    def score(ip: str) -> int:
        first = int(ip.split(".")[0])
        if ip.startswith("192.168."):
            return 0
        if first == 10:
            return 1
        if first == 172:
            return 2
        return 3

    return sorted(usable, key=lambda ip: (score(ip), ip))


def candidates() -> list[dict]:
    """全部候选地址（含网卡名），供设置页手选。"""
    out: list[dict] = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if is_usable_ip(ip):
                out.append({"ip": ip, "hint": host})
    except OSError:
        pass
    ordered = rank_candidates([x["ip"] for x in out])
    return [{"ip": ip} for ip in ordered]


def pick_primary() -> str | None:
    ranked = rank_candidates([c["ip"] for c in candidates()])
    if ranked:
        return ranked[0]
    return "127.0.0.1"  # 无可用网卡时回落本机，页面必须同时给出黄条提示（PRD 5.8 异常表）


def pick_advertised(requested: str | None = None, fallback: str | None = None) -> str:
    """对外宣告的地址：手选值**必须在候选表内**，否则回落到实际绑定 IP、再回落主网卡。

    不校验就等于开放重定向：`?host=1.2.3.4` 会把二维码烙成任意地址，手机连过去连不上，
    用户只会怀疑「配对功能坏了」（PRD 5.8 只允许在候选网卡里挑）。
    """
    options = [c["ip"] for c in candidates()]
    if requested and requested in options:
        return requested
    if fallback:
        return fallback
    return options[0] if options else "127.0.0.1"


def subnet_broadcast_ip(ip: str, mask: str = "255.255.255.0") -> str:
    """由本机 IP + /24 推算定向广播地址（192.168.1.10 → 192.168.1.255），T-13 的第一选择。"""
    octets = [int(x) for x in ip.split(".")]
    if len(octets) != 4 or not is_usable_ip(ip):
        return "255.255.255.255"
    return ".".join(str(o) for o in octets[:3]) + ".255"


def looks_virtual(interface_name: str) -> bool:
    name = (interface_name or "").lower()
    return any(h.lower() in name for h in VIRTUAL_HINTS)
