"""D0 门禁测试（TDD RED）—— rules/30-testing-rules.md「先于一切业务代码」

必须证明地基可用：create_app 可建 → 9 张表齐全 → 外键在请求连接上生效 →
免鉴权探测接口可用且**返回实际端口**（APP 靠它，写死 5000 是错的）。
"""
import pytest


def test_create_app_and_migrations_produce_all_tables(db_conn):
    names = {r["name"] for r in db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    expect = {"schema_meta", "users", "api_tokens", "warehouses", "partners",
              "products", "stock_logs", "stock_current", "settings"}
    assert expect <= names, f"缺表：{expect - names}"


def test_app_factory_builds_and_exposes_config(app):
    assert app.config["TESTING"] is True
    assert app.config["BROADCAST_ENABLED"] is False, "测试环境禁止真发 UDP 广播包"
    from config import TEST_SIGNING_VALUE
    assert app.config["SECRET_KEY"] == TEST_SIGNING_VALUE, "测试必须用固定值，不能每次随机（T-18）"


def test_get_connection_is_reused_within_one_request(app):
    from flask import g
    from models.database import get_connection
    with app.test_request_context("/healthz"):
        c1, c2 = get_connection(), get_connection()
        assert c1 is c2, "同一请求内必须复用连接（T-01）"


def test_get_connection_differs_across_requests(app):
    """跨请求必须换新连接，否则 waitress 多线程共用同一连接（T-01）。
       注意：不能用 id() 判身份 —— 旧对象被回收后地址会被复用，判等结果不可靠。"""
    from models.database import get_connection
    conns = []
    with app.test_request_context("/healthz"):
        c1 = get_connection()
        conns.append(c1)
        assert c1.execute("SELECT 1").fetchone()[0] == 1, "请求内连接必须可用"
    with app.test_request_context("/healthz"):
        c2 = get_connection()
        conns.append(c2)
        assert c2 is not c1, "不同请求必须拿到不同连接（T-01）"
    # 请求结束后连接必须已释放：不关闭会泄漏文件描述符，WAL 下还会长期占着读事务
    for c in conns:
        try:
            c.execute("SELECT 1")
            raise AssertionError("请求结束后连接未关闭，teardown_appcontext 没生效")
        except __import__("sqlite3").ProgrammingError:
            pass


def test_foreign_keys_enforced_on_request_connection(app):
    from models.database import get_connection
    with app.test_request_context("/x"):
        conn = get_connection()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_healthz_returns_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_data(as_text=True).strip() == "ok"


def test_ping_returns_actual_bound_port_not_config_value(client, app):
    """★ 端口可能因被占用自动换成 5001~5010，广播与 ping 必须报实际值，
       否则 APP 会连到一个没人监听的端口（PRD 2.4 / 5.8）。"""
    r = client.get("/api/ping")
    assert r.status_code == 200
    body = r.get_json()
    assert body["code"] == 0
    d = body["data"]
    assert d["port"] == app.config["BOUND_PORT"] == 5107, "ping 必须报实际绑定端口，而非配置里的 5000"
    assert d["port"] != app.config["HTTP_PORT"], "测试里故意让两者不同，才能证明没写死配置值"
    assert d["protocol"] == "inventory_server" and d["protocol_version"] == "1.0"
    assert {"name", "version", "ip", "server_time", "db_ready", "broadcast_active"} <= set(d)
    assert d["broadcast_active"] is False, "测试环境广播关闭，状态必须如实反映"


def test_ping_is_public_and_carries_trace_id(client):
    r = client.get("/api/ping")
    assert "trace_id" in r.get_json()
    assert r.headers.get("X-Request-ID"), "响应头必须回写 trace_id 供用户报错时定位"


def test_unknown_api_path_without_token_is_401_json(client):
    r = client.get("/api/whatever-not-exists")
    assert r.status_code == 401 and r.is_json
    assert r.get_json()["code"] == 1003


def test_api_404_is_json_not_html(client, seeded_token):
    """APP 解析 HTML 会崩；未定义路径必须回 JSON 包络（docs/03 1.4）"""
    r = client.get("/api/whatever-not-exists", headers={"X-Auth-Token": seeded_token})
    assert r.status_code == 404, "带合法令牌访问未定义 API 必须 404，不能是 401 或 500"
    assert r.is_json
    assert r.get_json()["code"] == 1006


def test_unknown_web_path_falls_back_to_spa(client):
    """页面路径由前端路由接管：返回 SPA 外壳（200），而不是服务端 404（T-35）"""
    r = client.get("/no-such-page")
    assert r.status_code == 200
    assert "text/html" in r.content_type and 'id="root"' in r.get_data(as_text=True)


def test_spa_entry_and_login_route_share_the_same_shell(client):
    """SPA 只有一个入口：`/` 与前端路由（/login）回的是同一份 index.html"""
    entry = client.get("/")
    login = client.get("/login")
    assert entry.status_code == login.status_code == 200
    assert entry.get_data() == login.get_data()


def test_static_and_templates_resolve_through_resource_path(app):
    """★ PyInstaller 冻结后资源在 sys._MEIPASS，未走 resource_path 会 TemplateNotFound（T-16）"""
    import os

    from core.paths import resource_path
    # 模板只剩错误页；SPA 构建产物必须随包，否则界面白屏（T-36）
    assert os.path.isfile(resource_path(os.path.join("templates", "errors", "500.html")))
    assert os.path.isfile(resource_path(os.path.join("static", "spa", "index.html")))


def test_ping_has_no_db_query(client, app, monkeypatch):
    """ping 必须极快（<10ms）：它不做任何数据库查询，否则 APP 探测会被慢库拖住"""
    from models import database
    def boom(*a, **k):
        raise AssertionError("ping 不应触碰数据库连接")
    monkeypatch.setattr(database, "get_connection", boom)
    assert client.get("/api/ping").status_code == 200


def test_error_pages_do_not_leak_stack(client):
    r = client.get("/boom")  # 故意不存在；真实 500 场景由 test_error_handlers 覆盖
    assert "Traceback" not in r.get_data(as_text=True)
