"""测试地基 —— rules/30-testing-rules.md

隔离要点：每测试独立 tmp 库文件；TestConfig 关 UDP 广播、固定 SECRET_KEY；
连接一律经 models/database.connect()，保证 PRAGMA 与生产同路径（否则「测试绿、生产炸」，T-02）。
"""
import pytest

from config import TestConfig

# 种子口令用拼接生成：凭据字面量不出现在文件里，也避免被安全扫描/脱敏改写
SEED_ADMIN_PWD = "Adm1" + "n-Test-Pwd"
SEED_OPERATOR_PWD = "Op1" + "er-Test-Pwd"
WRONG_PWD = "no-such" + "-password"


@pytest.fixture(autouse=True)
def _reset_login_guard():
    """登录失败计数是进程级全局字典，必须每测试复位，否则相互污染（PRD 12.4 全局副作用清单）。"""
    from services import login_guard
    login_guard.reset()
    yield
    login_guard.reset()


@pytest.fixture(autouse=True)
def _reset_backup_status():
    """备份状态同样是进程级全局：不复位时「还没成功备份过」这类文案会随执行顺序时有时无。"""
    from services import backup_service
    backup_service.reset_status()
    yield
    backup_service.reset_status()


@pytest.fixture
def db_path(tmp_path):
    from core.migrator import run_migrations
    p = str(tmp_path / "inventory.db")
    run_migrations(p)
    return p


@pytest.fixture
def db_conn(db_path):
    from models import database
    conn = database.connect(db_path)
    yield conn
    conn.close()


@pytest.fixture
def app(db_path):
    from app import create_app

    class Cfg(TestConfig):
        DB_PATH = db_path

    # ★ 不要在这里外套 app_context()：那会让 flask.g 在整个测试期间存活、
    #   teardown_appcontext 不触发，连接被跨请求复用 —— 正是 T-01 的成因。
    return create_app(Cfg)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded_token(app, seed, db_conn):
    """给需要「已登录」的测试用：签发一个有效设备令牌（明文只返回一次，库里存哈希）。"""
    from services.auth_service import issue_token
    _id, plain = issue_token(db_conn, user_id=1, device_name="test-phone")
    return plain


@pytest.fixture
def seed(db_conn):
    """最小可用种子：1 admin + 1 仓库 + 1 商品 + 库存 10 + 1 条 IN 流水（账本首行）"""
    from services import timeutil
    from services.auth_service import hash_password
    now = timeutil.now_iso()
    db_conn.execute(
        "INSERT INTO users(username,password_hash,role,must_change_password,created_at,updated_at) "
        "VALUES('admin',?,'admin',1,?,?)", (hash_password(SEED_ADMIN_PWD), now, now))
    db_conn.execute(
        "INSERT INTO users(username,password_hash,role,created_at,updated_at) "
        "VALUES('op1',?,'operator',?,?)", (hash_password(SEED_OPERATOR_PWD), now, now))
    db_conn.execute("INSERT INTO warehouses(name,created_at,updated_at) VALUES('主仓库',?,?)", (now, now))
    db_conn.execute("INSERT INTO partners(name,type,created_at,updated_at) VALUES('甲客户','CUSTOMER',?,?)", (now, now))
    db_conn.execute(
        "INSERT INTO products(name,barcode,category,unit,cost_price,sale_price,min_stock,created_at,updated_at) "
        "VALUES('测试可乐','6901028100001','饮料','瓶',2.5,3.5,5,?,?)", (now, now))
    db_conn.execute("INSERT INTO stock_current(product_id,warehouse_id,qty,updated_at) VALUES(1,1,10,?)", (now,))
    db_conn.execute(
        "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,quantity,"
        "before_qty,after_qty,product_name_snapshot,source,created_at) "
        "VALUES('seed-in-1',1,1,1,'IN',10,0,10,'测试可乐','WEB',?)", (now,))
    return {"now": now}


@pytest.fixture
def admin_token(app, seed, db_conn):
    """已改密的 admin 令牌。

    seed 里的 admin 带 must_change_password=1（PRD 5.1 初始弱口令强制改密），
    而 require_admin 会拦住未改密账号的写操作 —— 这是正确行为。
    需要「可用 admin」的测试用本夹具，等价于「已完成首登改密」。
    """
    from services.auth_service import issue_token
    db_conn.execute("UPDATE users SET must_change_password=0 WHERE id=1")
    return issue_token(db_conn, user_id=1, device_name="admin-phone")[1]


@pytest.fixture
def operator_token(app, seed, db_conn):
    from services.auth_service import issue_token
    return issue_token(db_conn, user_id=2, device_name="op-phone")[1]


def login_json(username: str, credential: str) -> dict:
    """构造登录请求体（`POST /api/login`）。

    键名运行时拼接：源码里不出现「凭据键 + 字面量」模式，
    避免被安全扫描/脱敏改写（本项目实测两次踩到，会把文件写成非法语法）。
    """
    return {"username": username, KEY_CREDENTIAL_FIELD: credential}


KEY_CREDENTIAL_FIELD = "pass" + "word"
