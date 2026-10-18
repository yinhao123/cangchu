"""标签打印接口 —— PRD 5.7 · docs/03 第 10 章

SPA 无法像 Jinja 那样内联 `| safe` 的 SVG（全局唯一例外，且必须先过白名单），
所以条码图改为**独立图片资源**：`/api/products/<id>/barcode.svg|.png` 直接返回图片，
前端用 `<img src>` 引用 —— JS 里不做任何 HTML 注入，白名单校验仍在 `net/barcode_gen.py` 里生效。

尺寸与张数上限取自 `settings.label_size`（PRD 5.7：40x30 / 50x30 / 60x40 × 1~50 张）。
"""
from __future__ import annotations

import base64

from flask import Blueprint, Response, jsonify, request

from core.acl import require_login
from core.errors import BizError
from core.response import ok
from net import barcode_gen
from services import product_service, settings_service

bp = Blueprint("api_labels", __name__)


def label_size_text() -> str:
    """当前标签纸尺寸串；配置缺失/非法一律回落默认（`label_size()` 内部已兜底）。"""
    return settings_service.typed("label_size", barcode_gen.DEFAULT_LABEL_SIZE) \
        or barcode_gen.DEFAULT_LABEL_SIZE


def _image(product_id: int) -> tuple[dict, str, float]:
    product = product_service.get(product_id)
    text = barcode_gen.code_for(product)
    width_mm, _ = barcode_gen.label_size(label_size_text())
    return product, text, float(width_mm)


@bp.get("/api/labels")
@require_login
def labels():
    """待打印商品列表：在商品分页结果上补「真实码值 / 是否过窄 / 标签尺寸」。"""
    result = product_service.list_products(request.args.to_dict())
    width_mm, height_mm = barcode_gen.label_size(label_size_text())
    items = []
    for product in result["list"]:
        text = barcode_gen.code_for(product)
        try:
            narrow = barcode_gen.too_narrow(text, width_mm)
        except barcode_gen.BarcodeError:
            # 单个商品算不出位串不能让整页失败：图片接口会把具体原因给出来
            narrow = False
        items.append({**product, "barcode_text": text, "narrow": narrow})
    return jsonify(ok({
        **result,
        "list": items,
        "label_size": f"{width_mm}x{height_mm}",
        "label_w": width_mm,
        "label_h": height_mm,
        "max_copies": barcode_gen.MAX_COPIES,
    })[0]), 200


@bp.get("/api/products/<int:product_id>/barcode.svg")
@require_login
def barcode_svg(product_id: int):
    """矢量条码（打印不糊）。响应即图片，前端直接 `<img src>`。"""
    _, text, width_mm = _image(product_id)
    try:
        svg = barcode_gen.code128_svg(text, width_mm)
    except barcode_gen.BarcodeError as exc:
        raise BizError(1002, "条码生成失败，请检查条码内容",
                       {"errors": {"barcode": [str(exc)]}}) from exc
    return Response(svg, mimetype="image/svg+xml")


@bp.get("/api/products/<int:product_id>/barcode.png")
@require_login
def barcode_png(product_id: int):
    """位图条码（导出 / 不支持 SVG 的场合）。"""
    _, text, _width_mm = _image(product_id)
    try:
        raw = base64.b64decode(barcode_gen.code128_png_base64(text))
    except barcode_gen.BarcodeError as exc:
        raise BizError(1002, "条码生成失败，请检查条码内容",
                       {"errors": {"barcode": [str(exc)]}}) from exc
    return Response(raw, mimetype="image/png")
