"""REST API 蓝图注册（PRD 8.3：33 个接口的挂载点）

本层只做「取参 → validators 校验 → 调 services → core/response 包络」，
禁止写 SQL、判业务规则、省略可空字段（安卓 data class 依赖键存在）。
"""
from __future__ import annotations

from flask import Flask

MODULES = ("ping", "auth", "products", "stock", "sync", "reports", "system",
           "users", "activation", "labels", "wms_purchase", "wms_operations", "wms_stocktake",
           "wms_common", "imports", "print", "wms_sales")


def register_api_blueprints(app: Flask) -> None:
    from importlib import import_module
    for name in MODULES:
        mod = import_module(f"api.{name}")
        app.register_blueprint(mod.bp)
