"""权限装饰器 —— PRD 3.1 / 3.2 / 3.3 · docs/03 1.2

浏览器端与手机端现在**共用同一套认证**：`X-Auth-Token`（`app.py` 的 before_request
校验后注入 `g.user`）。旧的 Flask-Login 会话随 Jinja 页面一起下线 —— 两套认证并存时，
最容易出的问题是「一边拦住了、另一边漏了」。
本模块提供两级守卫，**所有需要权限的接口必须带**，禁止绕过（CODEBUDDY.md 禁止事项 4）。
"""
from __future__ import annotations

import functools
from typing import Any

from flask import g

from core.errors import BizError


def current_user() -> dict[str, Any]:
    user = getattr(g, "user", None)
    if user is None:
        raise BizError(1003, "未登录")
    return user


def require_login(fn):
    """API 用：未登录 → 401 包络。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        current_user()
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    """API 用：非 admin → 403 包络（PRD 3.1 权限矩阵）。

    `must_change_password` 的账号在这里再拦一道（全量拦截还有 `app.py` 的那道全局门禁）：
    双保险不冗余 —— 装饰器漏挂是真实存在的回归方式。
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user.get("role") != "admin":
            raise BizError(1004, "该操作仅管理员可执行")
        if user.get("must_change_password"):
            raise BizError(1004, "请先修改初始密码")
        return fn(*args, **kwargs)
    return wrapper
