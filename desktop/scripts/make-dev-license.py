"""开发期注册码生成器 —— 仅在 `license/config.py:VERIFY_SIGNATURE = False`（联调模式）下可用。

背景（docs/11 §1.1 / docs/12 §4.4）：卖家后台的正式出码渠道尚未接通，因此 P7 演示阶段
用「不验签」模式先跑通激活流程。此时注册码内容由本脚本按协议拼装，**没有任何签名保护**，
任何人都能伪造 —— 只能用于本机/客户演示，绝不可作为正式授权。

★ `VERIFY_SIGNATURE = True` 时本脚本会直接拒绝执行：正式发版后必须走卖家后台出码，
  私钥只应存在于后台，不进客户端、不进源码树（docs/11 §4.5）。

用法（在 desktop 目录）：
    python scripts/make-dev-license.py                  # 用本机机器码
    python scripts/make-dev-license.py <机器码>          # 给指定机器出码（客户把机器码发给你）

协议：`LG1.<base64url(JSON 载荷)>.<base64url(签名)>`；载荷 {"v":1,"c":<软件编码>,"m":<机器码>,"x":<到期时间戳，0=永久>}
"""
from __future__ import annotations

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from license import config  # noqa: E402
from license import machine_id as machine_id_module  # noqa: E402


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def build_code(target_machine_id: str, expire_ts: int = 0) -> str:
    payload = {
        "v": 1,
        "c": config.app_code(),
        "m": machine_id_module.normalize_machine_id(target_machine_id),
        "x": int(expire_ts),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    # 联调模式下签名段不会被校验，但仍按协议放在第三段，保证格式与正式码一致
    return "LG1.%s.%s" % (body, _b64(b"dev-mode-no-signature"))


def main(argv: list[str]) -> int:
    if config.VERIFY_SIGNATURE:
        print("拒绝执行：VERIFY_SIGNATURE=True（已启用真实验签），请到卖家后台出码。")
        return 2
    target = argv[1] if len(argv) > 1 else machine_id_module.get_machine_id()
    print("机器码：%s" % machine_id_module.format_machine_id(target))
    print("注册码：%s" % build_code(target))
    print("")
    print("注意：当前是联调模式（不验签），此码仅供本机/演示使用；正式发版前必须打开 VERIFY_SIGNATURE。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
