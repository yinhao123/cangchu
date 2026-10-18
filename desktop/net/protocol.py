"""UDP 报文协议 —— docs/03 第 8 章 · 陷阱 T-12 / T-13

线格式（UTF-8，空格分隔的 KEY:VALUE）：
    INVENTORY_SERVER v1.0 IP:192.168.1.10 PORT:5000 NAME:仓库电脑

★ `NAME` 必须放最后：它的值可以含空格，解析规则是「取 NAME: 之后到行尾」。
  新增字段一律插在 NAME 之前，否则会破坏旧 APP 的解析（协议是双端契约，改它=安卓端返工）。
★ 未知 KEY 必须忽略（向前兼容）；缺 IP/PORT 或格式非法则整包丢弃，不做部分解析。
"""
from __future__ import annotations

MAGIC = "INVENTORY_SERVER"
VERSION = "v1.0"
MAX_BYTES = 512
_NAME_FIELD = "NAME"


def build_payload(ip: str, port: int, name: str | None = None) -> bytes:
    parts = [MAGIC, VERSION, f"IP:{ip}", f"PORT:{int(port)}"]
    if name:
        clean = " ".join(str(name).split())[:60]  # 压掉换行/连续空格，避免破坏 KEY:VALUE 结构
        parts.append(f"{_NAME_FIELD}:{clean}")
    payload = " ".join(parts).encode("utf-8")
    if len(payload) > MAX_BYTES:
        payload = payload[:MAX_BYTES]
    return payload


def parse_payload(text: str) -> dict | None:
    """解析广播包；任何不合规返回 None（调用方直接丢弃，不得抛异常打断监听循环）。"""
    if not text or not text.strip():
        return None
    pieces = text.strip().split(" ", 2)  # 最多切两段：MAGIC、VERSION、剩余全部（NAME 可能含空格）
    if len(pieces) < 2 or pieces[0] != MAGIC or pieces[1] != VERSION:
        return None
    fields: dict[str, str] = {}
    rest = pieces[2] if len(pieces) > 2 else ""
    # 先取 NAME:（其后全部为值），再解析其余 KEY:VALUE
    marker = f"{_NAME_FIELD}:"
    name_value = ""
    idx = rest.find(marker)
    if idx != -1:
        head = rest[:idx]
        name_value = rest[idx + len(marker):].strip()
        for token in head.split(" "):
            if ":" in token:
                k, _, v = token.partition(":")
                fields[k.upper()] = v
        if name_value:
            fields[_NAME_FIELD] = name_value
    else:
        for token in rest.split(" "):
            if ":" in token:
                k, _, v = token.partition(":")
                fields[k.upper()] = v

    ip = fields.get("IP", "")
    port_raw = fields.get("PORT", "")
    if not _is_private_ipv4(ip):
        return None
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        return None
    if not (1 <= port <= 65535):
        return None
    out = {"ip": ip, "port": port}
    if name_value:
        out["name"] = name_value
    return out


def _is_private_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    if any(not 0 <= o <= 255 for o in octets):
        return False
    return octets[0] == 10 or octets[0] == 192 or (octets[0] == 172 and 16 <= octets[1] <= 31)
