"""Code128 条码生成 —— PRD 5.7 / 5.1
python-barcode 只用来**算位串**（code.build() → '110100...'），图形部分自己拼 `<rect>`：
它的 SVGWriter 输出含 `<!-- ... -->`、`style="fill:black;"`、`id="barcode_group"`，
`!` `;` `_` 都在 PRD 5.1 白名单字符集之外，内联进 HTML 会被自己的安全校验拦下（实测踩到）。
行级人可读文本由页面用 HTML 渲染（Jinja 自动转义），SVG 里不写 `<text>`：码值含 `&`/`;` 时
转义后同样破坏白名单，而页面文本**任何字符都显示得出来**。
无条码商品用内码 'S'+10 位补零 id；生成失败时页面显示可读码值 + 原因，**禁止输出空白图**。
"""
from __future__ import annotations

import base64
import io
import logging
import re
from functools import lru_cache

log = logging.getLogger("scanable.barcode")

#: 内码前缀 + 固定 10 位：'S0000000123'。字母开头，与 EAN/UPC 的纯数字天然不冲突（PRD 5.7）
INTERNAL_PREFIX = "S"
INTERNAL_WIDTH = 10
#: 单次可打印张数上限（PRD 5.7：1~50）
MAX_COPIES = 50
DEFAULT_LABEL_SIZE = "40x30"
#: SVG 内联白名单（PRD 5.1）：这是全局唯一允许 | safe 的地方，必须先过白名单再交给模板
SVG_ALLOWED = re.compile(r'^[0-9a-zA-Z<>/=" #.,%:\-]*$')
#: 标签纸尺寸串校验：40x30 / 50x30 / 60x40（mm）
SIZE_RE = re.compile(r"^(\d{2,3})x(\d{2,3})$")
#: Code128 规范要求的静区（左右各留 10 个模块），自绘时计入总宽
QUIET_MODULES = 10
#: 标签内边距与条码条高（mm）
MARGIN_MM = 1.5
BAR_HEIGHT_MM = 10.0
#: 模块宽低于此值（mm）时热敏打印机会糊成一片：页面要给「换更宽的标签纸」提示
MIN_MODULE_MM = 0.25
#: python-barcode 位图（PNG）参数；PNG 是二进制、不内联 HTML，故不受白名单约束
PNG_OPTIONS = {"module_width": 0.22, "module_height": 6.0, "font_size": 5,
               "text_distance": 1.0, "quiet_zone": 1.0, "write_text": True}


class BarcodeError(RuntimeError):
    """条码生成失败：页面必须显示可读文本 + 原因，不能只留一块空白。"""


def internal_code(product_id: int) -> str:
    """内码 = 'S' + 10 位补零 id。派生值，不写库：商品 ID 本身已唯一且不可变。"""
    return f"{INTERNAL_PREFIX}{int(product_id):0{INTERNAL_WIDTH}d}"


def code_for(product: dict) -> str:
    """该商品要打印的码：优先真实条码，缺失时用内码（PRD 5.7）。"""
    return (product.get("barcode") or "").strip() or internal_code(product["id"])


def label_size(value: str | None = None) -> tuple[int, int]:
    """解析标签尺寸（如 '40x30'）为 (宽mm, 高mm)；非法值回落默认，不弹错。"""
    match = SIZE_RE.match((value or "").strip()) or SIZE_RE.match(DEFAULT_LABEL_SIZE)
    return int(match.group(1)), int(match.group(2))


def module_mm(text: str, width_mm: float = 40.0) -> float:
    """单模块宽度（mm）。小于 MIN_MODULE_MM 时页面提示换标签纸，而不是让用户打出扫不到的码。"""
    bits = _bits(text)
    usable = max(8.0, float(width_mm) - MARGIN_MM * 2)
    return usable / (len(bits) + 2 * QUIET_MODULES)


def too_narrow(text: str, width_mm: float = 40.0) -> bool:
    """是否窄到打不出可扫的码（'S0000000123' 在 40mm 上约 0.28mm，是常见标签的下限附近）。"""
    return module_mm(text, width_mm) < MIN_MODULE_MM


def code128_svg(text: str, width_mm: float = 40.0, bar_height_mm: float = BAR_HEIGHT_MM) -> str:
    """自绘 Code128 条形码 SVG（矢量，打印不糊）。返回值已过 PRD 5.1 白名单校验。"""
    bits = _bits(text)
    bars = [(m.start(), len(m.group())) for m in re.finditer(r"1+", bits)]
    if not bars:
        raise BarcodeError("条码位串为空")
    module = module_mm(text, width_mm)
    height = float(bar_height_mm) + MARGIN_MM * 2
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm:.2f}mm" '
             f'height="{height:.2f}mm" viewBox="0 0 {width_mm:.2f} {height:.2f}">',
             f'<rect x="0" y="0" width="{width_mm:.2f}" height="{height:.2f}" fill="#FFFFFF"/>']
    for start, length in bars:
        x = MARGIN_MM + (start + QUIET_MODULES) * module
        parts.append(f'<rect x="{x:.2f}" y="{MARGIN_MM:.2f}" width="{length * module:.2f}" '
                     f'height="{bar_height_mm:.2f}" fill="#000000"/>')
    parts.append("</svg>")
    svg = "".join(parts)
    if not SVG_ALLOWED.match(svg):
        bad = sorted({ch for ch in svg if not SVG_ALLOWED.match(ch)})
        raise BarcodeError(f"条码图含非白名单字符，已拒绝内联：{bad[:8]}")
    return svg


def code128_png_base64(text: str) -> str:
    """位图形态（APP/导出用）。PNG 二进制不进 HTML，故无需白名单校验。"""
    if not str(text or "").strip():
        raise BarcodeError("条码内容为空")
    buf = io.BytesIO()
    try:
        import barcode
        from barcode.writer import ImageWriter
        barcode.get("code128", str(text), writer=ImageWriter()).write(buf, options=dict(PNG_OPTIONS))
    except Exception as exc:  # 字体缺失/字符集不支持等，都归为「生成失败」并保留原因
        log.warning("条码位图生成失败 text=%s: %s", text, exc)
        raise BarcodeError(f"{type(exc).__name__}: {exc}") from exc
    return base64.b64encode(buf.getvalue()).decode("ascii")


@lru_cache(maxsize=512)
def _bits(text: str) -> str:
    """取 Code128 位串（同一商品每次渲染都算一遍没必要，缓存住）。

    异常统一转 BarcodeError：调用方要能把原因显示在页面上，而不是只留白。
    """
    if not str(text or "").strip():
        raise BarcodeError("条码内容为空")
    try:
        from barcode.codex import Code128
        return "".join(Code128(str(text)).build())
    except BarcodeError:
        raise
    except Exception as exc:
        log.warning("条码生成失败 text=%s: %s", text, exc)
        raise BarcodeError(f"{type(exc).__name__}: {exc}") from exc
