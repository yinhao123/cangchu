"""集中参数校验 —— PRD 5.3 / 10.1 · docs/03 1.3 错误码 1002 · 陷阱 T-09

所有 api 必须经本模块，禁止各接口重复写校验或"顺手"放宽。
★ 条码不合规直接报错，**不做自动修正后静默通过**（用户会以为存进去了却查不到）。
★ client_msg_id 必填、无默认值、不自动生成（T-09：留了退路就一定会被忘）。
"""
from __future__ import annotations

import re
import uuid

from core.errors import BizError

BARCODE_MIN, BARCODE_MAX = 3, 48
_BARCODE_CHARS = re.compile(r"^[0-9A-Za-z\-]+$")
_FULLWIDTH = str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ－　",
                           "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz- ")

QUANTITY_MAX = 100000
PAGE_SIZES = (20, 50, 100)


def _field_err(field: str, msg: str) -> BizError:
    return BizError(1002, "参数校验失败", {"errors": {field: [msg]}})


def normalize_barcode(value) -> str | None:
    """strip + 全角转半角 + 去空格；长度 3~48；字符集仅 [0-9A-Za-z-]。

    传 None / 空串 → 返回 None（表示「无条码」，与「非法条码」区分开）。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise _field_err("barcode", "条码必须是字符串")
    text = value.translate(_FULLWIDTH).strip()
    if text == "":
        return None
    if " " in text:
        raise _field_err("barcode", "条码内不能含空格")
    if not _BARCODE_CHARS.match(text):
        raise _field_err("barcode", "条码只能包含字母、数字与连字符")
    if not (BARCODE_MIN <= len(text) <= BARCODE_MAX):
        raise _field_err("barcode", f"条码长度需在 {BARCODE_MIN}~{BARCODE_MAX} 位之间")
    return text


def require_client_msg_id(payload: dict) -> str:
    val = (payload or {}).get("client_msg_id")
    if not val:
        raise _field_err("client_msg_id", "缺少 client_msg_id（离线去重与幂等的唯一依据）")
    if not isinstance(val, str) or len(val) > 36:
        raise _field_err("client_msg_id", "client_msg_id 必须是不超过 36 位的字符串")
    try:
        parsed = uuid.UUID(val)
    except (ValueError, AttributeError):
        raise _field_err("client_msg_id", "client_msg_id 必须是 UUID")
    if parsed.version not in (1, 4):
        raise _field_err("client_msg_id", "client_msg_id 必须是 UUID v4")
    return str(parsed)


def require_positive_int(value, name: str, max_value: int = QUANTITY_MAX) -> int:
    """数量：整数、>0、有上限。小数/0/负数/字符串数字一律拒绝（不自动取整、不自动改符号）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _field_err(name, f"{name} 必须是整数")
    if value <= 0:
        raise _field_err(name, f"{name} 必须大于 0")
    if value > max_value:
        raise _field_err(name, f"{name} 不能超过 {max_value}")
    return value


def require_int(value, name: str, minimum: int = 0, maximum: int = 10 ** 9, default: int = 0) -> int:
    """None 表示「未填写」→ 取默认值（这些列有 DB 默认 0）；填了非法值才报错。"""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise _field_err(name, f"{name} 必须是整数")
    if not (minimum <= value <= maximum):
        raise _field_err(name, f"{name} 需在 {minimum}~{maximum} 之间")
    return value


def require_str(value, name: str, max_len: int = 100, required: bool = True,
                min_len: int = 1) -> str | None:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        if required:
            raise _field_err(name, f"{name} 不能为空")
        return None
    if not isinstance(value, str):
        raise _field_err(name, f"{name} 必须是字符串")
    text = value.strip()
    if not (min_len <= len(text) <= max_len):
        raise _field_err(name, f"{name} 长度需在 {min_len}~{max_len} 之间")
    return text


def require_choice(value, name: str, choices: tuple[str, ...]) -> str:
    text = require_str(value, name, max_len=32)
    upper = str(text).upper()  # 枚举统一大写（T-20），角色不在此列
    if upper not in choices:
        raise _field_err(name, f"{name} 只能是 {'/'.join(choices)}")
    return upper


def require_money(value, name: str) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _field_err(name, f"{name} 必须是数字")
    if value < 0:
        raise _field_err(name, f"{name} 不能为负")
    return round(float(value), 2)  # 只在入口与展示两处 round（T-21）


def require_id(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _field_err(name, f"{name} 必须是正整数")
    return value


def validate_product_payload(payload: dict, partial: bool = False) -> dict:
    """商品新增/编辑共用。partial=True 时未出现的字段不改动（含 barcode 不传=不改）。"""
    payload = payload or {}
    errors: dict[str, list[str]] = {}
    out: dict = {}
    try:
        if "name" in payload or not partial:
            out["name"] = require_str(payload.get("name"), "name", max_len=100)
    except BizError as e:
        errors.update(e.data["errors"])
    if "barcode" in payload or not partial:
        try:
            out["barcode"] = normalize_barcode(payload.get("barcode"))
        except BizError as e:
            errors.update(e.data["errors"])
    for key, getter in (
        # 分类/单位可缺省（用默认值），填了非法值才报错
        ("category", lambda: require_str(payload.get("category"), "category", max_len=30, required=False) or "未分类"),
        ("unit", lambda: require_str(payload.get("unit"), "unit", max_len=10, required=False) or "件"),
        ("cost_price", lambda: require_money(payload.get("cost_price"), "cost_price")),
        ("sale_price", lambda: require_money(payload.get("sale_price"), "sale_price")),
        ("min_stock", lambda: require_int(payload.get("min_stock"), "min_stock")),
        ("remark", lambda: require_str(payload.get("remark"), "remark", max_len=200, required=False)),
    ):
        if key in payload or not partial:
            try:
                out[key] = getter()
            except BizError as e:
                errors.update(e.data["errors"])
    if "expected_updated_at" in payload:
        exp = payload["expected_updated_at"]
        from services import timeutil
        if not timeutil.is_valid_iso(str(exp)):
            errors.setdefault("expected_updated_at", []).append("乐观锁版本戳无效")
        else:
            out["expected_updated_at"] = str(exp)
    if errors:
        raise BizError(1002, "参数校验失败", {"errors": errors})
    return out


def clamp_page(page, page_size) -> tuple[int, int]:
    """page_size 白名单，非法回落 20；page 非法回落 1（PRD 7.2）。"""
    try:
        p = int(page)
    except (TypeError, ValueError):
        p = 1
    try:
        s = int(page_size)
    except (TypeError, ValueError):
        s = 20
    return max(p, 1), (s if s in PAGE_SIZES else 20)
