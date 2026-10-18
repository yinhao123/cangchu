"""网页端蓝图注册 —— SPA 化后只剩「浏览器客户端」这一项。

旧的服务端渲染页面（登录 / 仪表盘 / 主数据 / 出入库 / 报表 / 设置 / 账号 / 备份，
共 28 个模板）已全部下线，能力改由 `api/` 的 JSON 接口 + `desktop/web` 的 SPA 承担，
决策与理由见 `docs/09-扫雷审查与决策记录.md`。

保留项：`templates/errors/{403,404,500}.html` 仍由 `app.py` 的 errorhandler 渲染 ——
非 `/api` 路径出错时要给人看的是一页提示，而不是 JSON（`/api` 路径仍回 JSON 包络）。
"""
from __future__ import annotations

from flask import Flask

MODULES = ("spa",)


def register_web_blueprints(app: Flask) -> None:
    from importlib import import_module
    for name in MODULES:
        mod = import_module(f"views.{name}")
        app.register_blueprint(mod.bp)
