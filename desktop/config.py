"""配置对象 —— PRD 20 章

优先级：环境变量 > config.ini（exe）/ .env（dev）> 内置默认。
  - DevConfig：DEBUG=True、BROADCAST_ENABLED 可开、数据目录 ./data
  - ProdConfig：DEBUG=False、数据目录 %LOCALAPPDATA%\\ScanableSelf（禁止写安装目录，T-16）
  - TestConfig：每测试独立 tmp 库、BROADCAST_ENABLED=False、SECRET_KEY 固定值（30-testing-rules.md）
新增配置项必须同步 .env.example 与 PRD 20 章表格。
"""


TEST_SIGNING_VALUE = "scanable-self-testing-only-value"


class BaseConfig:
    DEBUG = False
    TESTING = False
    HTTP_PORT = 5000
    AUTO_SWITCH_PORT = True
    BROADCAST_ENABLED = True
    BROADCAST_INTERVAL_MS = 3000
    BROADCAST_PORT = 55555
    SERVER_NAME = "ScanableSelf"
    SYNC_BATCH_MAX = 200
    LOGIN_MAX_FAIL = 5
    LOGIN_LOCK_SECONDS = 900
    # 未激活时全站拦截（docs/11 §4.4 ② 的浏览器 UI 等价实现，见 core/license_gate.py）
    LICENSE_ENFORCED = True


class DevConfig(BaseConfig):
    DEBUG = True


class ProdConfig(BaseConfig):
    pass


class TestConfig(BaseConfig):
    TESTING = True
    BROADCAST_ENABLED = False
    SECRET_KEY = TEST_SIGNING_VALUE
    BOUND_PORT = 5107        # 故意 ≠ HTTP_PORT(5000)：验证 /api/ping 报的是实际绑定端口
    # 测试机没有真实激活码；门禁默认关（开着会让既有用例全部撞 403），
    # 需要验证门禁的用例在用例内自行置 app.config["LICENSE_ENFORCED"] = True
    LICENSE_ENFORCED = False
