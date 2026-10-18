"""后台管理域的 **JSON 接口契约** —— PRD 5.1 / 5.7 / 5.8 / 5.9 / 5.10 · docs/03 第 7、9、10 章

背景：这组用例原本断言 Jinja 后台页面的 HTML（条码打印 / 手机配对 / 系统设置 / 账号管理 /
备份恢复）。界面已全量改为浏览器端 SPA，**断言对象随之改为 SPA 真正消费的接口契约** ——
否则「页面还在、字段已经漂了」这类问题会一路漂到用户那里才被发现。

★这里只保留**别处没有覆盖**的契约点；同一批能力的其余断言已按模块归位：
  - 设置读写与网络二次确认 → `test_reports_and_system.py`
  - 账号 / 设备 / 强制改密 → `test_api_users_devices.py`、`test_api_token_auth.py`
  - 备份创建与列表、库存重算、二维码 → `test_reports_and_system.py`
  - SPA 静态回落与未激活门禁 → `test_spa_serving.py`
"""
import json
import os

import pytest

from tests.conftest import SEED_ADMIN_PWD, WRONG_PWD

NEW_PWD = "Zx9" + "-New-Secret-Pwd"


def _auth(token: str) -> dict:
    return {"X-Auth-Token": token}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """数据目录必须隔离：默认落在 %LOCALAPPDATA%，测试会污染真实备份目录。"""
    monkeypatch.setenv("SCANABLE_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- 条码与标签（PRD 5.7）

def test_labels_list_carries_barcode_text_and_label_meta(client, seed, admin_token):
    d = client.get("/api/labels", headers=_auth(admin_token)).get_json()["data"]
    item = d["list"][0]
    assert item["barcode_text"] == "6901028100001", "有码商品直接用条码"
    assert {"label_size", "label_w", "label_h", "max_copies"} <= set(d)
    assert d["label_w"] > 0 and d["max_copies"] >= 1


def test_labels_falls_back_to_internal_code_without_barcode(client, seed, admin_token):
    """无条码商品必须给内部码：否则标签上是一张空白条码，扫码枪什么也扫不出来（PRD 5.1）"""
    from net import barcode_gen
    created = client.post("/api/products", json={"name": "散装花生", "barcode": None},
                          headers=_auth(admin_token)).get_json()["data"]
    d = client.get("/api/labels", headers=_auth(admin_token)).get_json()["data"]
    item = next(row for row in d["list"] if row["id"] == created["id"])
    assert item["barcode_text"] == barcode_gen.code_for({"id": created["id"], "barcode": None})


def test_labels_barcode_svg_is_inline_safe_image(client, seed, admin_token):
    r = client.get("/api/products/1/barcode.svg", headers=_auth(admin_token))
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"
    svg = r.get_data(as_text=True)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    allowed = set('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ<>/=" #.,%:-')
    assert not set(svg) - allowed, "SVG 必须过 PRD 5.1 白名单（前端才敢交给 <img> 渲染）"


def test_labels_barcode_unknown_product_is_json_404(client, seed, admin_token):
    """图接口失败也必须回 JSON 包络：SPA 会把错误当图片渲染成「碎图」（docs/03 1.3 豁免说明）"""
    r = client.get("/api/products/9999/barcode.svg", headers=_auth(admin_token))
    assert r.status_code == 404 and r.is_json and r.get_json()["code"] == 1006


def test_labels_and_barcode_require_login(client, seed):
    assert client.get("/api/labels").status_code == 401
    assert client.get("/api/products/1/barcode.svg").status_code == 401


# ---------------------------------------------------------------- 手机配对（PRD 5.8）

def test_qrcode_payload_contains_no_credentials(client, seed, admin_token):
    """二维码内容只有连接信息：被拍照/截图也拿不到任何凭据（PRD 5.8）"""
    from net import qrcode_gen
    payload = qrcode_gen.build_payload("192.168.1.10", 5000, "仓库电脑")
    assert "192.168.1.10" in payload and "5000" in payload
    assert "password" not in payload.lower() and "token" not in payload.lower()


def test_qrcode_response_never_echoes_passwords(client, seed, admin_token):
    body = client.get("/api/qrcode", headers=_auth(admin_token)).get_data(as_text=True)
    assert SEED_ADMIN_PWD not in body


# ---------------------------------------------------------------- 系统设置（PRD 5.9）

def test_settings_items_carry_group_label_and_hint(client, seed, admin_token):
    """分组 / 中文标签 / 提示由服务端给（settings_service.SETTING_META）：
    SPA 直接照渲染，前后端各写一套标签就会出现「界面写着 A、实际改的是 B」。"""
    d = client.get("/api/settings", headers=_auth(admin_token)).get_json()["data"]
    groups = {group["id"] for group in d["groups"]}
    assert {"device", "ops", "data", "network"} <= groups
    for item in d["items"]:
        assert item["group"] in groups, f"{item['key']} 的分组不在 groups 里"
        assert item["label"], f"{item['key']} 缺中文标签"
        assert "hint" in item and "value_type" in item


def test_settings_bool_item_is_real_boolean(client, seed, admin_token, db_conn):
    """布尔必须是真布尔：SPA 用 antd Switch 直接绑定，字符串 "0" 会被判成 true（PRD 类型规约）"""
    from models import settings_repo
    settings_repo.set_many(db_conn, {"broadcast_enabled": False}, 1)
    d = client.get("/api/settings", headers=_auth(admin_token)).get_json()["data"]
    item = next(row for row in d["items"] if row["key"] == "broadcast_enabled")
    assert item["value"] is False and item["value_type"] == "bool"


# ---------------------------------------------------------------- 账号管理（PRD 5.10）

def test_users_list_never_exposes_password_hash(client, seed, admin_token):
    """口令哈希绝不出网（T-19 / PRD 10.4）：任何列表接口透传 user_repo 整行都会漏"""
    raw = client.get("/api/users", headers=_auth(admin_token)).get_data(as_text=True)
    assert "password_hash" not in raw and "pbkdf2" not in raw


def test_created_user_can_login_and_is_flagged_must_change(client, seed, admin_token):
    """新建账号必须真的能登录：不能只是往库里插一行"""
    created = client.post("/api/users", headers=_auth(admin_token), json={
        "username": "zhangsan", "display_name": "张三", "role": "operator",
        "password": NEW_PWD, "password_confirm": NEW_PWD, "must_change": True,
    })
    assert created.status_code == 200, created.get_data(as_text=True)

    login = client.post("/api/login", json={"username": "zhangsan", "password": NEW_PWD})
    assert login.status_code == 200
    assert login.get_json()["data"]["user"]["must_change_password"] == 1, "首登必须先去改密"


def test_reset_password_revokes_devices_and_new_password_works(client, seed, admin_token, db_conn):
    from services.auth_service import issue_token
    issue_token(db_conn, user_id=2, device_name="op-phone")

    r = client.post("/api/users/2/password", headers=_auth(admin_token), json={
        "password": SEED_ADMIN_PWD, "new_password": NEW_PWD,
        "new_password_confirm": NEW_PWD,
    })
    assert r.status_code == 200 and r.get_json()["data"]["revoked_tokens"] == 1
    assert db_conn.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id=2 AND revoked_at IS NULL"
                           ).fetchone()[0] == 0, "改密必须把该账号已配对的设备全部踢下线"
    assert client.post("/api/login", json={"username": "op1", "password": NEW_PWD}).status_code == 200


def test_deactivated_user_cannot_login_and_history_keeps_operator(client, seed, admin_token, db_conn):
    r = client.post("/api/users/2/active", headers=_auth(admin_token),
                    json={"password": SEED_ADMIN_PWD, "is_active": False})
    assert r.status_code == 200 and r.get_json()["data"]["revoked_tokens"] >= 0

    login = client.post("/api/login", json={"username": "op1", "password": "Op1" + "er-Test-Pwd"})
    assert login.status_code == 403 and login.get_json()["code"] == 1004
    # ★停用不是删除：历史流水必须留着经办人，否则审计链就断了
    assert db_conn.execute("SELECT operator_id FROM stock_logs WHERE id=1").fetchone()[0] == 1


# ---------------------------------------------------------------- 备份恢复（PRD 5.8）

def test_backup_restore_rejects_illegal_filename(client, seed, admin_token, data_dir):
    """恢复是不可逆覆盖：路径穿越与非法文件名必须在触库之前被拒（PRD 7.4 / T-04）"""
    for bad in ("../../inventory_20260101_030000.db", "/etc/passwd",
                "inventory_x/y.db", "other_20260101.db"):
        r = client.post("/api/backup/restore", headers=_auth(admin_token),
                        json={"filename": bad, "password": SEED_ADMIN_PWD})
        assert r.status_code == 422, bad
        # 错误文案在 data.errors 里且响应是 ensure_ascii 的：直接搜原文永远搜不到
        detail = json.dumps(r.get_json(), ensure_ascii=False)
        assert "文件名不合法" in detail or "必须指定备份文件" in detail
    assert not os.path.exists(os.path.join(str(data_dir), "backups", "other_20260101.db"))


def test_backup_restore_requires_password_before_touching_files(client, seed, admin_token, data_dir):
    r = client.post("/api/backup/restore", headers=_auth(admin_token),
                    json={"filename": "inventory_20260101_030000.db", "password": WRONG_PWD})
    assert r.status_code == 422
    assert "密码不正确" in json.dumps(r.get_json(), ensure_ascii=False)
    # 口令没过就绝不能产生 pre_restore 备份：那说明流程顺序反了
    backups = os.path.join(str(data_dir), "backups")
    assert not os.path.isdir(backups) or not any(
        name.startswith("inventory_") for name in os.listdir(backups))


# ---------------------------------------------------------------- 条码生成器（PRD 5.1 白名单）

def test_barcode_svg_stays_inside_prd_51_whitelist():
    from net import barcode_gen
    svg = barcode_gen.code128_svg("6901028100001", 40.0)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    allowed = set('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ<>/=" #.,%:-')
    extra = set(svg) - allowed
    assert not extra, f"内联 SVG 含非白名单字符：{sorted(extra)}"


def test_barcode_internal_code_and_label_size_fallback():
    from net import barcode_gen
    assert barcode_gen.code_for({"id": 7, "barcode": None}) == "S0000000007"
    assert barcode_gen.code_for({"id": 7, "barcode": "690123"}) == "690123"
    assert barcode_gen.label_size("50x30") == (50, 30)
    assert barcode_gen.label_size("乱填") == (40, 30), "非法标签尺寸必须回落默认而不是崩"


def test_barcode_too_narrow_flags_long_code_on_small_label():
    from net import barcode_gen
    assert barcode_gen.too_narrow("S0000000123", 20.0) is True
    assert barcode_gen.too_narrow("690123", 60.0) is False
