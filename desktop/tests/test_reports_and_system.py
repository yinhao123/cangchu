"""报表与系统接口测试（TDD RED）—— docs/03 第 6、7 章 · PRD 5.6 / 5.9 / 15.3

覆盖：流水/库存/汇总查询、CSV 带 BOM、派生值由服务端算、设置白名单与重启提示、
      备份接口、配对二维码内容。
"""
import csv
import io
import json

import pytest


# ---------- 流水查询 ----------

def test_logs_query_defaults_to_today(client, admin_token, seed):
    r = client.get("/api/stock/logs", headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["total"] == 1 and d["list"][0]["client_msg_id"] == "seed-in-1"
    assert set(d) >= {"list", "page", "page_size", "total", "has_more"}


def test_logs_query_date_range_filters(client, admin_token, seed):
    r = client.get("/api/stock/logs?date_from=2020-01-01&date_to=2020-12-31",
                   headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200 and r.get_json()["data"]["total"] == 0


def test_logs_span_over_366_days_is_422(client, admin_token, seed):
    r = client.get("/api/stock/logs?date_from=2020-01-01&date_to=2026-01-01",
                   headers={"X-Auth-Token": admin_token})
    assert r.status_code == 422 and r.get_json()["code"] == 1002
    assert "366" in json.dumps(r.get_json(), ensure_ascii=False)


def test_logs_sort_param_is_whitelisted_not_injected(client, admin_token, seed):
    """T-05：恶意 sort 必须被忽略并回落默认排序，绝不能进 SQL"""
    evil = "quantity; DROP TABLE stock_logs; --"
    r = client.get(f"/api/stock/logs?sort={evil}&order=asc", headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200
    assert client.get("/api/stock/logs", headers={"X-Auth-Token": admin_token}).get_json()["data"]["total"] == 1, \
        "注入不得改动数据"


def test_logs_uses_name_snapshot(client, admin_token, db_conn, seed):
    """改名后历史流水仍显示当时的名字（快照字段的唯一用途）"""
    db_conn.execute("UPDATE products SET name='改名后的可乐' WHERE id=1")
    row = client.get("/api/stock/logs", headers={"X-Auth-Token": admin_token}).get_json()["data"]["list"][0]
    assert row["product_name"] == "测试可乐"


# ---------- 库存现状 ----------

def test_current_marks_low_stock_server_side(client, admin_token, seed):
    r = client.get("/api/stock/current", headers={"X-Auth-Token": admin_token})
    item = r.get_json()["data"]["list"][0]
    assert item["qty"] == 10 and item["min_stock"] == 5
    assert item["is_low_stock"] is False, "派生值必须由服务端给出，两端不得各算一套"


def test_current_low_only_returns_alerts(client, admin_token, db_conn, seed):
    db_conn.execute("UPDATE stock_current SET qty=2 WHERE product_id=1")
    r = client.get("/api/stock/current?low_only=true", headers={"X-Auth-Token": admin_token})
    d = r.get_json()["data"]
    assert d["total"] == 1 and d["list"][0]["is_low_stock"] is True


def test_current_negative_only_requires_admin(client, admin_token, operator_token, db_conn, seed):
    db_conn.execute("UPDATE stock_current SET qty=-1 WHERE product_id=1")
    ok = client.get("/api/stock/current?negative_only=true", headers={"X-Auth-Token": admin_token})
    assert ok.status_code == 200 and ok.get_json()["data"]["total"] == 1
    nope = client.get("/api/stock/current?negative_only=true", headers={"X-Auth-Token": operator_token})
    assert nope.status_code == 403


# ---------- 汇总与仪表盘 ----------

def test_summary_by_day(client, admin_token, seed):
    # ★date_to 必须由 seed 的时间推导：写死 2026-10-15 会在跨过零点当天把种进去的数据排除在区间外
    #   （rows 变空 → 每天零点后误报一次），且这类失败极易被误判成"业务回归"。
    today = seed["now"][:10]
    r = client.get(f"/api/stock/summary?dimension=DAY&date_from=2026-03-01&date_to={today}",
                   headers={"X-Auth-Token": admin_token})
    d = r.get_json()["data"]
    assert d["dimension"] == "DAY" and len(d["rows"]) == 1
    assert d["rows"][0]["in_qty"] == 10 and d["total"]["bill_count"] == 1


def test_summary_invalid_dimension_is_422(client, admin_token, seed):
    r = client.get("/api/stock/summary?dimension=WEEK", headers={"X-Auth-Token": admin_token})
    assert r.status_code == 422


def test_dashboard_shape(client, admin_token, seed):
    d = client.get("/api/dashboard", headers={"X-Auth-Token": admin_token}).get_json()["data"]
    assert d["product_count"] == 1 and d["total_qty"] == 10
    assert d["today"]["in_qty"] == 10 and d["today"]["in_count"] == 1
    assert "low_stock_list" in d and "recent_logs" in d and "server" in d
    assert d["stock_anomaly_count"] == 0


# ---------- CSV ----------

def test_csv_export_has_bom_and_headers(client, admin_token, seed):
    """T-10：不带 BOM 会让 Excel 打开中文乱码，这是自用用户天天踩的问题"""
    today = seed["now"][:10]   # 同上：写死日期会让导出在跨零点后变成"只有表头"
    r = client.get(f"/api/stock/logs/export?date_from=2026-03-01&date_to={today}",
                   headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200
    raw = r.get_data()
    assert raw[:3] == b"\xef\xbb\xbf", "CSV 必须以 UTF-8 BOM 开头"
    assert b"\r\n" in raw
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert "条码" in rows[0] and "类型" in rows[0]
    assert r.headers["Content-Disposition"].startswith("attachment")


# ---------- 设置 / 备份 / 二维码 ----------

def test_settings_get_marks_restart_needed(client, admin_token, seed):
    d = client.get("/api/settings", headers={"X-Auth-Token": admin_token}).get_json()["data"]
    keys = {x["key"]: x for x in d["items"]}
    assert keys["http_port"]["needs_restart"] is True
    assert keys["low_stock_alert"]["needs_restart"] is False


def test_settings_update_rejects_unknown_key(client, admin_token, seed):
    r = client.post("/api/settings", json={"items": {"secret_tunnel": "1"}},
                    headers={"X-Auth-Token": admin_token})
    assert r.status_code == 422


def test_settings_update_type_and_range_checked(client, admin_token, db_conn, seed):
    r = client.post("/api/settings", json={"items": {"backup_keep": "abc"}},
                    headers={"X-Auth-Token": admin_token})
    assert r.status_code == 422
    ok = client.post("/api/settings", json={"items": [{"key": "backup_keep", "value": 5},
                                                       {"key": "low_stock_alert", "value": False}]},
                     headers={"X-Auth-Token": admin_token})
    assert ok.status_code == 200
    body = ok.get_json()["data"]
    assert "backup_keep" in body["updated"]
    assert body["items"][0]["value"] == 5 or any(x["value"] == 5 for x in body["items"]), "读回应为 int"


def test_operator_cannot_read_settings(client, operator_token, seed):
    assert client.get("/api/settings", headers={"X-Auth-Token": operator_token}).status_code == 403


def test_backup_endpoint_creates_file(client, admin_token, app, seed, tmp_path, monkeypatch):
    monkeypatch.setenv("SCANABLE_DATA_DIR", str(tmp_path))
    r = client.post("/api/backup", json={}, headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["size_bytes"] > 0 and d["filename"].endswith(".db")
    listing = client.get("/api/backup/list", headers={"X-Auth-Token": admin_token}).get_json()["data"]
    assert d["filename"] in [x["filename"] for x in listing["list"]]


def test_backup_list_items_match_contract(client, admin_token, app, seed, tmp_path, monkeypatch):
    """列表项必须含 created_at / trigger（docs/03 7.4 的 BackupInfo）。

    缺 created_at 时前端按 `value.replace("T", " ")` 渲染会抛 undefined.replace，
    整个 /backup 页面白屏 —— 契约字段不许少给。
    """
    monkeypatch.setenv("SCANABLE_DATA_DIR", str(tmp_path))
    created = client.post("/api/backup", json={}, headers={"X-Auth-Token": admin_token}).get_json()["data"]
    listing = client.get("/api/backup/list", headers={"X-Auth-Token": admin_token}).get_json()["data"]
    row = next(x for x in listing["list"] if x["filename"] == created["filename"])
    for key in ("filename", "size_bytes", "created_at", "trigger"):
        assert key in row, f"列表项缺字段 {key}"
    assert row["trigger"] == "manual"
    assert row["size_bytes"] > 0
    assert row["created_at"].endswith("+08:00") and len(row["created_at"]) == 25


def test_backup_trigger_label_is_restored_from_filename(tmp_path):
    """`pre_restore` 在文件名里被压成 `prerestore`，出参要还原成文档里的枚举值。"""
    from services.backup_service import _trigger_of

    assert _trigger_of("inventory_20261018_030000.db") == "auto"
    assert _trigger_of("inventory_20261018_030000_manual.db") == "manual"
    assert _trigger_of("inventory_20261018_030000_prerestore.db") == "pre_restore"
    assert _trigger_of("inventory_20261018_030000_premigration.db") == "pre_migration"
    assert _trigger_of("inventory_20261018_030000_manual_2.db") == "manual"


def test_qrcode_payload_has_no_credentials(client, admin_token, app):
    """二维码内容只是连接信息，绝不能含账号/令牌（会被拍照外传）"""
    d = client.get("/api/qrcode", headers={"X-Auth-Token": admin_token}).get_json()["data"]
    payload = json.loads(d["payload_json"] if isinstance(d["payload_json"], str) else json.dumps(d["payload_json"]))
    assert set(payload) == {"protocol", "version", "host", "port", "name"}
    assert "token" not in json.dumps(payload).lower() and "password" not in json.dumps(payload).lower()
    assert d["image_png_base64"].startswith("iVBOR")  # PNG magic


# ---------- SPA 补齐：单条流水 / 操作员下拉 ----------

def test_log_detail_matches_list_columns(client, admin_token, seed):
    """冲销确认页要展示单条流水，字段名必须与列表完全一致（否则前端要写两套映射）"""
    detail = client.get("/api/stock/logs/1", headers={"X-Auth-Token": admin_token})
    assert detail.status_code == 200
    row = detail.get_json()["data"]
    listed = client.get("/api/stock/logs", headers={"X-Auth-Token": admin_token}).get_json()["data"]["list"][0]
    assert set(row) == set(listed)
    assert row["product_name"] == "测试可乐" and row["client_msg_id"] == "seed-in-1"


def test_log_detail_missing_is_404(client, admin_token, seed):
    r = client.get("/api/stock/logs/9999", headers={"X-Auth-Token": admin_token})
    assert r.status_code == 404 and r.get_json()["code"] == 1006


def test_operators_dropdown_never_leaks_hash(client, admin_token, seed):
    """T-19：下拉只取展示列，password_hash 绝不出库"""
    r = client.get("/api/stock/operators", headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200
    items = r.get_json()["data"]["list"]
    assert {x["username"] for x in items} == {"admin", "op1"}
    assert all(set(x) == {"id", "username", "display_name"} for x in items)


# ---------- SPA 补齐：系统信息 / 库存重算 ----------

def test_system_info_reports_bound_endpoint_and_hints(client, admin_token, app, seed):
    r = client.get("/api/system/info", headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200
    d = r.get_json()["data"]
    # TestConfig 故意把 BOUND_PORT 设成 5107（≠ HTTP_PORT 5000）：接口必须报**实际绑定端口**
    assert d["port"] == app.config["BOUND_PORT"] == 5107
    assert d["url"] == f"http://{d['ip']}:5107/"
    assert "5000-5010" in d["firewall_ports"] and d["firewall_commands"]
    assert d["concurrency_hint"] and d["urls"]
    assert {"total_mb", "free_mb", "used_percent"} <= set(d["disk"])
    assert d["backup"] is not None and d["autostart"] in (True, False)
    assert d["schema_version"] >= 1 and d["now"][:2] == "20"


def test_system_info_is_admin_only(client, operator_token, seed):
    assert client.get("/api/system/info",
                      headers={"X-Auth-Token": operator_token}).status_code == 403


def test_recalc_reports_zero_diff_on_consistent_ledger(client, admin_token, seed):
    """账面与流水一致时：预览 0 条，执行不改库也不产生备份"""
    preview = client.get("/api/backup/recalc", headers={"X-Auth-Token": admin_token})
    assert preview.status_code == 200 and preview.get_json()["data"]["count"] == 0
    applied = client.post("/api/backup/recalc", headers={"X-Auth-Token": admin_token})
    assert applied.status_code == 200
    assert applied.get_json()["data"] == {"changed": 0, "backup": None}


def test_recalc_detects_tampered_stock_current(client, admin_token, db_conn, seed):
    """派生表被人为改坏时必须能被检出（这是「库存对不上」的唯一仲裁路径）"""
    db_conn.execute("UPDATE stock_current SET qty = 7 WHERE product_id = 1 AND warehouse_id = 1")
    d = client.get("/api/backup/recalc", headers={"X-Auth-Token": admin_token}).get_json()["data"]
    assert d["count"] == 1
    assert d["list"][0]["recorded"] == 7 and d["list"][0]["computed"] == 10


# ---------- SPA 补齐：端口 / 自动发现要再输本人口令 ----------

def test_network_settings_require_own_password(client, admin_token, seed):
    """PRD 265：改端口/自动发现前必须再次确认是本人（否则 token 泄露即可改死手机连接）"""
    from tests.conftest import SEED_ADMIN_PWD, WRONG_PWD

    bad = client.post("/api/settings/network",
                      json={"items": {"http_port": 5001}, "password": WRONG_PWD},
                      headers={"X-Auth-Token": admin_token})
    assert bad.status_code == 422 and bad.get_json()["code"] == 1002
    assert "密码不正确" in json.dumps(bad.get_json(), ensure_ascii=False)

    good = client.post("/api/settings/network",
                       json={"items": {"http_port": 5001}, "password": SEED_ADMIN_PWD},
                       headers={"X-Auth-Token": admin_token})
    assert good.status_code == 200
    assert "http_port" in good.get_json()["data"]["needs_restart"]


def test_network_settings_reject_non_network_keys(client, admin_token, seed):
    """只有网络两项能走这个带口令的接口，否则它就成了绕过口令改任意配置的后门"""
    from tests.conftest import SEED_ADMIN_PWD

    r = client.post("/api/settings/network",
                    json={"items": {"backup_keep": 1}, "password": SEED_ADMIN_PWD},
                    headers={"X-Auth-Token": admin_token})
    assert r.status_code == 422 and "backup_keep" in json.dumps(r.get_json(), ensure_ascii=False)


def test_network_settings_operator_forbidden(client, operator_token, seed):
    r = client.post("/api/settings/network", json={"items": {"http_port": 5001}},
                    headers={"X-Auth-Token": operator_token})
    assert r.status_code == 403


def test_common_settings_cannot_smuggle_network_keys(client, admin_token, db_conn, seed):
    """PRD 3.5：改端口 / 关广播必须再输本人口令。

    通用设置口（/api/settings）若能顺手带上这两项，那道闸一条请求就能绕过 ——
    后果是全屋手机静默失联，要重启服务才发现，属最坏的一类「静默失败」。
    """
    from models import settings_repo

    for method in ("put", "post"):
        r = getattr(client, method)("/api/settings", headers={"X-Auth-Token": admin_token},
                                    json={"items": {"http_port": 5099, "server_name": "仓库电脑"}})
        assert r.status_code == 422, method
        assert "settings/network" in json.dumps(r.get_json(), ensure_ascii=False)
    assert settings_repo.get(db_conn, "http_port") == 5000, "被拒之后必须一点都没写进去"


def test_backup_restore_wrong_password_is_422_not_401(client, admin_token, seed):
    """口令打错必须是 422/1002：1003 会被前端当成「登录已失效」而清 Token 踢回登录页"""
    from tests.conftest import WRONG_PWD

    r = client.post("/api/backup/restore", headers={"X-Auth-Token": admin_token},
                    json={"filename": "inventory_20260101_000000.db", "password": WRONG_PWD})
    assert r.status_code == 422 and r.get_json()["code"] == 1002
