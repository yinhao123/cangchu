---
description: Flask / SQLite / 前端实现约定与已知陷阱对策
alwaysApply: true
enabled: true
---

# 实现约定

## SQLite 访问（`models/database.py` 是唯一出口）

- **每请求一个连接**：`g.db` 缓存，`teardown_appcontext` 关闭；**禁止**模块级全局连接对象（T-01）。
- 每次连接后立刻执行：`PRAGMA foreign_keys=ON`、`busy_timeout=5000`、`synchronous=FULL`、`journal_mode=WAL`（前两者不持久化，T-02）。
- `isolation_level=None`，事务用显式 `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`（避免 Python 隐式事务时机不可控）。
- `synchronous` 固定 `FULL`，禁止为性能改 `NORMAL`（T-03）。
- Repository 显式列字段（禁止 `SELECT *`，T-19），返回 `dict` / `list[dict]`。

## 类型统一规约（违反即返工）

| 维度 | 唯一规定 |
|---|---|
| 时间 | TEXT，`2026-10-15T17:30:12+08:00`（东八区），只由 `timeutil.now_iso()` 生成；「今日」用 `substr(created_at,1,10)=?` 比较（T-11） |
| 主键 | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| 布尔 | INTEGER 0/1（JSON 输出保持 0/1 整数，两端一致） |
| 枚举 | 大写：`IN`/`OUT`/`ADJUST`、`CUSTOMER`/`SUPPLIER`、`WEB`/`APP`/`PDA`、`PENDING`/`SYNCED`/`FAILED`；角色例外小写 `admin`/`operator` |
| 数量 | INTEGER；金额 REAL 且 `round(x,2)` 只在入库与展示两处做（T-21） |
| NULL | 数据库 NULL → JSON `null` → 前端 `—`；数值字段禁止 NULL（无值即 0）；禁止用空字符串表达「无」 |

## 响应与错误

统一用 `core/response.py:ok(data)` / `fail(code,msg,data)`，输出包络 `{code,msg,data,trace_id}`；**禁止**散落手写 `jsonify`。
错误码集合固定（1001/1002/1003/1004/1005/1006/1007/1008/1009/1010/1011/1012/1999），新增须先改 `03-API接口规范.md`。
`code != 0` 时 HTTP 状态码必须非 2xx。未匹配路由返回 JSON 404，不返回 HTML。
业务异常统一 `raise BizError(code, msg, data)`，由 `errorhandler` 转包络；禁止 `except: pass`、禁止吞异常返回 200。

## 认证与权限

`before_request` 分派（顺序固定）：
1. 授权门禁（`core/license_gate.py`）：未激活 → `/api/*` 回 `403 {code:1013}`，页面 302 到 `/activate`；
   免拦截 = `/activate`、`/healthz`、`/static/*`、`/assets/*`、`/api/activation[/state]`。
2. 免鉴权白名单（**只有这 5 个**）：`/api/ping`、`/api/login`、`/healthz`、`/api/activation/state`、`/api/activation`。
3. `/api/*` → 校验 `X-Auth-Token`（查 `api_tokens.token_hash`，SHA-256 比对，未吊销未过期）；
   未命中 → `401 {code:1003}`。
4. 其余是页面请求 → 交给 SPA 回退（`views/spa.py`）；**不再有 Session**，浏览器端与手机端同一套 Token。

装饰器只有 `require_login` / `require_admin` 两级（无细粒度）。
`must_change_password=1` 时除 `/api/me`、`/api/me/password`、`/api/logout` 外**全量** 403/1004；
但**未匹配到真实路由的请求仍回 404**（别被 SPA 兜底路由骗了，见 T-35）。
Token 存库为哈希，明文只在登录响应出现一次。

## 安全头与防护

`after_request` 统一加：`X-Content-Type-Options: nosniff`、`X-Frame-Options: SAMEORIGIN`、`Referrer-Policy: same-origin`、CSP `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'`。
- **不加 `script-src 'unsafe-eval'`**：前端经 Vite 构建（非 Babel standalone），不需要也不允许；
  antd 的 CSS-in-JS 运行期注入 `<style>`，已由 `style-src 'unsafe-inline'` 覆盖。
- 条码 SVG 的白名单校验在**服务端**（`net/barcode_gen.py`）；前端只当图片交给 `<img>`，不做 HTML 注入。
- 不用 Cookie 会话 ⇒ **不需要 `form_token`**，也没有 `?next=`（开放重定向问题随之消失）。
- 生产禁止暴露堆栈：500 返回通用错误页 + `trace_id`（错误页模板完全自包含，不依赖静态资源），堆栈只进日志。

## UDP 广播与配对（`net/`）

- 报文：`INVENTORY_SERVER v1.0 IP:<ip> PORT:<实际端口> NAME:<名称>`，UTF-8，≤512 字节，空格分隔 `KEY:VALUE`，`NAME` 必须放最后（可含空格），未知 KEY 忽略（T-12）。
- **端口必须是实际绑定成功的端口**（自动换端口 5001~5010 后要更新广播内容）；广播线程在 HTTP 绑定成功后才启动。
- 本机 IP 枚举：排除 `127.*`/`169.254.*`/虚拟网卡，按「默认路由 > 192.168 > 10 > 172.16-31」排序，设置页展示全部候选供手选（T-14）。
- 发送目标先试子网定向广播（如 `192.168.1.255`），失败回落 `255.255.255.255`；两者都失败 → 状态栏黄条提示改用扫码配对，HTTP 不受影响（T-13）。
- 广播失败**绝不能**导致进程退出或未捕获异常。

## 服务端交付与路径

- 资源（模板/静态/schema）路径必须过 `core/paths.py:resource_path()`，兼容 `sys._MEIPASS`；SPA 产物走 `core/paths.py:spa_dir()`。
- 可写数据（库/备份/日志/配置）一律 `%LOCALAPPDATA%\ScanableSelf\`，禁止写自身安装目录；启动做目录可写自检。
- 交付形态是**纯服务端**：双击即起 HTTP + 广播，**不弹窗口**；启动信息由 `core/service_mode.publish()` 落盘 + 记日志。
- 无控制台模式下 `sys.excepthook` + `threading.excepthook` 双钩子：写 CRITICAL 日志 + 弹窗提示（含 trace_id）。启动任何失败都必须有用户可见反馈（T-17）。
- CSV 导出必须 `utf-8-sig`（BOM）+ `\r\n`（T-10）。
- **PyInstaller 5.13.2（Win7 线）的 onedir 产物没有 `_internal` 子目录**，DLL 与 datas 直接落在 `dist/<名>/` 下；
  `datas` 必须含 `static`（内有 `static/spa` 构建产物）、`templates`、`db`、license 公钥（T-32 / T-36）。

## 前端（React + antd SPA）

界面在 `desktop/web/src/`，产物构建到 `desktop/static/spa/` 并**随包提交**（运行与打包都不装 Node）。
- 接口只有一个出口：`api/client.ts`（注入 `X-Auth-Token`、解包 `{code,msg,data,trace_id}`、
  `1003` 清 Token 跳登录、`1013` 跳激活、错误 Toast 带追踪码）。**禁止**在页面里散落 `fetch`。
- 业务口径一律用服务端返回值展示（`is_low_stock`、合计行等都别在前端重算）。
- 列表页统一：分页 20/50/100、空状态、骨架屏加载、失败可原地重试（`AsyncBoundary`）。
- 扫码台：条码框自动聚焦、回车即提交、成功后焦点回位并保留仓库与模式；失败保留现场便于改一个数字重交。
- 打印：`@media print` 只留标签网格（`.sd-print-only`），屏幕部分用 `.sd-screen-only` 隐藏。
- 深浅色：antd 用 `ConfigProvider` + `darkAlgorithm`；**自绘部分**（顶栏/状态栏/登录卡片）必须同步覆盖
  `[data-theme="dark"]` 的 CSS 变量，否则深色里会冒出白带。
- 改完前端必须 `npm run build`，否则测试与打包用的还是旧产物（T-36）。
