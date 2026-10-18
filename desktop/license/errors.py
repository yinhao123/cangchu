"""客户端错误码与面向用户的中文文案。

★ 这套码与 core.errors 的 API 错误码**不是同一空间**（1001~1008 两边含义完全不同），
  因此本模块的 BizError 只允许在 license 包内部传播，绝不能让 api 层 errorhandler 按 API 码解释。
  service.py 会把它们全部转成 LicenseState / VerifyResult 再交给业务层。
"""

# ★Python 3.8（Win7 服务端）会在导入期求值注解，缺这行用 PEP 585/604 会 TypeError（docs/12）
from __future__ import annotations

from typing import Final

ERR_UNKNOWN: Final[int] = 1000
ERR_LICENSE_FORMAT: Final[int] = 1001
ERR_SIGNATURE_INVALID: Final[int] = 1002
ERR_MACHINE_MISMATCH: Final[int] = 1003
ERR_LICENSE_EXPIRED: Final[int] = 1004
ERR_APP_MISMATCH: Final[int] = 1005
ERR_LICENSE_FILE_CORRUPT: Final[int] = 1006
ERR_LICENSE_FILE_MISSING: Final[int] = 1007
ERR_LICENSE_VERSION: Final[int] = 1008

#: 错误码 → 可直接展示给用户的人话文案（不含任何内部细节）
MESSAGES: Final[dict[int, str]] = {
    ERR_UNKNOWN: "未知错误，请截图后联系卖家",
    ERR_LICENSE_FORMAT: "注册码格式不正确，请确认完整复制（注意不要漏掉或多加字符）",
    ERR_SIGNATURE_INVALID: "注册码校验失败，请联系卖家确认",
    ERR_MACHINE_MISMATCH: "该注册码不适用于本机，请把本机机器码发给卖家重新出码",
    ERR_LICENSE_EXPIRED: "注册码已过期，请续费",
    ERR_APP_MISMATCH: "该注册码不适用于当前软件",
    ERR_LICENSE_FILE_CORRUPT: "本地授权文件已损坏，请重新激活",
    ERR_LICENSE_FILE_MISSING: "尚未激活",
    ERR_LICENSE_VERSION: "注册码版本过新，请升级软件",
}


def message_for(code: int) -> str:
    """取错误码对应的中文文案；未登记时返回通用文案。"""
    return MESSAGES.get(code, MESSAGES[ERR_UNKNOWN])


class BizError(Exception):
    """只带错误码与可展示中文文案的业务异常。"""

    def __init__(self, code: int, message: str | None = None) -> None:
        self.code = code
        self.message = message or message_for(code)
        super().__init__(self.message)
