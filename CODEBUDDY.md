# ScanableSelf — 局域网扫码出入库系统（WMS MVP）

手机当扫码枪、电脑当账本：**Win7 服务端（无窗口 exe）+ 浏览器客户端（SPA）**，安卓端 Kotlin/Compose，UDP 自动发现 + 扫码配对，同一局域网内运行。

**当前阶段：只开发电脑端（`desktop/`）。安卓端 `android/` 仅保留骨架，未经我明确要求不得生成安卓代码。**

## 真相源（必读顺序）

- 所有开发规范见 @docs/01-产品需求文档.md（第 0 章「规模适配声明」、2.1/2.2 技术栈与理由、3.3 认证分派、7.1 页面清单、8.3 接口清单、12 章规约、13 章测试必须读）
- 数据库唯一权威 DDL：@docs/02-数据模型.md ，必须与 `desktop/db/schema.sql` 逐字一致
- 双端接口契约（含 UDP 协议、错误码语义边界）：@docs/03-API接口规范.md
- 编码规范与已知陷阱 T-01~T-38：@docs/04-开发规约与陷阱.md
- 交付形态与决策记录：@docs/09-扫雷审查与决策记录.md（第二轮 S1~S5 / A1~A5 / U1~U5）
- Win7 运行时线与真机验收清单：@docs/12-Win7服务端部署与Python3.8验收.md
- 上线前功能补齐清单（P0 导入/盘点/打印 → P1 销货/成本/权限审计，含 DDL 与接口草案）：@docs/14-上线前功能补齐方案.md
- 通用型产品化改造（淘宝销售：开箱可用/承诺兑现/商品档案/试用与诊断，B0~B3 批次）：@docs/15-通用型产品化改造方案.md
- 分阶段开发指令：`.codebuddy/commands/`（用 `/day1-start` 等斜杠指令）

## 技术栈（版本已锁定，不得更换）

**服务端（Win7 SP1 运行线）**：Python **3.8.10** · Flask 3.0.3 · waitress **2.1.2** · 原生 `sqlite3`（WAL）· qrcode[pil] 7.4.2 · python-barcode 0.15.1 · Pillow **9.5.0** · cryptography **41.0.7** · PyInstaller **5.13.2**（onedir，产物**没有** `_internal` 子目录） · pytest 8.3.2（dev）。

**浏览器客户端（仅构建机需要 Node）**：Vite 5 · React 18.3.1 · TypeScript 5 · Ant Design 5 · react-router-dom 6 · dayjs。产物构建到 `desktop/static/spa/` 并**随包提交**，运行与打包都不需要 Node。

**刻意不用**：SQLAlchemy/任何 ORM、Pydantic、Redis、Docker、Celery、APScheduler、axios、Tailwind、状态管理库。**刻意移除**：pywebview / pythonnet（Win7 无 WebView2）、Flask-Login（改用 `X-Auth-Token`）。理由见 PRD 2.2 与 docs/09，不要"顺手"引入。

## 关键命令

```bash
cd desktop && python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python manage.py initdb            # 建库 + 种子 admin/admin123
python manage.py runserver         # http://localhost:5000
pytest -q                          # 提交前门禁
python manage.py recalc-stock      # 库存与流水一致性自检

cd desktop/web && npm ci && npm run build   # 改过前端才需要：产物写入 ../static/spa，必须一起提交
```

## 分层结构

`api/`（参数解析+校验+调 service+统一响应包络）→ `services/`（业务规则+事务边界）→ `models/`（参数化 SQL+行转 dict）。
`views/` 现在**只负责 SPA 静态服务与前端路由回退**（`views/spa.py`），`templates/` 只剩 `errors/`；
浏览器界面在 `web/src/`（页面在 `pages/`，外壳在 `layout/`，接口出口在 `api/client.ts`）。UDP 广播在 `net/`。
禁止跨层：api 不写 SQL，models 不判业务，前端不自己算业务口径（一律以服务端返回值展示）。

## 硬性禁止事项

1. 不得引入 PRD 2.1 未列出的任何第三方依赖；版本一律 `==` 锁定，禁止 `>=`。
2. 不得直接改表结构：改表 = 新增 `db/migrations/NNNN_*.sql` + 同步 `schema.sql` + 递增 `schema_meta.version`，禁止修改历史迁移文件。
3. 不得改 `.env.example` 里的变量名；新增配置须同时更新 PRD 第 20 章。
4. 不得跳过权限校验（`require_login` / `require_admin`）与字段校验（`services/validators.py`）。
5. 不得硬编码密码、`SECRET_KEY`、Token；日志与异常输出不得出现 `password`/`token`/`SECRET_KEY` 明文（有过滤器兜底，但不得依赖它）。
6. **不得在 SQL 中拼接值**：值用 `?` 参数化；表名/列名/排序字段只能来自服务端白名单常量（T-05）。
7. 不得用 `SELECT *`；不得用 `INSERT OR REPLACE` 写 `stock_current`（T-07）。
8. 时间必须走 `services/timeutil.py:now_iso()`（东八区 ISO-8601），禁止 `datetime.now()` 裸调用、`utcnow`、SQLite `CURRENT_TIMESTAMP`（T-11）。
9. 未通过对应测试的功能不得声称"已完成"。不得为让测试通过而修改测试断言。
10. **不得让 SPA 回退吃掉 `/api`**：任何兜底路由都必须排除 `/api`、`/healthz`、`/static`（T-35）。
11. **改了前端必须 `npm run build` 后一起提交**：`datas` 里漏掉 `static/spa` 就是白屏（T-36）。
12. 不得用 `1003` 表达「某个字段里的口令打错了」——那个码会让客户端清 Token 踢人下线；口令类错误一律 `1002/422 + data.errors`（docs/03 1.3）。
13. 不得在 Win7 线上使用新的 Python 语法特性（PEP 585/604 注解必须配 `from __future__ import annotations`，否则 3.8 导入期即崩，T-38）。

## 开发红线（顺序不可颠倒）

1. **D0 门禁**：写任何业务功能之前，先跑通 `create_app()` + `tests/test_smoke.py` + PyInstaller 空包双击后**浏览器能打开界面**（纯服务端形态不再有窗口，见 PRD 14.2）。不满足就继续解决它。
2. 全局类型统一：时间=TEXT/ISO-8601/+08:00，主键=INTEGER AUTOINCREMENT，布尔=INTEGER 0/1，枚举=大写（角色 `admin`/`operator` 小写例外）。禁止混用。
3. 每请求一个 SQLite 连接，`teardown_appcontext` 关闭；连接后必须设 `foreign_keys=ON`、`busy_timeout=5000`、`synchronous=FULL`（T-01/02/03）。
4. **库存扣减只能写成条件更新 + `cursor.rowcount` 判定**，禁止"先 SELECT 判断再 UPDATE"（T-06）。测试数据清理按 FK 逆序，`tests/conftest.py` 的隔离 fixture 在写第一个测试之前设计好。
5. UDP 广播线程与 HTTP 服务同进程：广播内容里的端口必须是**实际绑定成功**的端口（T-13/14）。测试环境 `BROADCAST_ENABLED=false`，禁止测试真发广播包。
6. 备份只能用 `sqlite3` 的 `backup()` API，禁止复制 `.db` 文件（T-04）。
7. **浏览器端与手机端共用同一套认证**：`/api/*` 一律 `X-Auth-Token`（不再有 Session）。
   免鉴权白名单只有 5 个：`/api/ping`、`/api/login`、`/healthz`、`/api/activation/state`、`/api/activation`；
   `must_change_password=1` 时除 `/api/me`、`/api/me/password`、`/api/logout` 外全部 403/1004。
   未匹配路由仍须回 JSON 404（不得被改判成 403），见 T-35。
8. `views/` 只做静态服务与回退，**不得再新增服务端渲染页面**；新增界面一律做成 SPA 页面 + `/api/*` 接口。

## 功能完成的定义（DoD）

代码 + 对应 pytest 用例通过 + 异常路径已实现（PRD 各模块"异常处理"表）+ 无敏感信息日志 + 文档（若契约变化）同步更新 + `pytest -q` 全绿。

## 与我协作的方式

- 一次只做一个阶段（`/day1-base`、`/day1-biz`…），做完跑测试并汇报「改了哪些文件 + 测试结果 + 与 PRD 的偏差」，等我确认再进下一步。
- 契约有歧义时**先问，不要猜**；尤其涉及库存扣减、幂等、软删除语义。
- 不要输出"接下来我可以帮你做…"式建议清单，直接交付当前阶段结果。
