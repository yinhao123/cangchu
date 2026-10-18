"""SECRET_KEY 等运行期配置的持久化 —— PRD 20 章 · 陷阱 T-18

★ 不能每次启动随机：网页 Session 会全部失效（用户表现为「刚登录又掉线」）。
★ 首启生成后写入 config.ini；轮换密钥只影响网页会话，设备令牌存库不受影响。
"""
from __future__ import annotations

import configparser
import os
import secrets

from core.paths import config_file, ensure_dirs

_SECTION = "runtime"


def resolve_secret_key() -> str:
    try:
        ensure_dirs()
    except RuntimeError:
        pass  # 目录不可写时仍给一个临时值，由启动自检另行报错
    parser = configparser.ConfigParser()
    if os.path.exists(config_file()):
        parser.read(config_file(), encoding="utf-8")
    key = parser.get(_SECTION, "secret_key", fallback="") if parser.has_section(_SECTION) else ""
    if key:
        return key
    key = secrets.token_hex(32)
    if not parser.has_section(_SECTION):
        parser.add_section(_SECTION)
    parser.set(_SECTION, "secret_key", key)
    try:
        with open(config_file(), "w", encoding="utf-8") as fh:
            parser.write(fh)
    except OSError:
        pass  # 写不进去也不能崩，本次会话用内存值
    return key


def read_runtime(key: str, default: str = "") -> str:
    parser = configparser.ConfigParser()
    if os.path.exists(config_file()):
        parser.read(config_file(), encoding="utf-8")
    return parser.get(_SECTION, key, fallback=default)
