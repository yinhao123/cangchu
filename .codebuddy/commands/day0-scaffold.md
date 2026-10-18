---
description: "D0 脚手架与门禁 —— 建库、create_app、smoke 测试、PyInstaller 空包双击出页面"
argument-hint: "[可选：补充说明，如 'Python 装在 C:\\py311']"
allowed-tools: Read, Write, Edit, Bash
---

你现在的角色是本项目的**脚手架工程师**。只做地基，**不做任何业务功能**。

## 先读（不可跳过）
1. `CODEBUDDY.md` 全文
2. `docs/01-产品需求文档.md` 第 0、2.1、2.4、12 章
3. `docs/02-数据模型.md` 全文
4. `docs/04-开发规约与陷阱.md` 的 T-01~T-05、T-11、T-16、T-17

## 任务清单（按顺序）

1. **依赖与配置**：确认 `desktop/requirements.txt` 已存在且版本锁定；创建 `desktop/.env.example` 与 `config` 读取逻辑（`core/config.py`：环境变量 > `config.ini` > 内置默认；`SECRET_KEY` 首次启动随机生成并持久化到配置文件）。
2. **路径与数据库基座**：
   - `core/paths.py`：`resource_path()`（兼容 `sys._MEIPASS`）与 `data_dir()`（Windows 用 `%LOCALAPPDATA%\ScanableSelf`，非 Windows 回退 `./data`），并做**目录可写自检**。
   - `models/database.py`：**每请求一连接**（`g.db` + `teardown_appcontext`），连接后立刻设 `foreign_keys=ON`、`busy_timeout=5000`、`synchronous=FULL`、`journal_mode=WAL`；`isolation_level=None`。**禁止**全局连接对象（T-01/02/03）。
3. **迁移器**：`core/migrator.py` 读 `schema_meta.version` → 顺序执行 `db/migrations/*.sql` 中编号更大者 → 单事务 + 失败整体回滚并终止启动。落 `db/migrations/0001_init.sql`（内容与 `docs/02-数据模型.md` 的 DDL 逐字一致）与 `db/schema.sql`。
4. **统一响应与异常**：`core/response.py`（`ok()/fail()`，包络 `{code,msg,data,trace_id}`）、`core/errors.py`（`BizError` + errorhandler）、`core/errors.json 码表` 对齐 `docs/03-API接口规范.md` 1.3。
5. **应用工厂 + 日志**：`app.py` 内 `create_app(config)`（**禁止**模块级 `app = Flask(__name__)`）；waitress 启动封装在 `core/server.py`，端口占用自动试 5001~5010 并记录**实际端口**；日志用 JSON 行式 + `RotatingFileHandler`，**必须实现按 key 黑名单的脱敏过滤器**（`password`/`token`/`secret`）。装 `sys.excepthook` 与 `threading.excepthook`：写 CRITICAL 日志 + 弹窗（含 trace_id）。
6. **测试地基**：`tests/conftest.py`（`app`/`client`/`db_path`/`seed` fixture，`BROADCAST_ENABLED=false`，独立临时库，FK 逆序清理）+ `tests/factories.py` + `tests/test_smoke.py`（建 app→建表→插种子→查一条→断言）。
7. **打包地基**：`ScanableSelf.spec`，`datas` 含 `templates`/`static`/`db/schema.sql`/`db/migrations`，`hiddenimports` 含 `waitress` 子模块与 `barcode`，`console=False`；另给一份带控制台用于调试。

## 完成门禁（三条全绿才算完成，不许提前宣布完成）

```
[1] pytest -q                                     → 全绿
[2] python manage.py initdb && runserver 后 curl /healthz → 返回 ok，且 /api/ping 返回正确 port
[3] pyinstaller ScanableSelf.spec → 双击 dist exe  → 能打开登录页（模板/静态不报错）
```

## 输出要求

改动的文件清单（一行一个 + 一句话职责）、三条门禁的实际执行结果（贴关键输出）、以及**任何与本指令或 PRD 不一致的地方**（不要擅自决定，列出来问我）。

补充说明：$ARGUMENTS
