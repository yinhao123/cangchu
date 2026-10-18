"""时间工具 —— PRD 4.3.3 / 12.1 · 陷阱 T-11

全局唯一时间出口。所有 created_at/updated_at 必须来自 now_iso()。
格式 2026-10-15T17:30:12+08:00（东八区、秒精度、ISO-8601 带偏移）。
字典序 == 时间序，故「今日」用 substr(created_at,1,10) 前缀比较（可用索引）。

★ 禁止 datetime.now() 裸调用 / utcnow() / SQLite CURRENT_TIMESTAMP（它存 UTC 无时区）/ Unix 秒。
★ 时区不开放配置：自用场景「今日」必须按本地日历日切分（T-11）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from core.errors import BizError

TZ_CN = timezone(timedelta(hours=8))
MAX_SPAN_DAYS = 366  # PRD 5.6：报表最大查询跨度


def now_iso() -> str:
    """唯一时间生成入口（东八区 ISO-8601，秒精度）。"""
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def now_dt() -> datetime:
    return datetime.now(TZ_CN)


def iso_from_epoch(epoch: float) -> str:
    """Unix 秒 → 统一时间格式。

    只用于**外部时间源**（如备份文件的 mtime）落库/出参；业务时间一律走 now_iso()。
    集中在这里是为了避免各处再写 `time.strftime(..., time.localtime())` ——
    那种写法依赖机器时区，跨机器取值可能不是东八区。
    """
    return datetime.fromtimestamp(epoch, TZ_CN).isoformat(timespec="seconds")


def today_prefix(d: date | None = None) -> str:
    """返回 'YYYY-MM-DD'，用于 created_at 前缀比较。"""
    if d is None:
        return now_iso()[:10]
    return d.strftime("%Y-%m-%d")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def is_valid_iso(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def day_range(date_from: str, date_to: str) -> tuple[str, str]:
    """校验并归一化日期区间（闭区间）；跨度 >366 天 → BizError(1002)。"""
    try:
        d1 = date.fromisoformat(date_from)
        d2 = date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise BizError(1002, "参数校验失败", {"errors": {"date": ["日期格式必须是 YYYY-MM-DD"]}})
    if d1 > d2:
        raise BizError(1002, "参数校验失败", {"errors": {"date": ["起始日期不能晚于结束日期"]}})
    if (d2 - d1).days > MAX_SPAN_DAYS:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"date": [f"最多查询 {MAX_SPAN_DAYS} 天，请缩小范围"]}})
    return d1.strftime("%Y-%m-%d"), d2.strftime("%Y-%m-%d")
