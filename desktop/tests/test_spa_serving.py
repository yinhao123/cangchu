"""SPA 静态服务与前端路由回退 —— 陷阱 T-35 / T-36

旧的服务端渲染页面（28 个模板）已全部下线，浏览器界面只有一个入口：`static/spa/index.html`。
这里守的是三条「一破就是现场事故」的契约：
  1. 回退**不能**吃掉 `/api/*` 与 `/healthz` —— 吃掉后手机端拿到 HTML，直接解析失败（T-35）；
  2. 构建产物缺失时必须给**人话提示**，而不是 500 或白屏（T-36）；
  3. 未激活时静态资源仍必须加载得出来，否则激活页自己渲染不出来（白屏比报错更难查）。
"""
from __future__ import annotations

import glob
import json
import os

import views.spa as spa_module
from core.paths import spa_dir


def _assets() -> list[str]:
    return [os.path.basename(p) for p in glob.glob(os.path.join(spa_dir(), "assets", "*"))]


def test_root_serves_spa_shell(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    assert 'id="root"' in r.get_data(as_text=True)


def test_entry_is_never_cached(client):
    """index.html 必须 no-store：否则发了新版而用户仍跑旧壳，报错信息对不上"""
    assert "no-store" in client.get("/").headers.get("Cache-Control", "")


def test_frontend_routes_fall_back_to_shell(client):
    """前端路由（/login、/stock/logs 等）由 React 接管，服务端只负责回 index.html"""
    for path in ("/login", "/change-password", "/activate", "/products", "/stock/logs",
                 "/settings"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert 'id="root"' in r.get_data(as_text=True), path


def test_hashed_assets_are_served_and_immutable(client):
    assets = _assets()
    assert assets, "static/spa/assets 为空：前端构建产物没有提交，打包后会白屏（T-36）"
    r = client.get(f"/assets/{assets[0]}")
    assert r.status_code == 200
    assert "immutable" in r.headers.get("Cache-Control", "")


def test_traversal_outside_spa_root_is_not_served(client):
    """`..` 不能把 static/spa 之外的文件读出来。

    ★允许回落 index.html（那只是前端路由），但绝不允许吐出源码 —— 前者无害，后者是任意文件读取。
    """
    for path in ("/../../core/paths.py", "/..%2f..%2fapp.py", "/%2e%2e/%2e%2e/desktop/app.py"):
        body = client.get(path).get_data(as_text=True)
        assert "def resource_path" not in body and "create_app" not in body, path


def test_api_404_stays_json_not_html(client, admin_token):
    """★T-35：SPA 回退绝不能把 /api/* 的 JSON 404 变成 HTML（手机端会直接解析失败）"""
    r = client.get("/api/no-such-endpoint", headers={"X-Auth-Token": admin_token})
    assert r.status_code == 404
    assert r.mimetype == "application/json"
    body = r.get_json()
    assert body is not None and body["code"] == 1006, json.dumps(body, ensure_ascii=False)


def test_healthz_is_not_swallowed_by_spa(client):
    """健康检查是给监控/运维探活用的纯文本响应，绝不能被 SPA 兜底换成一张 HTML 页面"""
    r = client.get("/healthz")
    assert r.status_code == 200 and "html" not in r.mimetype
    assert 'id="root"' not in r.get_data(as_text=True)


def test_unauthenticated_api_is_401_not_spa(client):
    r = client.get("/api/no-such-endpoint")
    assert r.status_code == 401 and r.mimetype == "application/json"


def test_missing_build_gives_actionable_page(client, monkeypatch, tmp_path):
    """T-36：产物缺失要能一眼看出「怎么修」，而不是白屏"""
    monkeypatch.setattr(spa_module, "spa_dir", lambda: str(tmp_path / "empty"))
    r = client.get("/")
    assert r.status_code == 503
    text = r.get_data(as_text=True)
    assert "前端资源缺失" in text and "npm run build" in text


def test_shell_and_assets_are_reachable_before_activation(client, app):
    """未激活时：页面被 302 到 /activate，而激活页与它的静态资源必须能加载

    ★只放行 `/activate` 而忘了 `/assets/`，激活页会白屏 —— 这比一句报错更难排查。
    """
    from license.service import LicenseState

    assets = _assets()
    assert assets, "缺少构建产物"
    app.config["LICENSE_ENFORCED"] = True
    app.config["LICENSE_STATE"] = LicenseState(activated=False, message="尚未激活", code=1007)
    try:
        blocked = client.get("/")
        assert blocked.status_code == 302 and blocked.headers["Location"].endswith("/activate")
        assert client.get("/activate").status_code == 200
        assert client.get(f"/assets/{assets[0]}").status_code == 200
    finally:
        app.config["LICENSE_ENFORCED"] = False
