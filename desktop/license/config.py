"""激活模块常量与资源路径：换产品时只改这一处（指南 §4.2 / §4.3）。

与指南的两处偏差（本项目约定所致，已在交付说明中登记）：
1. 授权文件跟 `core.paths.data_dir()`（`%LOCALAPPDATA%\\ScanableSelf`），
   而不是指南的 `%APPDATA%\\<产品名>` —— 本项目可写数据统一一个目录，
   且测试能用 `SCANABLE_DATA_DIR` 隔离。
2. 公钥资源保持指南布局 `<项目>/license/resources/keys/public.pem`，
   故 `resource_path()` 的开发态基准是 `license/resources/`，与 `core.paths.resource_path()`
   （基准为 `desktop/`）**语义不同**，不要互相替换。
"""

import sys
from pathlib import Path

#: 产品名：窗口标题与日志用；授权文件目录名以 core.paths.data_dir() 为准
APP_NAME: str = "ScanableSelf"
#: 软件编码：必须与后台「软件管理」里的编码逐字符一致（大小写敏感），否则验签报 1005
APP_CODE: str = "CHURUKU"
#: ★开发期开关：True=正常验签；False=跳过 RSA 验签（只校验格式/编码/机器码/有效期）。
#: 关掉是因为后台出码渠道尚未接通、内置公钥也还没放；此时注册码的**内容完全不可信**，
#: 只能用于本机联调。正式发版前必须改回 True 并放入 keys/public.pem（T0 自检会拦）。
VERIFY_SIGNATURE: bool = False
#: 授权文件内留档的产品版本号（与 app.__version__ 保持一致，便于日后排查）
APP_VERSION: str = "1.0.0"
#: 内置公钥的相对路径（打包后位于 keys/public.pem）
PUBLIC_KEY_RESOURCE: str = "keys/public.pem"
#: 授权文件名
LICENSE_FILE_NAME: str = "license.dat"


def resource_path(relative: str) -> Path:
    """兼容开发态与 PyInstaller 打包态的资源路径解析。"""
    packed_base = getattr(sys, "_MEIPASS", None)
    base = Path(packed_base) if packed_base else Path(__file__).resolve().parent / "resources"
    return base / relative


def public_key_path() -> Path:
    """内置公钥路径。"""
    return resource_path(PUBLIC_KEY_RESOURCE)


def public_key_pem() -> bytes:
    """读取内置公钥 PEM。

    Raises:
        RuntimeError: 公钥文件缺失（打包漏带或安装被破坏）。
    """
    path = public_key_path()
    if not path.exists():
        raise RuntimeError(f"内置公钥缺失：{path}；请重新安装客户端")
    return path.read_bytes()


def app_code() -> str:
    """本软件的编码；未配置时立刻报错而不是拿空串去比对（否则所有码都报 1005，极难排查）。

    Raises:
        RuntimeError: `APP_CODE` 为空。
    """
    code = APP_CODE.strip()
    if not code:
        raise RuntimeError("软件编码未配置：请在 license/config.py 填写 APP_CODE")
    return code


def license_file_path() -> Path:
    """授权文件路径（目录由 core.paths 负责创建，此处只拼路径）。"""
    from core.paths import data_dir

    return Path(data_dir()) / LICENSE_FILE_NAME
