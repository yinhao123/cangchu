"""往来单位接口 —— docs/03 4.8。

回归背景（P7 交付现场）：前端在「出入库 → 盘点调整」模式下会把 `type` 传成 undefined，
而服务端把 type 当必填，于是页面一切到调整模式就弹「参数校验失败：类型 type 不能为空」。
契约里 `type` 是**可选筛选**，因此不传必须返回全部；非法值仍然 422。
"""
import pytest


def _create(client, token, name, partner_type):
    return client.post("/api/partners", json={"name": name, "type": partner_type},
                       headers={"X-Auth-Token": token})


def test_list_without_type_returns_all_kinds(client, operator_token):
    _create(client, operator_token, "无类型筛选客户", "CUSTOMER")
    _create(client, operator_token, "无类型筛选供应商", "SUPPLIER")

    resp = client.get("/api/partners", headers={"X-Auth-Token": operator_token})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 0
    kinds = {item["type"] for item in body["data"]["list"]}
    assert {"CUSTOMER", "SUPPLIER"} <= kinds          # seed 里本来就有一条 CUSTOMER
    assert body["data"]["type"] is None


def test_list_filters_by_type(client, operator_token):
    _create(client, operator_token, "类型筛选用供应商", "SUPPLIER")

    resp = client.get("/api/partners?type=SUPPLIER", headers={"X-Auth-Token": operator_token})

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["type"] == "SUPPLIER"
    assert [item["type"] for item in data["list"]] == ["SUPPLIER"]


def test_list_rejects_unknown_type(client, operator_token):
    resp = client.get("/api/partners?type=WHATEVER", headers={"X-Auth-Token": operator_token})

    assert resp.status_code == 422
    body = resp.get_json()
    assert body["code"] == 1002
    assert "type" in body["data"]["errors"]


def test_create_requires_type(client, operator_token):
    """创建时 type 必填：它是「出库对象 / 入库来源」的唯一依据，不能猜默认值。"""
    resp = client.post("/api/partners", json={"name": "缺类型的单位"},
                       headers={"X-Auth-Token": operator_token})

    assert resp.status_code == 422
    body = resp.get_json()
    assert body["code"] == 1002
    assert "type" in body["data"]["errors"]


def test_create_and_duplicate_conflict(client, operator_token):
    first = _create(client, operator_token, "重复登记客户", "CUSTOMER")
    assert first.status_code == 200 and first.get_json()["code"] == 0

    second = _create(client, operator_token, "重复登记客户", "CUSTOMER")
    assert second.status_code == 409
    assert second.get_json()["code"] == 1010
