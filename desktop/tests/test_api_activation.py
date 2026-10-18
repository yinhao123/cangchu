"""离线激活接口 —— docs/11 §4.4 / docs/12

★ 最重要的契约：这两个接口在**未激活**时也必须能访问（免登录 + 免门禁）。
  否则用户卡在「登录不了、也激活不了」的死循环里，而现场只会看到「一直跳激活页」。
"""
import pytest

from license.service import LicenseState

MACHINE_ID = "A" * 32
DISPLAY_MACHINE_ID = "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-0000-1111"
PERMANENT_TS = 0
YEAR_2100_TS = 4102444800


def _state(activated: bool = False, message: str = "尚未激活", code: int = 1007,
           expire_ts: int = PERMANENT_TS) -> LicenseState:
    return LicenseState(activated=activated, message=message, code=code,
                        payload={"x": expire_ts} if activated else {},
                        machine_id=MACHINE_ID, display_machine_id=DISPLAY_MACHINE_ID)


@pytest.fixture
def cached_state(app):
    """把状态钉在「启动时缓存的那份」上：绝不能触到真实 refresh_state
    （含 20 万次 PBKDF2 + 采集本机硬件信息，慢且依赖开发机环境）。"""
    app.config["LICENSE_STATE"] = _state()
    return app


def test_state_is_public_and_exposes_machine_id(client, cached_state):
    r = client.get("/api/activation/state")
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["activated"] is False and d["message"] == "尚未激活"
    assert d["machine_id"] == MACHINE_ID and d["display_machine_id"] == DISPLAY_MACHINE_ID
    assert d["expire_text"] == "" and d["config_error"] == ""


def test_state_usable_while_gate_blocks_everything_else(client, cached_state):
    """门禁开着 + 未激活时：激活接口放行，业务接口 1013（这是它免拦截的全部意义）"""
    cached_state.config["LICENSE_ENFORCED"] = True

    assert client.get("/api/activation/state").status_code == 200
    blocked = client.get("/api/dashboard")
    assert blocked.status_code == 403 and blocked.get_json()["code"] == 1013


def test_activate_rejects_blank_code(client, cached_state):
    r = client.post("/api/activation", json={"code": "   "})
    assert r.status_code == 422 and r.get_json()["code"] == 1002


def test_activate_failure_returns_1013_with_reason(client, cached_state, monkeypatch):
    """验签失败必须是非 2xx：用 200 会让前端弹绿条，把失败显示成成功"""
    monkeypatch.setattr("license.service.activate",
                        lambda _code: _state(message="注册码校验失败，请联系卖家确认", code=1002))

    r = client.post("/api/activation", json={"code": "LG1.xxx.yyy"})
    assert r.status_code == 403
    body = r.get_json()
    assert body["code"] == 1013 and body["data"]["message"] == "注册码校验失败，请联系卖家确认"
    # 失败也要把最新状态写回缓存，否则页面显示的仍是激活前的旧状态
    assert cached_state.config["LICENSE_STATE"].message == "注册码校验失败，请联系卖家确认"


def test_activate_success_reports_permanent_license(client, cached_state, monkeypatch):
    monkeypatch.setattr("license.service.activate", lambda _code: _state(True, "激活成功", 0))

    r = client.post("/api/activation", json={"code": "LG1.xxx.yyy"})
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["activated"] is True and d["expire_text"] == "永久有效"
    assert cached_state.config["LICENSE_STATE"].activated is True


def test_activate_success_reports_expiry_date(client, cached_state, monkeypatch):
    monkeypatch.setattr("license.service.activate",
                        lambda _code: _state(True, "激活成功", 0, expire_ts=YEAR_2100_TS))

    d = client.post("/api/activation", json={"code": "LG1.xxx.yyy"}).get_json()["data"]
    assert d["expire_ts"] == YEAR_2100_TS and d["expire_text"].startswith("有效期至 2100-")
