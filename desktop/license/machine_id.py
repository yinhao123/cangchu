"""机器码采集、计算、归一化与格式化。

采集顺序固定为「主板 UUID → CPU ID → 系统盘序列号 → 主网卡 MAC」，
任一来源取不到就填空串——顺序一变，同一台机器的机器码就变了，
已发出的注册码会集体失效。

前三项优先走 wmic，整组不可用时改用 PowerShell/CIM（读同一组 WMI 属性）：
Windows 11 24H2 起系统已不带 wmic，只留一条通道会让机器码退化成「仅 MAC」。
"""

# ★Python 3.8（Win7 服务端）会在导入期求值注解，缺这行用 PEP 585/604 会 TypeError（docs/12）
from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import sys
import uuid
from typing import Final

from license.errors import ERR_UNKNOWN, BizError

logger = logging.getLogger(__name__)

MACHINE_ID_LENGTH: Final[int] = 32
GROUP_SIZE: Final[int] = 4
WMIC_TIMEOUT_SECONDS: Final[int] = 5

_STRIP_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^0-9A-Fa-f]")
_STANDARD_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9A-F]{32}$")

#: 顺序即拼接顺序（顺序一变机器码就变）
_WMIC_QUERIES: Final[tuple[str, ...]] = (
    "csproduct get uuid",
    "cpu get ProcessorId",
    "diskdrive get SerialNumber",
)

#: wmic 缺失时的整组兜底：一次 PowerShell 调用取回同样三个 WMI 属性。
#: 逐项调用要起 3 次 PowerShell 进程（实测 4.4 秒），会顶到激活超时。
_CIM_BATCH_SCRIPT: Final[str] = (
    "$ErrorActionPreference='SilentlyContinue'; "
    "$c=Get-CimInstance Win32_ComputerSystemProduct; "
    "$p=Get-CimInstance Win32_Processor | Select-Object -First 1; "
    "$d=Get-CimInstance Win32_DiskDrive | Select-Object -First 1; "
    'Write-Output "$($c.UUID)|$($p.ProcessorId)|$($d.SerialNumber)"'
)

_cached_machine_id: str | None = None


def _run_wmic(query: str) -> str:
    """wmic 通道，失败或超时返回空串。

    CREATE_NO_WINDOW 不能省：打包成窗口程序后不加会闪一下黑色控制台。
    """
    if not sys.platform.startswith("win"):
        return ""
    try:
        output = subprocess.check_output(
            ["wmic", *query.split()],
            timeout=WMIC_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("wmic 查询失败（%s）：%s", query, exc)
        return ""

    lines = [
        line.strip()
        for line in output.decode("utf-8", errors="ignore").splitlines()
        if line.strip()
    ]
    # 第一行是列标题，真正的结果从第二行开始
    return lines[1] if len(lines) > 1 else ""


def _run_powershell(script: str) -> str:
    """PowerShell / CIM 通道（Windows 11 24H2 起 wmic 已被移除）。"""
    if not sys.platform.startswith("win"):
        return ""
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=WMIC_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("PowerShell 查询失败：%s", exc)
        return ""

    lines = [
        line.strip()
        for line in output.decode("utf-8", errors="ignore").splitlines()
        if line.strip()
    ]
    return lines[0] if lines else ""


def _collect_via_cim() -> tuple[str, str, str]:
    """一次 PowerShell 调用取回三项硬件信息。"""
    output = _run_powershell(_CIM_BATCH_SCRIPT)
    if not output:
        return ("", "", "")
    parts = output.split("|")
    if len(parts) != len(_WMIC_QUERIES):
        logger.warning("CIM 批量输出格式异常，按采集失败处理")
        return ("", "", "")
    return (parts[0].strip(), parts[1].strip(), parts[2].strip())


def _query_sources() -> list[str]:
    """取回前三项硬件信息：优先整组 wmic，不可用则整组走 CIM。

    按「整组切换」而不是逐项降级——混用两条通道可能出现细微格式差异，
    那会让机器码在系统升级前后对不上。
    """
    values = [_run_wmic(query) for query in _WMIC_QUERIES]
    if all(values):
        return values
    logger.debug("wmic 通道不可用，改用 PowerShell/CIM")
    return list(_collect_via_cim())


def _primary_mac() -> str:
    """主网卡 MAC（uuid.getnode() 兜底，基本不会失败）。"""
    return f"{uuid.getnode():012X}"


def collect_raw_fingerprint() -> str:
    """采集硬件指纹原始串（``|`` 分隔，缺失位为空串）。

    Raises:
        BizError: 四项全部采集失败（1000）——真机上不会发生，但必须有兜底。
    """
    parts = [*_query_sources(), _primary_mac()]
    if not any(parts):
        raise BizError(ERR_UNKNOWN, "无法采集本机硬件信息")
    return "|".join(parts)


def compute_machine_id(raw: str) -> str:
    """原始串 → 标准形式机器码（SHA-256 前 32 位大写 hex）。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:MACHINE_ID_LENGTH].upper()


def normalize_machine_id(raw: str) -> str:
    """任意形式 → 标准形式（去分隔符 / 空白 / 大小写）。"""
    return _STRIP_PATTERN.sub("", raw or "").upper()


def format_machine_id(machine_id: str) -> str:
    """标准形式 → 显示形式（每 4 位加 ``-``）。"""
    if len(machine_id) != MACHINE_ID_LENGTH:
        return machine_id
    return "-".join(
        machine_id[index : index + GROUP_SIZE]
        for index in range(0, len(machine_id), GROUP_SIZE)
    )


def is_standard_machine_id(machine_id: str) -> bool:
    """判断是否为标准形式（32 位大写 hex）。"""
    return bool(_STANDARD_PATTERN.fullmatch(machine_id))


def get_machine_id() -> str:
    """获取本机机器码（标准形式），带进程内缓存。

    采集要调三次 wmic（数百毫秒），一次激活流程会多次用到，故做缓存。

    Raises:
        BizError: 硬件信息采集全部失败（1000）。
    """
    global _cached_machine_id
    if _cached_machine_id is None:
        _cached_machine_id = compute_machine_id(collect_raw_fingerprint())
    return _cached_machine_id


def reset_machine_id_cache() -> None:
    """清空机器码缓存（换机测试用）。"""
    global _cached_machine_id
    _cached_machine_id = None
