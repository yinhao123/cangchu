"""D0 地基测试（TDD RED 起点）—— PRD 12.2「Day1 门禁」+ docs/04 T-11

覆盖三件最基础、错了会污染全项目的事：
  1. 时间唯一出口 timeutil.now_iso()（T-11：混用 UTC/本地会让「今日」统计口径跳变）
  2. 统一响应包络 core/response（可空字段必须返回 null 而非省略键 —— 安卓 data class 依赖）
  3. 错误码 → HTTP 状态码映射（code!=0 时 HTTP 必须非 2xx）
"""
import pytest

from services import timeutil
from core.response import ok, fail
from core.errors import BizError, HTTP_BY_CODE


# ---------- 1. 时间：东八区 ISO-8601 带偏移 ----------

def test_now_iso_returns_eight_hour_offset_with_seconds():
    v = timeutil.now_iso()
    assert v.endswith("+08:00"), f"必须带东八区偏移，实际：{v}"
    assert len(v) == 25, f"秒精度 ISO-8601 应为 25 字符，实际：{v!r}"
    # 形如 2026-10-15T17:30:12+08:00
    date_part, time_part = v[:10], v[11:19]
    assert date_part[4] == "-" and date_part[7] == "-"
    assert time_part[2] == ":" and time_part[5] == ":"


def test_iso_strings_sort_in_chronological_order():
    """字典序 == 时间序，「今日」才能用 substr 前缀比较走索引（PRD 4.3.3 / T-11）"""
    earlier, later = "2026-10-15T23:59:59+08:00", "2026-10-16T00:00:00+08:00"
    assert earlier < later
    assert sorted([later, earlier]) == [earlier, later]


def test_today_prefix_is_ten_char_date():
    p = timeutil.today_prefix()
    assert len(p) == 10 and p[4] == "-" and p[7] == "-"


def test_day_range_rejects_span_over_366_days():
    from core.errors import BizError as E
    with pytest.raises(E) as ei:
        timeutil.day_range("2020-01-01", "2026-01-01")
    assert ei.value.code == 1002


def test_day_range_accepts_normal_span():
    assert timeutil.day_range("2026-10-01", "2026-10-15") == ("2026-10-01", "2026-10-15")


# ---------- 2. 统一响应包络 ----------

def test_ok_envelope_shape():
    body, status = ok({"a": 1})
    assert status == 200
    assert body["code"] == 0 and body["msg"] == "ok"
    assert body["data"] == {"a": 1}
    assert isinstance(body["trace_id"], str) and len(body["trace_id"]) == 8


def test_ok_keeps_null_valued_keys():
    """可空字段必须存在且为 null，不能省略键（安卓契约第 8 条）"""
    body, _ = ok({"barcode": None, "remark": None})
    assert "barcode" in body["data"] and body["data"]["barcode"] is None


def test_fail_envelope_carries_data_and_code():
    body, status = fail(1009, "库存不足", {"current": 3, "requested": 5, "lack": 2})
    assert body["code"] == 1009 and body["msg"] == "库存不足"
    assert body["data"]["lack"] == 2


# ---------- 3. 错误码 → HTTP 映射 ----------

@pytest.mark.parametrize("code,http", sorted(HTTP_BY_CODE.items()))
def test_every_business_code_maps_to_non_2xx_http(code, http):
    assert 400 <= http <= 599, f"code {code} 映射到 {http}，必须是错误状态码"


def test_fail_without_explicit_http_uses_mapping_table():
    body, status = fail(1003, "未登录")
    assert status == HTTP_BY_CODE[1003] == 401


def test_bizerror_defaults_from_table():
    e = BizError(1009, "库存不足")
    assert e.http_status == 422
