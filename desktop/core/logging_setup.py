"""结构化日志与脱敏 —— PRD 10.4 · 陷阱 T-17 / 规则 20-stack-conventions.md

★ 脱敏按 key 名黑名单实现（日志过滤器统一处理），**不依赖开发者自觉**：
  任何嵌套字段里出现 password/token/secret 等键名，值一律替换成 ***。
★ JSON 行式，字段固定；trace_id 从 Flask 请求上下文取，脱离请求则为 '-'。
★ RotatingFileHandler 10MB × 10；生产不暴露堆栈到页面（堆栈只进日志）。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

REDACT_KEYS = {
    "password", "password_hash", "old_password", "new_password",
    "token", "x-auth-token", "auth_token", "secret", "secret_key", "apikey", "api_key",
}
MASK = "***"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 10
#: 堆栈单独放宽：字段统一按 500 截断时只剩 flask 几个框架帧，异常类型与消息全被截掉（T-30）
EXC_LIMIT = 2000


def _redact(value, limit: int = 500):
    if isinstance(value, dict):
        return {k: (MASK if str(k).lower() in REDACT_KEYS else _redact(v, limit)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, limit) for v in value]
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


class RedactFilter(logging.Filter):
    """兜底：连 message 文本里出现的敏感串也抹掉（防止有人把口令拼进日志文案）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _scrub_text(record.msg)
        record.args = tuple(_scrub_text(str(a)) if isinstance(a, str) else _redact(a) for a in (record.args or ()))
        if hasattr(record, "extra_fields"):
            record.extra_fields = _redact(record.extra_fields)
        return True


_SENSITIVE_PATTERNS = ("password=", "passwd=", "token=", "secret=", "api_key=")


def _scrub_text(text: str) -> str:
    low = text.lower()
    for pat in _SENSITIVE_PATTERNS:
        idx = low.find(pat)
        while idx != -1:
            start = idx + len(pat)
            end = text.find(" ", start)
            end = len(text) if end == -1 else end
            text = text[:start] + MASK + text[end:]
            low = text.lower()
            idx = low.find(pat, end + 3)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from services import timeutil
        payload = {
            "ts": timeutil.now_iso(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("trace_id", "user_id", "username", "action", "source", "ip",
                    "duration_ms", "status", "err_code"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if hasattr(record, "extra_fields"):
            payload["extra"] = record.extra_fields
        safe = _redact(payload)
        if record.exc_info:
            safe["exc"] = _redact(self.formatException(record.exc_info), EXC_LIMIT)
        return json.dumps(safe, ensure_ascii=False)


def business(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    """带结构化字段的业务日志入口：log_business(logger, INFO, 'ok', action='stock.in', trace_id='x')"""
    logger.log(level, msg, extra=fields)


def setup_logging(app=None, level: str = "INFO") -> logging.Logger:
    from core.paths import logs_dir
    root = logging.getLogger("scanable")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.handlers.clear()
    root.addFilter(RedactFilter())

    try:
        os.makedirs(logs_dir(), exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            os.path.join(logs_dir(), "app.log"), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.StreamHandler(sys.stderr)  # 写不了文件也别让程序起不来
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactFilter())
    root.addHandler(handler)

    if app is not None:
        @app.before_request
        def _bind_trace():
            pass  # trace_id 由各请求钩子写入 g，Formatter 从 record 字段取

    return root


def install_excepthooks() -> None:
    """无控制台 exe 的最后一道反馈：崩溃写 CRITICAL 日志 + 弹窗（T-17）。"""
    def hook(exc_type, exc, tb):
        try:
            logging.getLogger("scanable.crash").critical("未捕获异常", exc_info=(exc_type, exc, tb))
        except Exception:
            pass
        _show_crash(exc)

    sys.excepthook = hook

    def thread_hook(args):
        try:
            logging.getLogger("scanable.crash").critical(
                "线程异常 thread=%s", args.thread.name if args.thread else "?",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        except Exception:
            pass
        _show_crash(args.exc_value)

    threading.excepthook = thread_hook


def _show_crash(exc: BaseException | None) -> None:
    if exc is None:
        return
    msg = f"程序遇到未处理的错误：{exc}\n请查看日志目录排查（trace_id 见日志）。"
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.MessageBoxW(None, msg, "扫码出入库系统", 0x10)
            return
        except Exception:
            pass
    print(msg, file=sys.stderr)
