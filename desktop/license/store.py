"""授权文件的加密读写。

文件结构：``salt(16) || nonce(12) || ciphertext || tag(16)``
密钥：``PBKDF2-HMAC-SHA256(机器码标准形式, salt, 200_000, dklen=32)``

密钥由机器码派生，等于把授权文件绑死在本机：拷到别的机器上派生结果不同，
解密必然失败（1006）——这是离线方案的换机防护。

★ 路径不由本模块决定：`config.license_file_path()` 是唯一真相源（本项目落在 data_dir）。
"""

# ★Python 3.8（Win7 服务端）会在导入期求值注解，缺这行用 PEP 585/604 会 TypeError（docs/12）
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from license.errors import ERR_LICENSE_FILE_CORRUPT, BizError

logger = logging.getLogger(__name__)

SALT_LENGTH: int = 16
NONCE_LENGTH: int = 12
TAG_LENGTH: int = 16
KEY_LENGTH: int = 32
KDF_ITERATIONS: int = 200_000


def _derive_key(machine_id: str, salt: bytes) -> bytes:
    """用机器码与盐派生 AES-256 密钥。"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(machine_id.encode("utf-8"))


def save_license_file(path: Path, data: dict[str, Any], machine_id: str) -> None:
    """把授权信息加密写入本地文件。

    AESGCM.encrypt() 返回的正是 ``ciphertext || tag``，与文件结构要求一致。
    """
    salt = os.urandom(SALT_LENGTH)
    nonce = os.urandom(NONCE_LENGTH)
    sealed = AESGCM(_derive_key(machine_id, salt)).encrypt(
        nonce,
        json.dumps(data, ensure_ascii=False).encode("utf-8"),
        None,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(salt + nonce + sealed)
    logger.info("授权文件已写入")


def load_license_file(path: Path, machine_id: str) -> dict[str, Any] | None:
    """解密读取授权文件。

    Returns:
        授权字典；文件不存在时返回 None。

    Raises:
        BizError: 文件长度异常、解密失败或内容不是 JSON 对象（1006）。
            换机、改文件、AES 标签校验不过都会落到这里。
    """
    if not path.exists():
        return None

    blob = path.read_bytes()
    if len(blob) <= SALT_LENGTH + NONCE_LENGTH + TAG_LENGTH:
        logger.warning("授权文件长度异常：%s 字节", len(blob))
        raise BizError(ERR_LICENSE_FILE_CORRUPT)

    salt = blob[:SALT_LENGTH]
    nonce = blob[SALT_LENGTH : SALT_LENGTH + NONCE_LENGTH]
    sealed = blob[SALT_LENGTH + NONCE_LENGTH :]
    try:
        plain = AESGCM(_derive_key(machine_id, salt)).decrypt(nonce, sealed, None)
        data = json.loads(plain.decode("utf-8"))
    except Exception as exc:
        logger.warning("授权文件解密失败：%s", type(exc).__name__)
        raise BizError(ERR_LICENSE_FILE_CORRUPT) from exc

    if not isinstance(data, dict):
        raise BizError(ERR_LICENSE_FILE_CORRUPT)
    return data


def clear_license_file(path: Path) -> None:
    """删除授权文件（用于「重新激活」）；文件不存在时静默返回。"""
    path.unlink(missing_ok=True)
    logger.info("授权文件已清除")
