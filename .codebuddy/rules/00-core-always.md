---
description: ScanableSelf 核心架构约束与禁止事项（每个会话必须遵守）
alwaysApply: true
enabled: true
---

# 核心约束（最高优先级）

你是 ScanableSelf（局域网扫码出入库系统）的开发助手。**当前只开发电脑端 `desktop/`**，不要生成 `android/` 代码，除非用户明确要求。

## 不可协商的架构决策

1. 技术栈固定：**Win7 运行线** = Python 3.8.10 + Flask 3.0.3 + waitress 2.1.2 + 原生 `sqlite3`（WAL）+ PyInstaller 5.13.2；**浏览器客户端** = Vite 5 + React 18 + antd 5（产物提交到 `desktop/static/spa/`）。
   已移除 pywebview / pythonnet / Flask-Login；不再有 Jinja 页面与 Bootstrap（`templates/` 只剩 `errors/`）。
2. **禁止引入 ORM**（用 `models/*_repo.py` + 参数化 SQL）、**禁止 Pydantic**（用 `services/validators.py`）、禁止 Redis / Celery / APScheduler / Docker / 前端框架。定时备份用守护线程实现。
3. 分层：`api/` → `services/` → `models/`。api 层不写 SQL，models 层不判业务规则，services 层不渲染模板。
4. 本项目是**局域网单机自用工具**，不是公网服务。不要"顺手"加 HTTPS、多租户、限流中间件、缓存层、消息队列 —— 这些在 PRD 第 0 章已明确 N/A。
5. 新增任何依赖前必须先改 PRD 2.1 版本表，版本一律 `==` 锁定。

## 禁止事项

- 禁止在 SQL 中拼接值。值用 `?`；表名/列名/排序字段只能来自服务端白名单常量映射。
- 禁止 `SELECT *`；禁止 `INSERT OR REPLACE` 写 `stock_current`。
- 禁止 `datetime.now()` 裸调用 / `utcnow` / SQLite `CURRENT_TIMESTAMP`；时间统一走 `services/timeutil.py:now_iso()`（东八区 ISO-8601 带 +08:00）。
- 禁止硬编码密码、SECRET_KEY、Token；禁止在日志/异常里输出 `password`、`token`、`SECRET_KEY` 明文。
- 禁止跳过 `require_login` / `require_admin` 权限校验与字段校验。
- 禁止直接改表结构：必须新增 `db/migrations/NNNN_*.sql` + 同步 `schema.sql` + 递增 `schema_meta.version`；禁止修改已应用的迁移文件。
- 禁止删除或物理覆盖 `stock_logs` 历史流水（纠错只能红字冲销或 ADJUST 调整）。
- 禁止用复制 `.db` 文件的方式做备份（必须 `sqlite3.Connection.backup()`）。
- 禁止为了让测试通过而修改测试断言。

## 工作方式

- 一次只做一个阶段，做完运行 `pytest -q`，汇报「改动文件清单 + 测试结果 + 与 PRD 的偏差」，等用户确认。
- 契约（字段语义、状态码、枚举值）不确定时**先问用户，不要猜**。
- 引用规范时给出文档章节号与陷阱编号（如 `04-开发规约与陷阱.md T-06`）。
- 代码注释、错误提示文案、日志说明一律用中文。
