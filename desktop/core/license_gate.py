"""授权门禁 —— 未激活时全站拦截（指南 §4.4 ② 的浏览器 UI 等价实现）。

指南用 PySide6 弹窗实现「启动即卡住」；本项目是浏览器 UI，故翻译成：
未激活时除激活页、静态资源、健康检查外一律 302 到 `/activate`，`/api/*` 返回非 2xx 包络。
免拦截清单里必须同时有**页面入口 `/activate`** 与**静态资源前缀**：少任何一个都会让
激活页自己加载不出来（表现为白屏，比一句报错更难排查）。

★ 授权状态从 `app.config["LICENSE_STATE"]` 读，**绝不在请求里重新验签**：
  一次验签含 20 万次 PBKDF2（约 100~300ms），放 before_request 会把整站拖死。
  状态只在「进程启动」与「激活成功」两个时点刷新（见 app.py / views/activation.py）。
★ 拿不到状态（None）时按未激活处理：门禁必须 fail-closed，不能因为状态缺失而放行。
"""
from __future__ import annotations

from flask import Flask, jsonify, redirect, request

from core.response import fail

#: 免拦截路径（未激活也能访问）：激活页本身、健康检查，以及 SPA 的激活接口
#: ★`/api/activation` 必须在这里：它被门禁先于鉴权拦下，否则未激活时前端连
#:   「当前机器码是多少」都查不到，激活页只能是空白（docs/12）。
FREE_PATHS: frozenset[str] = frozenset({"/activate", "/healthz",
                                        "/api/activation/state", "/api/activation"})
#: 免拦截前缀：SPA 的静态资源（含 Vite 产物的 `/assets/*`）。
#: ★拦掉它们 = 未激活时脚本加载不到，激活页本身渲染不出来（白屏，比报错更难查）。
FREE_PREFIXES: tuple[str, ...] = ("/static/", "/assets/")

#: 未激活时给 APP 的文案（安卓端拿到 1013 应引导用户去电脑端激活）
NOT_ACTIVATED_MSG = "本软件尚未激活，请在电脑端完成激活"


def is_free_path(path: str) -> bool:
    """判断路径是否免拦截。"""
    return path in FREE_PATHS or path.startswith(FREE_PREFIXES)


def blocked_response(app: Flask):
    """门禁判定：放行返回 None，否则返回可直接 return 的响应。"""
    if not app.config.get("LICENSE_ENFORCED", True):
        return None
    if is_free_path(request.path):
        return None

    state = app.config.get("LICENSE_STATE")
    if state is not None and getattr(state, "activated", False):
        return None

    if request.path.startswith("/api/"):
        body, status = fail(1013, NOT_ACTIVATED_MSG)
        return jsonify(body), status

    # 页面一律送到 SPA 的 /activate 路由（它免拦截，由前端渲染激活页）。
    # 不再带 `?next=`：SPA 本来就是单页，激活成功后前端自己回登录页，回跳参数只会成为开放重定向的口子。
    return redirect("/activate")
