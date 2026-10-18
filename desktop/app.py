"""应用工厂与进程入口 —— PRD 2.4 / 12.2 · 陷阱 T-16 / T-17

★ 禁止模块级 `app = Flask(__name__)`：测试无法隔离、配置无法注入（CODEBUDDY.md 红线）。
★ 启动顺序固定：配置 → 建目录 → 建库+迁移 → 绑端口（可自动 +1）→ HTTP 起 → UDP 广播起。
  广播里的端口必须是**实际绑定成功**的端口（PRD 2.4/5.8）。
★ 无控制台（exe）模式下任何异常都要「写 CRITICAL 日志 + 弹窗」，禁止静默退出（T-17）。
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from flask import Flask, g, jsonify, render_template, request

from config import DevConfig, ProdConfig, TestConfig  # noqa: F401  (供测试/脚本引用)
from core import license_gate
from core.errors import BizError, HTTP_BY_CODE
from core.logging_setup import install_excepthooks, setup_logging
from core.paths import db_path, ensure_dirs, resource_path
from core.response import envelope, fail
from models import database

__version__ = "1.0.0"

# 免鉴权白名单（PRD 3.3）：除此之外 /api/* 要 X-Auth-Token，网页要 Session
# ★激活两个接口必须在此：未激活时全站（含登录页）都被门禁拦住，
#   若它们还要 Token，用户就「登录不了、也激活不了」（docs/12）
PUBLIC_API_PATHS = {"/api/ping", "/api/login", "/healthz",
                    "/api/activation/state", "/api/activation"}

# 强制改密期间仍可访问的接口（docs/03 3.3）：
# ★`/api/me` 必须放行 —— SPA 靠它判断当前账号状态，拦掉的话刷新页面后连「要走改密流程」都不知道。
# ★`/api/logout` 必须放行 —— 不给退出等于把人锁在这一屏里。
MUST_CHANGE_ALLOWED = {"/api/me", "/api/me/password", "/api/logout"}

#: SPA 的兜底端点（`views/spa.py`）：`/<path:subpath>` 会把**未匹配到的 /api/xxx** 也匹配上，
#: 因此「这条路径是否真的存在」不能只看 `request.url_rule`（它现在恒为真）。
#: 不排除它的话，写错路径的请求会先被改密门禁拦成 403，客户端据此去重新配对 —— 而问题只是路径写错。
SPA_FALLBACK_ENDPOINT = "spa.index"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'",
}


def create_app(config: Any | None = None) -> Flask:
    """组装应用。config 可以是配置类、实例或 None（按 APP_ENV 选）。"""
    app = Flask(
        __name__,
        template_folder=resource_path("templates"),  # T-16：冻结后指向 sys._MEIPASS
        static_folder=resource_path("static"),
    )
    if config is None:
        app.config.from_object(ProdConfig)
    elif isinstance(config, type):
        app.config.from_object(config)
    else:
        app.config.from_object(config)

    # ★SERVER_NAME 是 Flask 的保留键（=「绑定主机名」语义）：设成设备名后，真实请求的 Host
    #   与它不匹配，Werkzeug 会把 subdomain 判为 <invalid>，于是**所有路由 404**（测试客户端
    #   恰好把 Host 设成同名，所以单测发现不了）。本项目要的是广播/展示用名称，改名转存。
    #   注意：Flask 内部直接 config["SERVER_NAME"] 取键，所以只能置 None，不能 del。
    app.config["DISPLAY_NAME"] = app.config.pop("SERVER_NAME", "ScanableSelf")
    app.config["SERVER_NAME"] = None

    app.config.setdefault("APP_VERSION", __version__)
    app.config.setdefault("DB_PATH", db_path())
    app.config.setdefault("BOUND_PORT", int(app.config.get("HTTP_PORT", 5000)))
    app.config.setdefault("BOUND_IP", None)
    app.config.setdefault("DB_READY", False)
    app.config.setdefault("BROADCAST_ACTIVE", False)

    if not app.config.get("SECRET_KEY"):
        from core.config_runtime import resolve_secret_key  # 首启随机生成并持久化（T-18）
        app.config["SECRET_KEY"] = resolve_secret_key()

    database.init_app(app)
    setup_logging(app, level=app.config.get("LOG_LEVEL", "INFO"))
    _mark_db_ready(app)
    _load_license_state(app)
    _register_hooks(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    return app


def _mark_db_ready(app: Flask) -> None:
    """启动阶段一次性确认库可用（ping 靠这个标志，不做运行时查询）。"""
    try:
        conn = database.connect(app.config["DB_PATH"])
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        conn.close()
        app.config["DB_READY"] = True
    except Exception as exc:  # 库坏了要告警，但不能让探测接口跟着崩
        app.config["DB_READY"] = False
        logging.getLogger("scanable.startup").error("数据库不可用：%s", exc)


def _load_license_state(app: Flask) -> None:
    """启动时读授权文件并**重新验签**（docs/11 §4.4 ② 的挂载点）。

    这是唯一的验签时点（另一次是激活成功）：一次 refresh 含 20 万次 PBKDF2 与三次 wmic，
    放进请求会把整站拖死，所以结果缓存在 app.config["LICENSE_STATE"]，由 core/license_gate.py 读。

    刻意不 catch：`refresh_state()` 已把业务异常与配置错误都转成状态返回，
    它若抛异常说明授权模块自身坏了 —— 宁可启动失败，也不要静默放行或静默锁定。
    """
    from license import service as license_service

    state = license_service.refresh_state()
    app.config["LICENSE_STATE"] = state
    logging.getLogger("scanable.license").info(
        "授权状态：%s（机器码 %s）", state.message, state.display_machine_id or "-")


def _register_hooks(app: Flask) -> None:
    @app.before_request
    def _trace_and_auth():
        g.trace_id = (request.headers.get("X-Request-ID") or "").strip()[:16] or _new_trace()
        # 门禁必须在鉴权之前：未激活时不该为拦截请求去查 token（也不该让未激活的请求触达业务层）
        blocked = license_gate.blocked_response(app)
        if blocked is not None:
            return blocked
        if request.path in PUBLIC_API_PATHS or request.path.startswith("/static/"):
            return None
        if request.path.startswith("/api/"):
            return _authenticate_api(app)
        # 页面请求交给 SPA 回退（views/spa.py）：界面由前端路由渲染，
        # 「没登录 → 跳登录页」由前端守卫做（Token 存 sessionStorage，服务端不种会话）。
        return None

    @app.after_request
    def _finalize(resp):
        resp.headers["X-Request-ID"] = getattr(g, "trace_id", "-")
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp


def _authenticate_api(app: Flask):
    """令牌校验：命中则注入 g.user，否则返回 401 包络（PRD 3.3）。"""
    from services.api_auth import authenticate_token  # 延迟导入避免循环

    token = request.headers.get("X-Auth-Token", "")
    user = authenticate_token(token) if token else None
    if user is None:
        body, status = fail(1003, "登录已失效，请重新扫码配对")
        return jsonify(body), status
    g.user = user
    # ★强制改密是**全局**的（docs/03 3.3）：不能只靠 require_admin 顺带拦下——
    #   操作员的读接口没有那个装饰器，初始弱口令就能一直用下去。
    # ★没匹配到任何真实路由的请求必须留给 404 处理器（`url_rule is None`，或落到 SPA 兜底端点），
    #   不能让这里改成 403：APP 会把 403 当成「去重新配对」，而真正的问题只是路径写错。
    if user.get("must_change_password") and request.path not in MUST_CHANGE_ALLOWED \
            and request.url_rule is not None \
            and request.endpoint != SPA_FALLBACK_ENDPOINT:
        body, status = fail(1004, "请先修改初始密码")
        return jsonify(body), status
    return None


def _register_blueprints(app: Flask) -> None:
    from api import register_api_blueprints
    from views import register_web_blueprints

    register_api_blueprints(app)
    register_web_blueprints(app)


def _register_error_handlers(app: Flask) -> None:
    wants_json = lambda: request.path.startswith("/api/")  # noqa: E731

    @app.errorhandler(BizError)
    def _biz(err: BizError):
        # ★业务异常也要落日志：用户报的 8 位 trace_id 必须能在日志里查到，
        #   否则「参数校验失败（追踪码 xxxxxxxx）」等于什么都没说（docs/07 1.1）。
        #   只记字段名与原因，不记用户输入的原始值（避免把业务数据写进日志）。
        logging.getLogger("scanable.biz").warning(
            "业务异常 path=%s code=%s msg=%s", request.path, err.code, err.msg,
            extra={"trace_id": _trace(), "err_code": err.code, "action": "biz.error",
                   "extra_fields": {"errors": _error_fields(err.data),
                                    "body_keys": _body_keys()}})
        body, status = fail(err.code, err.msg, err.data)
        if wants_json():
            return jsonify(body), status
        # 追踪码必须一起给：非接口路径的错误也要能在服务端日志里对上号
        return render_template("errors/500.html", code=err.code, msg=err.msg,
                               trace_id=_trace()), status

    @app.errorhandler(404)
    def _not_found(_e):
        if wants_json():
            return jsonify(envelope(None, 1006, "资源不存在")), 404
        return render_template("errors/404.html", trace_id=_trace()), 404

    @app.errorhandler(403)
    def _forbidden(_e):
        if wants_json():
            return jsonify(envelope(None, 1004, "无权限")), 403
        return render_template("errors/403.html", trace_id=_trace()), 403

    @app.errorhandler(Exception)
    def _unexpected(e):
        """未捕获异常：堆栈只进日志，页面只给通用文案 + trace_id（PRD 10.1 错误信息脱敏）。"""
        logging.getLogger("scanable.error").exception("未捕获异常 trace_id=%s", getattr(g, "trace_id", "-"))
        if wants_json():
            return jsonify(envelope(None, 1999, "服务内部错误，请联系管理员并提供追踪码")), 500
        return render_template("errors/500.html", code=1999, msg="服务内部错误",
                               trace_id=_trace()), 500


def _error_fields(data) -> dict:
    """从 BizError.data 里取字段级错误（`{"errors": {"quantity": ["..."]}}`），供日志定位。

    只保留字段名与提示文案：这两样不含用户输入的具体值，可安全落盘。
    """
    if not isinstance(data, dict):
        return {}
    errors = data.get("errors")
    if not isinstance(errors, dict):
        return {}
    out = {}
    for key, value in list(errors.items())[:10]:
        if isinstance(value, (list, tuple)):
            out[str(key)] = [str(item)[:100] for item in list(value)[:5]]
        else:
            out[str(key)] = [str(value)[:100]]
    return out


def _body_keys() -> list:
    """请求体里出现了哪些字段**名**（不含值）。

    排查「XX 不能为空」时，第一个要回答的问题是「到底收到了哪些键」——
    只记键名既能定位问题，又不会把业务数据写进日志（日志脱敏红线）。
    """
    try:
        payload = request.get_json(silent=True)
    except Exception:
        return []
    if isinstance(payload, dict):
        return sorted(str(key) for key in payload.keys())[:30]
    return []


def _new_trace() -> str:
    import secrets
    return secrets.token_hex(4)


def _trace() -> str:
    """错误页要展示的追踪码：与响应头 `X-Request-ID` 同源，便于用户报障时对上日志。"""
    return getattr(g, "trace_id", "-")


def main() -> int:
    """进程入口：按 PRD 2.4 顺序启动。返回退出码。"""
    install_excepthooks()
    try:
        ensure_dirs()
        from core.migrator import run_migrations
        app = None
        target = db_path()
        run_migrations(target)
        app = create_app(ProdConfig)
        _seed_initial_if_needed(app)
        _reset_admin_if_requested(app)
        _seed_demo_if_requested(app)
        from core.server import serve, shutdown
        server, bound = serve(app)
        from net.udp_broadcaster import start_broadcaster
        broadcaster = start_broadcaster(app, bound)
        # ★纯服务端形态（docs/12）：不弹任何窗口（Win7 上也没有 WebView2 可承载），
        #   主线程直接阻塞在 waitress 上；访问地址、访问二维码与防火墙提示由
        #   service_mode 落盘 + 记日志，局域网内任意机器用浏览器访问。
        from core import service_mode
        service_mode.publish(bound, app.config.get("DISPLAY_NAME"))
        server.run()  # 阻塞直到 Ctrl+C / 进程终止
        shutdown(server, broadcaster)
        return 0
    except Exception as exc:
        logging.getLogger("scanable.startup").exception("启动失败")
        _fatal_feedback(exc)
        return 1


def _seed_initial_if_needed(app: Flask) -> None:
    """首启种子：双击 exe 的用户不会去命令行跑 initdb，因此启动路径必须自己保证有账号。

    ★ 幂等（users 表非空即跳过）：已有账号的库重启不会被重置口令。
    ★ 口令**不进日志**：无控制台形态下只记「已创建初始账号」，明文写进安装说明即可（日志脱敏红线）。
    """
    from models import database
    from services import bootstrap_service

    try:
        with app.app_context():
            created = bootstrap_service.seed_initial_data(database.get_connection())
    except Exception:
        logging.getLogger("scanable.startup").exception("首启种子失败（登录账号可能不存在）")
        return
    if not created:
        return
    logging.getLogger("scanable.startup").info(
        "已创建初始账号 %s（首次登录必须修改密码）", bootstrap_service.SEED_ADMIN_USER)
    print("已创建初始账号：%s / ******（首次登录必须修改，口令见安装说明）"
          % bootstrap_service.SEED_ADMIN_USER)


def _reset_admin_if_requested(app: Flask) -> None:
    """`--reset-admin`：交付后用户只剩 exe，忘记管理员口令时必须有一个本地恢复入口。

    ★ 只重置 admin 的口令并强制下次改密，不动任何业务数据。
    ★ 安全性：能运行这个 exe 的人本来就能读到数据目录里的库文件（同一 Windows 账户），
      所以它没有降低威胁模型；但它必须被写进使用说明，避免「谁知道都能重置」的误解。
    """
    if "--reset-admin" not in sys.argv[1:]:
        return
    from models import database, user_repo
    from services import bootstrap_service
    from services.auth_service import hash_password

    try:
        with app.app_context():
            conn = database.get_connection()
            user = user_repo.find_by_username(conn, "admin")
            if user is None:
                _info_dialog("数据库里没有 admin 账号。请确认程序使用的是正确的数据目录。")
                return
            password = bootstrap_service.seed_password()
            user_repo.set_password(conn, user["id"], hash_password(password), clear_must_change=False)
    except Exception:
        logging.getLogger("scanable.startup").exception("重置管理员口令失败")
        _info_dialog("重置管理员口令失败，请查看 logs 目录下的日志。")
        return
    logging.getLogger("scanable.startup").warning("已重置 admin 口令（强制下次改密）")
    _info_dialog("已重置管理员口令：%s\n\n下次登录会要求先修改密码。\n登录后请立刻改成你自己的口令。"
                 % password)


def _info_dialog(message: str) -> None:
    """无控制台形态下的提示框（T-17 的同一套降级：拿不到 GUI 就打到 stderr）。"""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "扫码出入库系统", 0x40)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def _seed_demo_if_requested(app: Flask) -> None:
    """`--demo` / `--demo-force`：起服务前灌演示数据（P7 交付用）。

    交付目录里的「启动演示.bat」把它指向独立数据目录（SCANABLE_DATA_DIR），
    因此客户演示与真实账本互不影响。灌数据失败不阻断启动：服务该起还得起，
    失败原因进日志（无控制台形态下用户看不到 stdout，但日志里有）。
    """
    argv = sys.argv[1:]
    if "--demo" not in argv and "--demo-force" not in argv:
        return
    from services.demo_seed_service import seed_demo_data

    try:
        with app.app_context():
            result = seed_demo_data(force="--demo-force" in argv)
        logging.getLogger("scanable.demo").info("演示数据：%s", result["message"])
        print(result["message"])
    except Exception:  # 演示数据是「锦上添花」，不能让它拦住登录页
        logging.getLogger("scanable.demo").exception("演示数据灌入失败")


def _fatal_feedback(exc: Exception) -> None:
    """无控制台模式下必须让用户看见失败原因（T-17）。"""
    msg = f"启动失败：{exc}\n日志位置：{db_path()}"
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.MessageBoxW(None, msg, "扫码出入库系统", 0x10)
            return
        except Exception:
            pass
    print(msg, file=sys.stderr)


# ★入口调用必须留在文件末尾：main() 会经 create_app() 用到本模块里的工厂与钩子，
#   一旦这段挪到那两个函数定义之前，`python app.py` 与打包后的 exe 都会在启动时报
#   NameError: name '_setup_web_login' is not defined（PyInstaller 正是以 __main__ 执行 app.py）。
#   开发态走 manage.py 导入模块（__name__ != "__main__"），所以这个坑只在打包后暴露。
if __name__ == "__main__":
    raise SystemExit(main())
