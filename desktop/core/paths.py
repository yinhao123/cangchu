"""路径解析 —— PRD 14.2 · 陷阱 T-16 / T-17

★ 冻结（PyInstaller）后只读资源被解包到 sys._MEIPASS，用 __file__ 相对路径会 TemplateNotFound。
★ 可写数据禁止放 exe 同目录：Program Files / 桌面受 UAC 保护，写不进去会让用户以为「双击没反应」。
"""
from __future__ import annotations

import os
import sys

APP_DIR_NAME = "ScanableSelf"


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(relative: str) -> str:
    """只读资源（templates / static / db / 字体）绝对路径，兼容冻结模式。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, relative)
    return os.path.join(_project_root(), relative)


def data_dir() -> str:
    """可写数据根目录：Windows 用 %LOCALAPPDATA%\\ScanableSelf，其他平台回退 ~/.scanableself。

    环境变量 SCANABLE_DATA_DIR 可覆盖（开发/测试/便携模式）。
    """
    override = os.environ.get("SCANABLE_DATA_DIR")
    if override:
        return override
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(local, APP_DIR_NAME)
    return os.path.join(os.path.expanduser("~"), ".scanableself")


def spa_dir() -> str:
    """SPA 构建产物（`static/spa/`）绝对路径，兼容冻结模式。

    ★构建产物改了前端就必须 `npm run build` 后一起提交：部署机与打包机都不装 Node（docs/12）。
    ★产物没随包 = 打开就是白屏，因此这里返回的路径必须由 `views/spa.py` 做存在性自检。
    """
    return resource_path(os.path.join("static", "spa"))


def db_path() -> str:
    return os.path.join(data_dir(), "inventory.db")


def backups_dir() -> str:
    return os.path.join(data_dir(), "backups")


def logs_dir() -> str:
    return os.path.join(data_dir(), "logs")


def config_file() -> str:
    return os.path.join(data_dir(), "config.ini")


def ensure_dirs() -> None:
    """建目录并做「可写自检」：写一个 .probe 文件。失败抛异常，由入口层弹窗（T-17）。"""
    for d in (data_dir(), backups_dir(), logs_dir()):
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".probe")
        try:
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
        except OSError as exc:
            raise RuntimeError(
                f"数据目录不可写：{d}（不要把程序放在 Program Files 或网络盘）。原因：{exc}") from exc
