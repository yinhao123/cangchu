"""注册码验签模块。**只使用公钥，绝不接触私钥**。

校验顺序不可颠倒：先验签，再比对内容——顺序反过来等于把未经验签的数据
当可信数据使用。

例外：`verify_signature=False`（`license.config.VERIFY_SIGNATURE`）时整段跳过验签，
仅用于后台出码渠道未接通期间的开发联调；此时注册码内容可被任意伪造。
"""

# ★Python 3.8（Win7 服务端）会在导入期求值注解，缺这行用 PEP 585/604 会 TypeError（docs/12）
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from license.errors import (
    ERR_APP_MISMATCH,
    ERR_LICENSE_EXPIRED,
    ERR_LICENSE_FORMAT,
    ERR_LICENSE_VERSION,
    ERR_MACHINE_MISMATCH,
    ERR_SIGNATURE_INVALID,
)
from license.machine_id import normalize_machine_id

logger = logging.getLogger(__name__)

PROTOCOL_VERSION: int = 1
SUPPORTED_PREFIXES: frozenset[str] = frozenset({"LG1"})
SEGMENT_COUNT: int = 3

#: 跳验签只告警一次（每请求都会走这里，不节流会刷满日志）
_warned_no_signature: bool = False


@dataclass
class VerifyResult:
    """验签结果。"""

    ok: bool
    code: int = 0
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


def _b64url_decode(text: str) -> bytes:
    """解码 base64url，自动补齐 ``=`` 填充。"""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_license(
    code_text: str,
    public_key_pem: bytes,
    expected_app_code: str,
    local_machine_id: str,
    now_ts: int | None = None,
    verify_signature: bool = True,
) -> VerifyResult:
    """离线验签注册码。

    校验顺序（不可颠倒）：格式/版本 → 验签 → 软件编码 → 机器码 → 有效期。

    Args:
        code_text: 用户粘贴的注册码原文（可含空白与换行）。
        public_key_pem: 内置公钥的 PEM 字节；``verify_signature=False`` 时不读取。
        expected_app_code: 本软件自己的软件编码。
        local_machine_id: 本机机器码（任意形式，内部归一化）。
        now_ts: 当前 UTC 时间戳，便于测试注入；默认取系统时间。
        verify_signature: False 时跳过第 2 步（开发联调用，见模块 docstring）。

    Returns:
        VerifyResult；``ok=True`` 时 ``payload`` 为解析出的载荷。
    """
    global _warned_no_signature
    now_ts = int(time.time()) if now_ts is None else now_ts

    # 1) 清洗与格式检查：一次性去掉所有空白（含换行、制表符、全角空格）
    cleaned = "".join(code_text.split())
    parts = cleaned.split(".")
    if len(parts) != SEGMENT_COUNT or parts[0] not in SUPPORTED_PREFIXES:
        if len(parts) == SEGMENT_COUNT and parts[0].startswith("LG"):
            return VerifyResult(False, ERR_LICENSE_VERSION, "注册码版本不支持")
        return VerifyResult(False, ERR_LICENSE_FORMAT, "注册码格式错误")
    _, payload_b64, signature_b64 = parts

    # 2) 验签（早于任何内容判断）
    if verify_signature:
        try:
            public_key = serialization.load_pem_public_key(public_key_pem)
            public_key.verify(
                _b64url_decode(signature_b64),
                payload_b64.encode("ascii"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature:
            return VerifyResult(False, ERR_SIGNATURE_INVALID, "签名验证失败")
        except Exception:
            # base64 非法、公钥不可加载、payload 段含非 ASCII 字符等一律归为格式错误
            return VerifyResult(False, ERR_LICENSE_FORMAT, "注册码格式错误")
    elif not _warned_no_signature:
        _warned_no_signature = True
        logger.warning("VERIFY_SIGNATURE=False：注册码验签已跳过，仅限本机联调，正式发版前必须打开")

    # 3) 解析 payload（此时已可信）
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return VerifyResult(False, ERR_LICENSE_FORMAT, "注册码内容损坏")
    if not isinstance(payload, dict):
        return VerifyResult(False, ERR_LICENSE_FORMAT, "注册码内容损坏")

    if int(payload.get("v", 0)) != PROTOCOL_VERSION:
        return VerifyResult(False, ERR_LICENSE_VERSION, "注册码版本不支持")

    # 4) 软件编码
    if payload.get("c") != expected_app_code:
        return VerifyResult(False, ERR_APP_MISMATCH, "该注册码不适用于当前软件")

    # 5) 机器码
    if normalize_machine_id(str(payload.get("m", ""))) != normalize_machine_id(local_machine_id):
        return VerifyResult(False, ERR_MACHINE_MISMATCH, "该注册码不适用于本机")

    # 6) 有效期（x = 0 表示永久）
    expire_ts = int(payload.get("x", 0))
    if expire_ts and now_ts > expire_ts:
        return VerifyResult(False, ERR_LICENSE_EXPIRED, "注册码已过期")

    return VerifyResult(True, 0, "激活成功", payload)
