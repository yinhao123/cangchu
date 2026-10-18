"""配对二维码 —— PRD 5.8 / docs/03 2.2

内容是**紧凑 JSON**，只含连接信息（protocol/version/host/port/name），**不含任何凭据**：
二维码会被拍照、截图、贴在仓库墙上，带 token 等于把凭据公开。
"""
from __future__ import annotations

import base64
import io
import json


def build_payload(host: str, port: int, name: str | None = None) -> str:
    return json.dumps({"protocol": "inventory_server", "version": "1.0", "host": host,
                       "port": int(port), "name": name or "ScanableSelf"},
                      ensure_ascii=False, separators=(",", ":"))


def to_png_base64(payload: str) -> str:
    import qrcode
    img = qrcode.make(payload)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
