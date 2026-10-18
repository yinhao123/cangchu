"""登录失败锁定 —— PRD 5.1

内存计数（重启即解除，自用场景已在 PRD 5.1 显式声明可接受）。
同用户名 5 次 / 同 IP 20 次 → 锁 15 分钟，返回剩余秒数供提示。
"""
from __future__ import annotations

import threading
import time

MAX_USER_FAILS = 5
MAX_IP_FAILS = 20
LOCK_SECONDS = 900

_lock = threading.Lock()
_user_fails: dict[str, list[int]] = {}   # username -> [次数, 解锁时间戳]
_ip_fails: dict[str, list[int]] = {}


def check(username: str, ip: str | None) -> int | None:
    """返回还需等待的秒数；None 表示未锁定。"""
    now = time.time()
    with _lock:
        for bucket in (_user_fails.get(username), _ip_fails.get(ip or "")):
            if bucket and len(bucket) > 1 and bucket[1] > now:
                return int(bucket[1] - now) + 1
    return None


def record_failure(username: str, ip: str | None) -> None:
    now = time.time()
    with _lock:
        u = _user_fails.setdefault(username, [0, 0])
        u[0] += 1
        if u[0] >= MAX_USER_FAILS:
            u[1] = now + LOCK_SECONDS
            u[0] = 0
        if ip:
            i = _ip_fails.setdefault(ip, [0, 0])
            i[0] += 1
            if i[0] >= MAX_IP_FAILS:
                i[1] = now + LOCK_SECONDS
                i[0] = 0


def reset(username: str | None = None, ip: str | None = None) -> None:
    with _lock:
        if username is None and ip is None:
            _user_fails.clear()
            _ip_fails.clear()
            return
        _user_fails.pop(username, None)
        _ip_fails.pop(ip, None)
