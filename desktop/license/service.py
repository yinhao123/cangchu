"""激活业务流程编排：采集机器码 → 验证配置 → 验签 → 落授权文件 → 供门禁查询状态。

为什么要这一层（指南把这段逻辑放在 UI 里）：本项目是浏览器 UI，
激活发生在 HTTP 请求线程、门禁判断发生在 before_request，两处都要用到同一套编排，
放 UI 层会导致逻辑重复且无法单测。

★ 本层是 license 包错误码/异常的**唯一出口**：`license.errors.BizError` 与 RuntimeError
  都不允许逃逸到业务层（1001~1008 与 core.errors 的 API 错误码语义完全不同）。
  一切异常都转成 LicenseState 返回。
★ 本层**不做缓存**：状态由调用方持有（Flask 侧放 `app.config["LICENSE_STATE"]`）。
  一次 refresh 含 20 万次 PBKDF2（约 100~300ms），所以每请求调用它是不允许的 ——
  只在「进程启动」与「激活成功」两个时点调用。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from license import config, store, verifier
from license import machine_id as machine_id_module
from license.errors import ERR_LICENSE_FILE_MISSING, ERR_UNKNOWN, BizError, message_for

logger = logging.getLogger(__name__)

#: `APP_CODE`/公钥出问题时的统一文案（细节另放 config_error 字段，供客服排查）
CONFIG_BROKEN_MESSAGE = "授权组件不可用，请重新安装客户端"


@dataclass(frozen=True)
class LicenseState:
    """一次授权判定的完整结果，供界面与门禁共用。"""

    activated: bool
    message: str
    code: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    machine_id: str = ""
    display_machine_id: str = ""
    #: 非空 = 客户端自带的授权配置有问题（公钥缺失 / APP_CODE 未配置），
    #: 与「用户还没激活」是两回事，入口层必须区别对待（见 app.py）
    config_error: str = ""

    @property
    def expire_ts(self) -> int:
        """到期时间（UTC 秒）；0 表示永久。"""
        try:
            return int(self.payload.get("x", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def expire_text(self) -> str:
        """面向用户的有效期文案（未激活时为空串，由模板降级显示状态文案）。"""
        if not self.activated:
            return ""
        ts = self.expire_ts
        if not ts:
            return "永久有效"
        from services.timeutil import TZ_CN

        return "有效期至 " + datetime.fromtimestamp(ts, TZ_CN).strftime("%Y-%m-%d")


def _mk(activated: bool, message: str, code: int = 0, payload: dict | None = None,
        standard: str = "", display: str = "", config_error: str = "") -> LicenseState:
    return LicenseState(activated, message, code, payload or {}, standard, display, config_error)


def _machine_id_pair() -> tuple[str, str]:
    """返回 (标准形式, 显示形式)。

    Raises:
        BizError: 四项硬件信息全部采集失败（1000）。
    """
    standard = machine_id_module.get_machine_id()
    return standard, machine_id_module.format_machine_id(standard)


def _config_problem() -> str | None:
    """检查客户端自带的授权配置；返回人话原因，正常时返回 None。

    这两个问题都不是「用户还没激活」，所以不能降级成未激活文案 ——
    否则用户会拿着正确的注册码反复重试，而每次都报看起来与注册码有关的错误。
    """
    try:
        if config.VERIFY_SIGNATURE:
            config.public_key_pem()
        config.app_code()
    except RuntimeError as exc:
        return str(exc)
    return None


def _public_key_or_empty() -> bytes:
    """跳验签时不读公钥文件（联调期 keys/public.pem 还没放）。"""
    return config.public_key_pem() if config.VERIFY_SIGNATURE else b""


def _config_broken(problem: str, standard: str, display: str) -> LicenseState:
    logger.critical("授权组件不可用：%s", problem)
    return _mk(False, CONFIG_BROKEN_MESSAGE, ERR_UNKNOWN, standard=standard,
               display=display, config_error=problem)


def refresh_state(now_ts: int | None = None) -> LicenseState:
    """读授权文件并**重新验签**（不是只判断文件存在）。

    指南 §4.4 ②：用户复制一份改过的授权文件就能绕过「只看文件存在」的检查，
    所以每次启动都必须拿文件里的注册码走一遍完整验签。

    Args:
        now_ts: 当前 UTC 时间戳；仅测试注入用（对应自测 T7）。
    """
    try:
        standard, display = _machine_id_pair()
    except BizError as exc:
        logger.warning("机器码采集失败：%s", exc.code)
        return _mk(False, exc.message, exc.code)

    problem = _config_problem()
    if problem:
        return _config_broken(problem, standard, display)

    path = config.license_file_path()
    try:
        data = store.load_license_file(path, standard)
    except BizError as exc:            # 1006 换机 / 文件被改
        return _mk(False, exc.message, exc.code, standard=standard, display=display)
    if data is None:                   # 1007 尚未激活
        return _mk(False, message_for(ERR_LICENSE_FILE_MISSING), ERR_LICENSE_FILE_MISSING,
                   standard=standard, display=display)

    result = verifier.verify_license(
        str(data.get("code", "")), _public_key_or_empty(), config.APP_CODE.strip(),
        standard, now_ts=now_ts, verify_signature=config.VERIFY_SIGNATURE,
    )
    if not result.ok:
        return _mk(False, result.message, result.code, standard=standard, display=display)
    return _mk(True, "已激活", 0, result.payload, standard, display)


def activate(code_text: str) -> LicenseState:
    """用注册码激活本机；成功即写授权文件。

    Returns:
        LicenseState；`activated=False` 时 `message` 是可直接展示给用户的中文文案。
    """
    try:
        standard, display = _machine_id_pair()
    except BizError as exc:
        return _mk(False, exc.message, exc.code)

    problem = _config_problem()
    if problem:
        return _config_broken(problem, standard, display)

    result = verifier.verify_license(
        code_text, _public_key_or_empty(), config.APP_CODE.strip(), standard,
        verify_signature=config.VERIFY_SIGNATURE,
    )
    if not result.ok:
        return _mk(False, result.message, result.code, standard=standard, display=display)

    store.save_license_file(
        config.license_file_path(),
        {
            "code": "".join(code_text.split()),   # 落盘即清洗空白，避免换行随文件带下去
            "payload": result.payload,
            "activated_at": _now_iso(),
            "app_version": config.APP_VERSION,
        },
        standard,
    )
    return _mk(True, "激活成功", 0, result.payload, standard, display)


def _now_iso() -> str:
    """授权文件内的激活时间：走项目唯一时间出口（东八区 ISO-8601）。"""
    from services.timeutil import now_iso

    return now_iso()


__all__ = ["CONFIG_BROKEN_MESSAGE", "LicenseState", "activate", "refresh_state"]
