"""离线激活接口 —— docs/11-第三方软件接入指南.md §4.4 / docs/12

★ 这两个接口**必须免登录且免门禁**：未激活时全站都被 `core/license_gate.py` 拦到激活页，
  这里若还要 Token，用户就变成「登录不了、也激活不了」。所以路径要同时进
  `app.py:PUBLIC_API_PATHS` 与 `core/license_gate.py:FREE_PATHS`。
★ 状态优先读 `app.config["LICENSE_STATE"]`（进程启动、激活成功两个时点刷新）：
  一次 refresh 含 20 万次 PBKDF2（约 100~300ms），放请求里会把整站拖死。
★ 不依赖 Cookie，故 CSRF 不适用、也不需要 form_token；且注册码与机器码绑定，
  没有卖家私钥就无法伪造出能激活**本机**的码。
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from core.errors import BizError
from core.license_gate import NOT_ACTIVATED_MSG
from core.response import fail, ok
from license import service as license_service

bp = Blueprint("api_activation", __name__)

ACTIVATION_STATE_PATH = "/api/activation/state"
ACTIVATION_PATH = "/api/activation"


def _state() -> license_service.LicenseState:
    state = current_app.config.get("LICENSE_STATE")
    return state if state is not None else license_service.refresh_state()


def _payload(state: license_service.LicenseState) -> dict:
    """给 SPA 的授权状态快照。字段名与 `LicenseState` 一一对应，前端不做二次推导。"""
    return {
        "activated": bool(state.activated),
        "message": state.message,
        "license_code": int(state.code),
        "machine_id": state.machine_id,
        "display_machine_id": state.display_machine_id,
        "expire_text": state.expire_text,
        "expire_ts": int(state.expire_ts),
        "config_error": state.config_error,
    }


@bp.get(ACTIVATION_STATE_PATH)
def state():
    """激活状态（含机器码）。免登录、免门禁。"""
    return jsonify(ok(_payload(_state()))[0]), 200


@bp.post(ACTIVATION_PATH)
def activate():
    """提交注册码。

    失败返回 `403 {code:1013}`：与门禁同一个码，前端据此判定「仍未激活」并红字显示原因；
    用 200 会让 antd 的 message 把失败当成功（激活失败却弹绿条是最坏的一种误导）。
    """
    payload = request.get_json(silent=True) or {}
    code_text = str(payload.get("code") or "")
    if not code_text.strip():
        raise BizError(1002, "参数校验失败", {"errors": {"code": ["请先粘贴卖家给你的注册码"]}})

    state = license_service.activate(code_text)
    current_app.config["LICENSE_STATE"] = state
    data = _payload(state)
    if not state.activated:
        body, status = fail(1013, state.message or NOT_ACTIVATED_MSG, data)
        return jsonify(body), status
    return jsonify(ok(data)[0]), 200
