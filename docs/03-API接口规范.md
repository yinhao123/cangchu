# 03 · API 接口规范

> 双端契约唯一真相源。电脑端实现与安卓端消费都以本文件为准。
> 变更字段须同步改本文件 + `01-产品需求文档.md` 8.3 清单 + `CODEBUDDY.md` 禁止事项第 4 条。

---

## 1. 通用约定

### 1.1 基础
- **Base**：`http://<局域网IP>:5000`（端口被占会 +1，APP 必须以 `/api/ping` 返回的 `port` 为准，禁止写死 5000）。
- **编码**：全部 UTF-8；`Content-Type: application/json; charset=utf-8`；网页表单除外。
- **字段命名**：`snake_case`（与数据库一致，安卓 Gson/Kotlinx 无需命名策略转换）。
- **时间**：ISO-8601 东八区带偏移，例 `2026-10-15T17:30:12+08:00`。
- **空值**：字段恒存在，无值返回 `null`，**禁止省略键**（安卓 data class 依赖字段存在）。

### 1.2 认证

| 端 | 方式 | 说明 |
|---|---|---|
| 浏览器（SPA） | Header `X-Auth-Token: <token>` | 由 `/api/login` 换取；Token 明文 48 位 hex，存**内存 + sessionStorage**（不用 Cookie → 不依赖浏览器自动携带，CSRF 天然规避，因此全站无 `form_token`） |
| 安卓 | Header `X-Auth-Token: <token>` | 同上，`client_type` 传 `android` |

> Flask-Login 会话已随 Jinja 页面一起下线：两套认证并存最容易「一边拦住了、另一边漏了」。
> 浏览器端刷新页面后用 `GET /api/me` 恢复身份（见 3.3）。

免鉴权白名单（**仅这 5 个**）：`GET /api/ping`、`POST /api/login`、`GET /healthz`、
`GET /api/activation/state`、`POST /api/activation`。

后两个必须免鉴权：未激活时全站被门禁拦下，若激活接口还要 Token，用户就「登录不了、也激活不了」。

**强制改密例外（`must_change_password=1`）**：除 `GET /api/me`、`POST /api/me/password`、
`POST /api/logout` 外，**所有** `/api/*` 返回 `403 {code:1004}`。
- `/api/me` 必须放行：前端靠它判断「要不要走改密流程」，拦掉的话刷新页面就不知道该怎么办；
- `/api/logout` 必须放行：不给退出等于把人锁死在这一屏；
- 未匹配到任何真实路由的请求仍返回 `404 {code:1006}`，**不得**改判成 403
  （客户端会把 403 当成「需要重新配对」，而真实原因只是路径写错）。

### 1.3 统一响应包络

```json
{ "code": 0, "msg": "ok", "data": { }, "trace_id": "b3f19a2c" }
```

分页 `data` 结构固定：

```json
{ "list": [], "page": 1, "page_size": 20, "total": 137, "has_more": true }
```

错误响应示例（字段校验）：

```json
{
  "code": 1002,
  "msg": "参数校验失败",
  "data": { "errors": { "quantity": ["数量必须是 1~100000 的整数"], "barcode": ["不能为空"] } },
  "trace_id": "b3f19a2c"
}
```

**字段级错误一律放在 `data.errors`**：`{"字段名": ["文案", ...]}`；前端表单就地标红。
响应体是 `ensure_ascii` 的 JSON，**按原文搜索中文字符串会搜不到**，测试与前端都必须先解析
JSON 再取 `msg` / `data.errors`。

#### 图片资源的显式豁免（重要）

`GET /api/products/<id>/barcode.svg|.png` **不套 JSON 包络**：成功时直接返回图片
（`image/svg+xml` / `image/png`），**失败时**才回 JSON 包络（如 `404 {code:1006}`）。
SPA 用 `fetch` 带上 `X-Auth-Token` 取回 blob 再交给 `<img>`（`<img src>` 带不了请求头，
把 Token 挂到 URL 上会被访问日志与浏览器历史留痕，禁止）。

错误码全集见 `01-产品需求文档.md` 10.3。**约定**：`code != 0` 时 HTTP 状态码必须非 2xx，两端都以 `code` 为业务判据、HTTP 状态码为传输层判据。

#### 1003 的语义边界（易错）

`1003 = 登录/Token 失效`（HTTP 401）。客户端收到 `1003` 会**清 Token 跳登录页**，
因此**禁止**用它表达「某个字段里的口令不对」——那会把用户无缘无故踢下线。
「操作人自己口令输错」一律用 `1002 / 422 + data.errors.password`
（已统一：`/api/me/password`、`/api/users/<id>/password`、`/api/settings/network`、`/api/backup/restore`）。

**离线授权（仅电脑端，实现见 `11-第三方软件接入指南.md`）**：软件未激活时全部 `/api/*` 返回 `403 {code:1013}`，`data` 为空对象。
页面（非 `/api/*`）在未激活时一律 302 到 `/activate`，由 SPA 的激活页渲染。
免拦截清单 = `FREE_PATHS`（`/activate`、`/healthz`、`/api/activation/state`、`/api/activation`）
+ `FREE_PREFIXES`（`/static/`、`/assets/`）。
**静态资源前缀必须放行**：拦掉的话激活页自己的脚本加载不出来 —— 白屏比报错更难排查。
安卓端收到 `1013` 应提示「请先在电脑端完成激活」，**不得**自动重试或清 Token。

### 1.4 通用规则
- 写接口（POST/PUT/DELETE）必须校验 `Content-Length` ≤ 256 KB，超出 400。
- 所有列表接口 `page_size` 白名单 `{20,50,100}`，非法回落 20。
- 排序字段必须命中服务端白名单，否则回落默认（防注入）。
- 未定义字段：服务端**忽略**不报错（向前兼容），禁止 500。
- 路径不存在：返回 JSON `404 {code:1006}`，不返回 HTML（APP 解析 HTML 会崩）。

---

## 2. 连接与探测

### 2.1 `GET /api/ping` — 服务探测（免鉴权）

用途：APP 收到 UDP 广播后确认 HTTP 可用；也是端口变动后的自适应入口。

| 项 | 值 |
|---|---|
| 请求 | 无参数 |
| 成功 | HTTP 200 |
| 预期耗时 | <10ms（无任何 DB 查询） |

```json
{
  "code": 0, "msg": "ok",
  "data": {
    "name": "ScanableSelf",
    "version": "1.0.0",
    "protocol": "inventory_server",
    "protocol_version": "1.0",
    "ip": "192.168.1.10",
    "port": 5000,
    "server_time": "2026-10-15T17:30:12+08:00",
    "db_ready": true,
    "broadcast_active": true
  },
  "trace_id": "b3f19a2c"
}
```

**Schema**

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `name` | string | ✅ | 服务器显示名 |
| `version` | string | ✅ | 程序版本，semver |
| `protocol` / `protocol_version` | string | ✅ | APP 用于兼容性判断，不匹配须提示升级 |
| `ip` | string | ✅ | 实际对外服务 IP（非 0.0.0.0） |
| `port` | int | ✅ | **实际绑定成功**的端口（可能非 5000） |
| `server_time` | string | ✅ | APP 用于「今日」口径对齐 |
| `db_ready` | bool | ✅ | `false` 时 APP 应禁止写操作 |
| `broadcast_active` | bool | ✅ | UDP 是否在发；`false` 提示只能扫码配对 |

### 2.2 `GET /api/qrcode` — 配对二维码（已登录）

```json
{ "code":0, "msg":"ok",
  "data": {
    "payload_json": {"protocol":"inventory_server","version":"1.0","host":"192.168.1.10","port":5000,"name":"仓库电脑"},
    "image_png_base64": "iVBORw0KGgo...",
    "expires_at": "2026-10-15T17:40:12+08:00"
  } }
```
二维码内容为上列 JSON 的紧凑字符串（**不含任何凭据**，仅连接信息）。有效期 10 分钟，过期 APP 扫码后仍可用但须提示「二维码较旧，确认电脑未换 IP」。

### 2.3 `GET /healthz` — 进程健康（免鉴权）
返回纯文本 `ok`（非 JSON 包络，供 bat/监控脚本判活）。附带检查 DB 可连；不可连返回 `degraded` + HTTP 503。

---

## 3. 账号

### 3.1 `POST /api/login`

| 字段 | 类型 | 必填 | 校验 |
|---|---|---|---|
| `username` | string | ✅ | 2~32 位 |
| `password` | string | ✅ | 1~64 位，**禁止记录日志** |
| `device_name` | string | ❌ | ≤40，如「Redmi Note 12」 |
| `client_type` | string | ❌ | `android`(默认)/`web`/`pda` |

响应 200：
```json
{ "code":0, "msg":"ok",
  "data": { "token":"3f9a...", "expires_at":"2027-01-13T17:30:12+08:00",
            "user": { "id":1, "username":"admin", "display_name":"系统管理员",
                      "role":"admin", "must_change_password":1 } },
  "trace_id":"..." }
```
失败：401/`code:1003`（账号密码错，msg 统一「账号或密码错误」）、429/`code:1005`（`data.retry_after`=秒）、403/`code:1004`（账号停用）。
**幂等**：同用户同 `device_name` 重复登录，复用未过期的有效 Token（避免 Token 表膨胀）。

### 3.2 `POST /api/logout`
吊销当前 `X-Auth-Token`（置 `revoked_at`）。始终返回 200 `code:0`（幂等，已吊销也返回成功）。APP 无论成败都必须清本地 Token。

### 3.3 `GET /api/me`
返回当前用户 `id/username/display_name/role/must_change_password/is_active/last_login_at` + `token_expires_at`。
用途：APP 冷启动校验 Token 有效性、按 `role` 控制入口显隐；**浏览器端刷新页面后靠它恢复身份**。
**注意**：`must_change_password=1` 时除 `/api/me`、`/api/me/password`、`/api/logout` 外所有接口返回 403/`code:1004`（见 1.2）。

### 3.4 `POST /api/me/password`
`{old_password, new_password}`；新密码 ≥8 且含字母与数字；成功后**不吊销自己当前 Token**（自用体验优先），改密成功后 `must_change_password` 置 0，该标记随响应返回。
- `old_password` 不符 → **`422 {code:1002}`**，`data.errors.old_password=["原密码不正确"]`。
  **不得**返回 `1003`：那个码会让客户端清 Token 跳登录页，把「口令打错」变成「被踢下线」。

---

## 4. 基础数据

### 4.1 `GET /api/products`

| 查询参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `keyword` | string | — | 匹配 `name` 模糊 / `barcode` 精确优先 |
| `barcode` | string | — | 精确查（规范化后） |
| `category` | string | — | 精确 |
| `only_active` | bool | `true` | admin 可传 `false` 看含软删 |
| `page`/`page_size`/`sort`/`order` | — | `1`/`20`/`id`/`desc` | `sort` 白名单：`id,name,barcode,category,updated_at` |

响应 `list` 元素 Schema（**所有接口返回的商品对象统一为此结构**）：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | int | ❌ | |
| `name` | string | ❌ | ≤100 |
| `barcode` | string | ✅ | `null`=无条码 |
| `internal_code` | string | ❌ | 内码 `S0000000123`，打印用 |
| `category` | string | ❌ | |
| `unit` | string | ❌ | |
| `cost_price` / `sale_price` | float | ❌ | 2 位小数 |
| `min_stock` | int | ❌ | |
| `is_active` | int | ❌ | 0/1 |
| `remark` | string | ✅ | |
| `total_qty` | int | ❌ | **全仓库合计库存**（冗余带出，省 APP 二次查询） |
| `stock_by_warehouse` | array | ❌ | `[{warehouse_id, qty}]` |
| `created_at`/`updated_at` | string | ❌ | `updated_at` 兼乐观锁版本 |

### 4.2 `GET /api/products/by-barcode/<barcode>` — **扫码主接口**

APP 离线兜底与网页扫码都靠它。响应同 4.1 的单个商品对象 + `stock_logs` 最近 5 条流水摘要（`[{id,type,quantity,created_at}]`）。
未命中：**`404 {code:1007}`**，`data.barcode` 回显规范化后的条码；APP 据 `code=1007` 触发「去电脑端新增」引导。
> 原文档在同一条路径上同时写了 1006 与 1007（一个接口两种语义），实现期统一为 **1007 + HTTP 404**。
路径参数做 URL 解码 + 规范化，长度 3~48，非法字符 422/`code:1002`。

### 4.3 `POST /api/products`（admin）

| 字段 | 必填 | 校验 |
|---|---|---|
| `name` | ✅ | 1~100 |
| `barcode` | ❌ | 3~48，`[0-9A-Za-z-]`，服务端规范化 |
| `category` `unit` | ❌ | 默认 `未分类` / `件` |
| `cost_price` `sale_price` | ❌ | ≥0 |
| `min_stock` | ❌ | ≥0 整数 |
| `remark` | ❌ | ≤200 |
| `form_token` | 网页✅ | 防重复提交 |

200 → 商品对象（含服务端生成的 `internal_code`）。
409/`code:1010` → `data.conflict = {id, name, barcode, is_active}`（**必须回传冲突商品详情**，让用户知道是哪条占了）。
422/`code:1002` → `data.errors`。

### 4.4 `PUT /api/products/<id>`（admin）
同 4.3 字段，全部**可选**（增量更新），**必须**额外带 `expected_updated_at`（乐观锁）。
- 不匹配 → 409/`code:1011`，`data.current_updated_at` 返回最新值，APP/网页提示「他人已修改，请刷新」。
- `barcode` 传 `null` 与不传含义不同：不传=不改，传 `null`=清空。这条语义必须在实现和文档同时锁定。

### 4.5b `POST /api/products/<id>/restore`（admin）
恢复被软删除的商品（`is_active=1`、`deleted_at=NULL`）。
> 该接口是 PRD 4.3.5「软删商品条码永久占用、确需复用请恢复而非新建」这条规则的**唯一落地出口**，
> 实现期补入契约（原清单遗漏，故接口总数由 33 → 34）。

### 4.5 `DELETE /api/products/<id>`（admin）
默认软删（`is_active=0, deleted_at=now`）。`?hard=true` 且该商品**无任何流水**时才物理删除，否则 409/`code:1010`（`data.log_count`）。

### 4.6 `GET /api/warehouses`
`list` 元素：`{id,name,address,is_active,sort_order,product_count,total_qty}`。默认只返回 `is_active=1`，`?all=true`（admin）含停用。

### 4.7 `POST /api/warehouses`（admin）/ `PUT /api/warehouses/<id>`（admin）
`name` 1~30 唯一；PUT 支持 `is_active`；停用仓库后不可再在其中出入库（写接口校验，422/`code:1008`）。DELETE 未提供（软删即停用，避免误删路径）。

### 4.8 `GET /api/partners?type=CUSTOMER` / `POST /api/partners`
`GET` 的 `type` 是**可选筛选**：不传返回全部启用记录（`data.type = null`），传非法值 → 422/`code:1002`。
> 为什么不传要能返回全部：出入库页的「盘点调整」模式既不是出库也不是入库，没有客户/供应商语义，
> 前端按可选传参（`type?: "CUSTOMER" | "SUPPLIER"`）；服务端若强制必填，页面切到调整模式就会假报
> 「type 不能为空」。契约与实现必须一致，见 T-30。

`POST` 字段：`name`(必填,1~50)、`type`(必填,枚举)、`contact`、`phone`、`address`、`remark`。`(name,type)` 冲突 → 409/`code:1010`。

---

## 5. 库存业务（核心）

### 5.1 `POST /api/stock/in` 与 `POST /api/stock/out`

**请求 Schema**（两接口字段完全一致，仅方向不同）

| 字段 | 类型 | 必填 | 校验 | 说明 |
|---|---|---|---|---|
| `client_msg_id` | string | ✅ | UUID v4，≤36，全局唯一 | **缺失即 422，无默认值**（强制幂等） |
| `product_id` | int | 二选一 | 存在且 `is_active=1` | |
| `barcode` | string | 二选一 | 规范化后能命中启用商品 | 与 `product_id` 同时存在时校验一致，不一致 422 |
| `warehouse_id` | int | ✅ | 存在且 `is_active=1` | 否则 `code:1008` |
| `quantity` | int | ✅ | 1~100000 | 小数/0/负 → `code:1002` |
| `partner_id` | int | ❌ | 存在且 `type` 匹配（OUT→CUSTOMER / IN→SUPPLIER） | 不匹配 422 |
| `remark` | string | ❌ | ≤200 | |
| `device_name` | string | ❌ | ≤40 | 仅 `source=APP` 时有意义 |
| `client_created_at` | string | ❌ | ISO-8601 | APP 离线补传时的**业务发生时刻**；缺省用服务器时间。服务端始终另存 `received_at` 供审计 |

**成功响应 200**
```json
{ "code":0, "msg":"ok",
  "data": {
    "log_id": 1024,
    "type": "OUT",
    "product_id": 7, "product_name": "可乐 330ml",
    "warehouse_id": 1, "warehouse_name": "主仓库",
    "quantity": 5, "before_qty": 12, "after_qty": 7,
    "duplicated": false,
    "created_at": "2026-10-15T17:30:12+08:00"
  }, "trace_id":"b3f19a2c" }
```

**幂等重复响应 200**（`code` 仍为 0，HTTP 不报错——这是关键约定）
```json
{ "code":0, "msg":"ok", "data": { "log_id":1024, "duplicated": true, "after_qty":7, ... },
  "trace_id":"..." }
```
判据：`data.duplicated=true` 表示该 `client_msg_id` 已存在，返回首次落库结果，**未产生新流水、未重复扣减**。

**失败响应**
```json
{ "code":1009, "msg":"库存不足",
  "data": { "product_id":7, "warehouse_id":1, "current":3, "requested":5, "lack":2 },
  "trace_id":"b3f19a2c" }
```

**实现约束**（不可协商）
1. 扣减用条件更新并判 `rowcount`（见 01-PRD 5.4 规则 4）；**禁止**先 `SELECT` 判断再 `UPDATE`。
2. `stock_logs` 插入 + `stock_current` 更新 + 快照字段填写，在同一事务。
3. `before_qty/after_qty` 从同事务内的更新结果取值，禁止用「查询-计算」推导。

### 5.2 `POST /api/stock/adjust`（admin）

| 字段 | 必填 | 说明 |
|---|---|---|
| `client_msg_id` | ✅ | |
| `product_id` 或 `barcode` | ✅ | |
| `warehouse_id` | ✅ | |
| `target_qty` | ✅ | 0~1000000，**盘点后的实际数量** |
| `remark` | ✅ | 必须说明差异原因，空值 422 |

服务端计算 `quantity = target_qty - 当前 qty`（可负），写入 `type='ADJUST'`。响应含 `diff` 字段。
`operator` 调用 → 403/`code:1004`。

### 5.3 `POST /api/stock/reverse/<log_id>`（admin）

冲销一条错误单据，生成反向流水（`type` 取反 IN↔OUT；`ADJUST` 不可冲销，返回 422）。
请求：`{client_msg_id, reason}`（`reason` 必填 ≤200）。
- 已成功 → 200，`data.reversal_log_id`；原记录 `reversed_by` 被填。
- 已冲销过 → 409/`code:1010`，`data.reversed_by=<已有冲销 id>`（**绝不允许生成第二条冲销**）。
- 冲销导致库存不足（原出库现库存不够加回？不存在此风险；原入库现库存不够扣回）→ 422/`code:1009`，提示先盘点调整。

### 5.4 `POST /api/sync/stock-logs` — 离线批量同步

**请求**：`{ "logs": [ <5.1 结构>，... ] }`，单次 1~200 条，超出 422/`code:1002`。每条必须自带 `type`（`IN`/`OUT`/`ADJUST`）。

**响应 200**（逐条结果，顺序与请求一致）
```json
{ "code":0, "msg":"ok",
  "data": {
    "received": 20, "ok_count": 18, "duplicated_count": 1, "failed_count": 1,
    "results": [
      { "client_msg_id":"...", "ok":true,  "duplicated":false, "log_id":1024, "after_qty":7 },
      { "client_msg_id":"...", "ok":true,  "duplicated":true,  "log_id":1010, "after_qty":7 },
      { "client_msg_id":"...", "ok":false, "code":1007, "msg":"条码未登记" }
    ]
  }, "trace_id":"..." }
```

**APP 侧处理规则（写进安卓文档）**
- `ok:true`（含 `duplicated:true`）→ 本地置 `SYNCED`。
- `ok:false` 且 `code` ∈ {1007,1008,1002,1009} → 置 `FAILED`，`retry_count+1`，停止自动重试转人工列表（这些是业务错，重试也不会成）。
- `code` ∈ {1003} → 整批中止，清 Token 回配对页，**队列保留**。
- HTTP 超时/连接失败 → **不得**标记失败，保留 `PENDING`，下次**用同一 `client_msg_id` 重发**（超时 ≠ 失败，幂等保证不重复入账）。

**服务端保护**：全局 `sync_lock` 串行；排队 >10s → 503/`code:1012` + `Retry-After: 5`。

---

## 6. 查询与报表

### 6.1 `GET /api/stock/logs`

| 参数 | 默认 | 说明 |
|---|---|---|
| `date_from` / `date_to` | 今日 | `YYYY-MM-DD`（闭区间，按东八区）；跨度 >366 天 → 422 |
| `product_id` / `barcode` / `keyword` | — | 三者共存时 AND |
| `warehouse_id` | — | |
| `type` | — | `IN`/`OUT`/`ADJUST`，可逗号多选 |
| `operator_id` | — | |
| `source` | — | `WEB`/`APP`/`PDA` |
| `only_reversed` | `false` | 只看已冲销 |
| `page`/`page_size`/`sort`/`order` | `1`/`20`/`created_at`/`desc` | `sort` 白名单 `created_at,id,quantity,product_id` |

`list` 元素：流水全字段（含 `product_name_snapshot`、`warehouse_name`、`operator_name`、`partner_name`、`reversed_by`、`amount`、`source`、`device_name`、`trace_id`）。
**注意**：列表展示商品名一律用 `product_name_snapshot`（历史快照），仅在其为空时回落 `products.name`。

### 6.2 `GET /api/stock/current`
参数：`warehouse_id`、`product_id`、`keyword`、`low_only`(bool)、`negative_only`(bool, admin 排障用)、`page`/`page_size`。
元素：`{product_id, product_name, barcode, unit, warehouse_id, warehouse_name, qty, min_stock, is_low_stock, last_in_at, last_out_at, updated_at}`。
`is_low_stock` 由服务端计算（`qty < min_stock`），**两端都不得自行重算**（口径必须唯一）。

### 6.3 `GET /api/stock/summary`
`dimension`=`DAY`/`PRODUCT`/`CATEGORY`/`WAREHOUSE`/`OPERATOR`；`date_from`/`date_to`；`metrics`=`qty,amount`（默认 both）。
```json
{ "code":0, "data": {
    "dimension":"DAY", "date_from":"2026-09-16", "date_to":"2026-10-15",
    "rows":[ {"key":"2026-10-15","label":"2026-10-15","in_qty":40,"out_qty":17,"in_amount":320.5,"out_amount":890.0,"bill_count":12} ],
    "total": { "in_qty":612,"out_qty":480,"in_amount":5120.4,"out_amount":12003.0,"bill_count":183 } } }
```

### 6.4 `GET /api/dashboard`
一次性返回仪表盘全部指标，避免 APP/网页多次往返：
`{product_count, total_qty, today:{in_qty,in_count,out_qty,out_count}, low_stock_list:[...≤50], low_stock_total, recent_logs:[...≤10], server:{ip,port,name,version,server_time,broadcast_active}, last_backup_at, stock_anomaly_count}`
`stock_anomaly_count>0`（负库存）时 APP/网页必须红字告警。

### 6.5 `GET /api/stock/logs/export`（网页，CSV）
同 6.1 参数 + `format=csv`。**同步流式**下载，上限 50,000 行，超出 422 提示分段。CSV **必须带 UTF-8 BOM**（否则 Excel 中文乱码）。

---

## 7. 系统

### 7.1 `GET /api/settings`（admin）
返回 `{items:[...], groups:[{id,title}]}`，`items` 元素为
`{key, value, raw_value, value_type, needs_restart, updated_at, group, label, hint, options?}`。
- `value` 已按 `value_type` 转换：`bool` 是**真布尔**、`int` 是数字（前端 Switch / InputNumber 直接绑定）；
- `group` / `label` / `hint` / `options` 由服务端给（`settings_service.SETTING_META`）：
  前端**不得**另写一套标签与分组，否则改一处漏一处就会出现「界面写着 A、实际改的是 B」。

### 7.2 `PUT` / `POST /api/settings`（admin）
`{items:{key:value}}` 或 `{items:[{key,value}]}`，逐个校验 key 白名单 + 类型转换（`int`/`bool`/`json`）；非法 key 或值 422。
响应 `{updated:[key...], needs_restart:[key...], items:[...]}`，前端据此提示「需重启生效」。

**★网络项不收**：`http_port`、`broadcast_enabled` 出现在本接口的 `items` 里一律 `422`，
必须走 7.5。网页端原本只是「表单不带这两个字段」，但接口不拦的话，
那道「改端口要再输本人口令」的闸一条请求就能绕过 —— 后果是全屋手机静默失联。

### 7.3 `POST /api/backup`（admin）
用 SQLite `backup()` API 生成一致性快照（**禁止文件复制**）。响应 `{filename, size_bytes, created_at, trigger}`。失败 500/`code:1999` 且必须在仪表盘告警。
### 7.4 `GET /api/backup/list` / `POST /api/backup/restore`（admin）
`list` 响应 `{list:[BackupInfo...], total, status:{last_ok_at,last_error,consecutive_failures}}`；
`status.consecutive_failures >= 3` 时界面必须显示红条告警（L2 降级）。
restore 需 `{filename, password}`：
- 口令不符 → `422 {code:1002}`（**不是** 1003，见 1.3）；`filename` 必须是 `inventory_*.db` 且不含 `/`、`..`；
- 先自动把当前库备份为 `pre_restore`，再替换；
- 恢复会覆盖当前全部数据 ⇒ 属于 PRD 3.5 的二次确认清单。

### 7.5 `POST /api/settings/network`（admin）
`{items:{http_port?,broadcast_enabled?}, password}`；`password` 是**操作人自己**的登录口令。
- 非网络项 → `422`；口令错 → `422 {code:1002}`（`data.errors.password`）；
- 成功返回同 7.2 的结构（网络项均标 `needs_restart`）。

### 7.6 `GET /api/system/info`（admin）
服务端自述：`{url, urls, ip, port, name, candidates, firewall_ports, firewall_commands,
concurrency_hint, version, device_name, schema_version, now, data_dir, db_path, backups_dir,
logs_dir, disk:{total_mb,free_mb,used_percent}, backup, autostart, broadcast_active}`。
- `ip`/`port` 取**实际绑定**结果（端口被占会顺延 5001~5010），不是配置值；
- `broadcast_active` 与仪表盘同源，禁止各算一套（否则两屏显示相反状态，运维不知道信谁）。

### 7.7 `GET /api/qrcode`（已登录）
`{payload_json, image_png_base64, host, port, expires_at, candidates, text}`。二维码内容只含连接信息，**不得**包含任何凭据。

### 7.8 `GET` / `POST /api/backup/recalc`（admin）
- `GET` 只读自检：`{count, list, truncated}`（`list` 最多 50 条）。
  口径与 `docs/02` 一致性检查 1 完全一致：**只逐行比对 `stock_current` 已有行**；
  「有流水但余额行缺失」不在检出范围内，需 `manage.py recalc-stock` 全量对账。
- `POST` 按流水全量重算：`{changed, backup}`；写前自动备份，不一致为 0 时不备份也不写库。
  这是**唯一**允许改 `stock_current` 的路径（手工 UPDATE 是「库存对不上」的源头）。

---

## 8. UDP 协议（与 API 同级契约）

### 8.1 广播报文（电脑 → 局域网，UDP:55555，每 3s）
```
INVENTORY_SERVER v1.0 IP:192.168.1.10 PORT:5000 NAME:仓库电脑
```
- 编码 UTF-8，≤512 字节。
- 结构：固定魔数 `INVENTORY_SERVER` + 版本 `v1.0` + 若干 `KEY:VALUE`，**空格分隔**。
- `KEY` 集合：`IP`、`PORT`、`NAME`（`NAME` 可选）。
- **`NAME` 必须放最后**（其值可含空格，解析规则为「取 `NAME:` 后到行尾的全部字符」）。新增字段必须插在 `NAME` 之前，否则破坏兼容解析。
- 解析容错：未知 `KEY` 必须忽略（向前兼容）；`IP`/`PORT` 缺失或非法则整包丢弃。

### 8.2 安卓监听要点
- `DatagramSocket(55555)`，`soBroadcast = true`，绑定前检查 `WifiManager` 状态。
- Android 需 `ACCESS_WIFI_STATE`；同一端口在部分机型上二次绑定会失败 → 异常必须捕获并回落「扫码配对」。
- 发现后**必须**用 `/api/ping` 验证 HTTP 可达才显示「可连接」，避免 UDP 通但 HTTP 挂的假阳性。

### 8.3 配对二维码内容（JSON，紧凑无空格）
```json
{"protocol":"inventory_server","version":"1.0","host":"192.168.1.10","port":5000,"name":"仓库电脑"}
```

---

## 9. 账号与设备（admin，实现见 `api/users.py`）

> 全部 `require_admin`。网页端原本只有两个 Jinja 页面，SPA 只能走 JSON 接口。
> 「重置口令 / 停用账号」必须再输**操作人自己**的口令（PRD 3.5），故入参透传 `password`。

### 9.1 `GET /api/users`
`{list:[{id,username,display_name,role,is_active,must_change_password,created_at,updated_at,last_login_at}],
roles:[{value,label}], me:<当前登录者 id>}`。
**`password_hash` 绝不出现在任何响应里**（T-19）：接口层必须显式剥掉，不能透传 `user_repo` 整行。

### 9.2 `POST /api/users`
`{username, display_name?, role, password, password_confirm, must_change?}`。
- 用户名重复 → `422`（含「已经存在」）；
- 两次口令不一致 / 弱口令 → `422`，`data.errors` 指向对应字段；
- 详情字段 `password_confirm` 缺失时按不校验处理（`password` 仍必填）。

### 9.3 `POST /api/users/<id>/password`
`{password, new_password, new_password_confirm?}`；`password` 是**操作人自己**的口令。
- 自己的口令错 → `1002 / 422`（**不是** 1003，见 1.3）；
- 成功后**吊销该账号全部设备令牌**（换口令即踢所有旧设备），响应 `{user, revoked_tokens}`。

### 9.4 `POST /api/users/<id>/active`
`{is_active: <boolean>, password}`。
- `is_active` **必须是显式布尔**：字符串 `"false"`、`0`、缺省字段一律 `422`。
  不能写成 `bool(payload.get("is_active"))` —— 漏传会被当成「停用该账号」，属静默破坏；
- 停用会吊销该账号设备令牌，但**历史流水保留经办人**（审计链不能断）；
- 禁止停用自己 / 停用最后一个启用的管理员 → `422`。

### 9.5 `GET /api/devices`
`{list:[{id,user_id,client_type,device_name,created_at,last_used_at,expires_at}]}`（只列未吊销的）。
### 9.6 `POST /api/devices/<token_id>/revoke`
吊销单台设备令牌；不存在 → `422`。响应 `{token:{...}}`。

---

## 10. 标签打印（已登录，实现见 `api/labels.py`）

### 10.1 `GET /api/labels`
在商品分页结果上补打印所需字段，并带上标签纸元信息：
`{list:[Product + {barcode_text, narrow}], page, page_size, total, has_more,
label_size:"40x30", label_w:40, label_h:30, max_copies:50}`。
- `barcode_text`：有码商品用条码，**无码商品回落内部码** `S%010d`（按商品 id）；
- `narrow`：当前标签纸上条码是否过窄（可能扫不出），前端必须给出橙色提示。

### 10.2 `GET /api/products/<id>/barcode.svg` / `.png`
返回**图片本体**，不套包络（见 1.3 的显式豁免）。SVG 由服务端自绘并过 PRD 5.1 白名单，
前端只把它当图片交给 `<img>`，**不做任何 HTML 注入**。

---

## 11. 离线授权（**免鉴权且免门禁**，实现见 `api/activation.py`）

> 未激活时全站被门禁拦下，若这两个接口还要 Token，就「登录不了、也激活不了」。
> 注册码与机器码绑定，故不依赖 Cookie，也不适用 CSRF。

### 11.1 `GET /api/activation/state`
`{activated, message, license_code, machine_id, display_machine_id, expire_text, expire_ts, config_error}`。
`machine_id` 是出码的唯一输入，界面必须能一键复制；`config_error` 非空表示授权模块配置异常。

### 11.2 `POST /api/activation`
`{code}`。成功 → `200 {code:0}`，并刷新进程内的 `LICENSE_STATE`（**不在请求里重新验签**，见 `core/license_gate.py`）。
失败 → `403 {code:1013}` 且 `data` 带失败原因；**不得**用 200 表示失败（否则前端弹绿条）。

---

## 12'. WMS 单据层（采购订单 / 采购入库）—— 新增 15 个接口

> 依据 `docs/13-yudao-WMS整合方案与实施计划.md` 第 6 章。实现见 `desktop/api/wms_purchase.py`。
> ★ 单据只在**电脑端浏览器**使用；安卓端继续走 `/api/stock/*` 扫码出入库（docs/13 决策 6）。
> ★ 单据**不物理删除**：作废走 `/cancel`（`status=CANCELED` + `is_active=0`），
>   已过账单据不可改，更正请走 `/api/stock/reverse/<log_id>` 冲销（账本不可变）。

### 12'.1 单据状态机

```
采购订单：DRAFT ──submit──▶ SUBMITTED ──approve──▶ APPROVED ──(入库收满)──▶ CLOSED
              │                  │
              │                  └──reject(必填原因)──▶ REJECTED ──edit──▶ DRAFT
              └──cancel(admin)──▶ CANCELED

采购入库单：DRAFT ──submit──▶ SUBMITTED ──approve──▶ APPROVED（★同步过账，posted_at 落值）
                                    └──reject──▶ REJECTED        │
                                                                 └── 已过账：禁止改/删，只能冲销
```

`POST .../approve` 请求体：`{"action":"APPROVE"}` 或 `{"action":"REJECT","reject_reason":"..."}`。
入库单 `APPROVE` **同步过账**：写 `stock_logs`（`ref_type='PURCHASE_IN'`）+ 更新 `stock_current`
+ 回写订单明细 `received_qty`，整单一个事务（docs/13 铁律 2）。

### 12'.2 采购订单

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/wms/purchase-orders` | 已登录 | 分页；`status` / `partner_id` / `date_from` / `date_to` / `keyword` / `page` / `page_size` |
| GET | `/api/wms/purchase-orders/<id>` | 已登录 | 含 `details[]` |
| POST | `/api/wms/purchase-orders` | 已登录 | `name`(1~60)、`partner_id`(必须是 SUPPLIER)、`applicant_date`、`remark`、`lines[]` |
| PUT | `/api/wms/purchase-orders/<id>` | 已登录 | 仅 `DRAFT`/`REJECTED` 可改，否则 `409 {code:1014}` |
| POST | `/api/wms/purchase-orders/<id>/submit` | 已登录 | 明细为空 → `422 {code:1015}` |
| POST | `/api/wms/purchase-orders/<id>/approve` | **admin** | 见状态机 |
| POST | `/api/wms/purchase-orders/<id>/cancel` | **admin** | 已 CLOSED 不可取消 |

`lines[]` 元素：`product_id`(必填，商品须存在且启用)、`qty`(1~100000 整数)、`price`(≥0，默认 0)、`remark`。
响应含服务端算出的 `code`、`total_qty`、`total_amount`、`status`、每行 `received_qty`。

### 12'.3 采购入库单

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/wms/purchase-inbounds` | 已登录 | 分页；`status` / `order_id` / `warehouse_id` / `date_from` / `date_to` / `keyword` |
| GET | `/api/wms/purchase-inbounds/<id>` | 已登录 | 含 `details[]` |
| POST | `/api/wms/purchase-inbounds` | 已登录 | `name`、`warehouse_id`(须启用，否则 `422 {code:1008}`)、`order_id`(可选，须已 APPROVED，否则 `409 {code:1014}`)、`in_date`、`lines[]` |
| PUT | `/api/wms/purchase-inbounds/<id>` | 已登录 | 已过账 → `409 {code:1014}` |
| POST | `/api/wms/purchase-inbounds/<id>/submit` | 已登录 | |
| POST | `/api/wms/purchase-inbounds/<id>/approve` | **admin** | APPROVE 时同步过账 |
| POST | `/api/wms/purchase-inbounds/<id>/cancel` | **admin** | 已过账不可取消 |

`lines[]` 元素：在订单行基础上多一个 `order_detail_id`（可选，指向采购订单明细；填了就受可收量约束）。
入库量超出 `qty - received_qty` → `409 {code:1016}`（`data` 含 `receivable`），且**整单回滚**。

### 12'.4 通用操作单（领用/退库/归还/借用/调拨）

`op_type` 枚举（大写）：`RECEIVE` 领用 / `RETURN_STOCK` 退库 / `RETURN_BORROW` 归还 / `BORROW` 借用 / `TRANSFER` 调拨。
过账方向（唯一权威，见 `docs/13` 4.6）：`RECEIVE`/`BORROW` → **OUT**；`RETURN_STOCK`/`RETURN_BORROW` → **IN**；
`TRANSFER` → **源仓 OUT + 目标仓 IN**（同一事务两条流水，任一条失败整体回滚）。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/wms/operation-orders` | 已登录 | 分页；`op_type` / `status` / `warehouse_id` / `date_from` / `date_to` / `keyword` |
| GET | `/api/wms/operation-orders/<id>` | 已登录 | 含 `details[]` |
| POST | `/api/wms/operation-orders` | 已登录 | `name`、`op_type`、`warehouse_id`、`to_warehouse_id`(仅 TRANSFER)、`operator_user_id`(默认当前用户)、`expect_return_date`(仅 BORROW，必填)、`partner_id`、`op_date`、`lines[]` |
| PUT | `/api/wms/operation-orders/<id>` | 已登录 | 仅 `DRAFT`/`REJECTED` 且未过账；`op_type` 不可改 |
| POST | `/api/wms/operation-orders/<id>/submit` | 已登录 | 明空 → `422 {code:1015}` |
| POST | `/api/wms/operation-orders/<id>/approve` | **admin** | APPROVE 同步过账 → `DONE`；REJECT 需 `reject_reason` |
| POST | `/api/wms/operation-orders/<id>/reverse` | **admin** | **冲销**：对已过账流水逐条红字冲销 → `REVERSED`；`reason` 必填；重复冲销 `409 {code:1010}` |
| POST | `/api/wms/operation-orders/<id>/cancel` | **admin** | 仅未过账可作废 |

`lines[]`：`product_id`、`qty`(1~100000)、`price`(可空，空则按成本价/售价兜底)、`serial_number`、`is_join_asset`、`remark`。

> ★ **冲销不是删单**：原流水不动，新增反向流水并回写 `reversed_by`；单据置 `REVERSED` + `reversed_at`。
> 领用（OUT）冲销后库存回到原值；若期间库存已被消耗导致冲销时不足，返回 `422 {code:1009}`（提示改用盘点调整）。

### 12'.5 `GET /api/wms/approvals` — 审批待办（admin）

三类单据里 `status='SUBMITTED'` 的合并视图，按 `submit_at` 倒序，**不分页**（待办是小集合）。

- 查询参数：`type`（可选，`PURCHASE_ORDER` / `PURCHASE_INBOUND` / `OPERATION_ORDER` / `STOCKTAKE`，缺省=全部）
- `data`：`{list:[{biz,biz_label,id,code,name,total_qty,total_amount,submit_at,submitter,counterparty,warehouse_name?,to_warehouse_name?,op_type?,op_label?}], total}`
- 语义：**只读汇总**。审批仍走各自的 `/approve` 接口，状态机只有一处实现。

### 12'.6 `GET /api/wms/export/<biz>` — 单据 CSV 导出（已登录）

`biz` ∈ `purchase-orders` / `purchase-inbounds` / `operation-orders` / `stocktakes`（未知值 404/`code:1006`）。

- 过滤参数与对应列表接口一致（`status` / `date_from` / `date_to` / `keyword` / `warehouse_id` …）
- **不套 JSON 包络**：成功直接回 `text/csv`，`Content-Disposition: attachment`，`X-Row-Count` 带行数
- 编码 `utf-8-sig`（BOM）+ `\r\n`；上限 5 万行，超出 `422/1002` 并提示缩小日期范围
- 只导出列表列；明细走详情接口

### 12'.7 单据层相关设置项

| key | 类型 | 默认 | 含义 |
|---|---|---|---|
| `wms_bills_enabled` | bool | `true` | **单据总开关**。关闭后所有单据写操作返回 `409 {code:1018}`；历史单据仍可查，`stock_logs`/`stock_current` 一行不动（docs/13 第 8.2 节回滚方案） |
| `wms_operation_approve_required` | bool | `true` | 领用/退库/归还/借用/调拨是否需要审批。`false` 时 `/submit` 即过账（`status` 直接 `DONE`）；采购入库**始终**强制审批 |

> 设置项白名单的权威来源是 `services/settings_service.KEY_SCHEMA`（`docs/01` 第 20 章只列环境变量）。
> 新增设置项必须同时改 `settings_repo.DEFAULTS` + `KEY_SCHEMA` + `SETTING_META`，否则 `POST /api/settings` 会拒写。

### 12'.8 单据层新增错误码

| code | HTTP | 语义 |
|---|---|---|
| 1014 | 409 | 单据状态不允许该操作（含「已过账不可改」） |
| 1015 | 422 | 单据明细非法（空明细 / 行不是对象 / direction 非法 / 超 200 行） |
| 1016 | 409 | 超出单据可收（可发）数量 |
| 1017 | 409 | 仓库层级非法（父级不存在 / 超过两级 / 改挂上级 / 有子库位禁止停用） |
| 1018 | 409 | WMS 单据功能已关闭（`wms_bills_enabled=false`） |

---

## 12''. 基础数据批量导入 / 导出（P0-1）—— 新增 4 个接口

> 依据 `docs/14-上线前功能补齐方案.md` 第 3 章。实现见 `desktop/api/imports.py` + `services/import_service.py`。
> ★ 两段式无状态：**preview 与 commit 共用同一套解析/校验函数**，不分叉。
> ★ 期初库存只能走 `stock_service.opening_stock()`，禁止直接写 `stock_current`。

### 12''.1 接口表

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/imports/templates/<kind>` | 已登录 | 下载 CSV 模板（表头 + 2 行示例 + `#` 注释行），`utf-8-sig` + `\r\n` |
| POST | `/api/imports/preview` | 已登录 | `multipart/form-data`：`kind` + `file`；**不落库**，返回统计与逐行错误 |
| POST | `/api/imports/commit` | 已登录；`opening-stock` 仅 admin | 同上 + `skip_invalid`（默认 `false`=整批回滚） |
| GET | `/api/imports/export/<kind>` | 已登录 | 导出 CSV：`products` / `partners` / `stock-current` |

`kind` ∈ `products`（商品）/ `partners`（往来单位）/ `opening-stock`（期初库存）。

### 12''.2 模板列（唯一权威在 `services/import_service.py: TEMPLATES`）

| kind | 列 | 必填 | 规则 |
|---|---|---|---|
| `products` | `name,barcode,category,unit,cost_price,sale_price,min_stock,remark` | `name` | 条码可空，填了需 3~48 位 `[0-9A-Za-z-]` 且全局唯一（含已停用商品）；价格 ≥0；预警库存为 0~10⁶ 整数 |
| `partners` | `name,type,contact,phone,address,remark` | `name,type` | `type` ∈ `CUSTOMER`/`SUPPLIER`（大小写不敏感，入库统一大写）；`(name,type)` 唯一 |
| `opening-stock` | `barcode,warehouse_code,qty,remark` | 全部三项 | `warehouse_code` 可填**仓库编码或仓库名称**（`initdb` 建的主仓库没有编码）；`qty` 1~100000 整数 |

### 12''.3 文件格式约定

| 项 | 规定 |
|---|---|
| 编码 | `utf-8-sig` → `gbk` 回退；都失败 `422/1019`（T-39） |
| 分隔符 | 逗号，RFC 4180 双引号转义（标准库 `csv`） |
| 表头 | 按名称匹配、顺序无关；缺必填列 → `422/1019` 并列缺失列名 |
| 行数 / 大小 | ≤ 5000 行、≤ 5 MB，超限 `422/1020` |
| 空行 / 注释 | 全空行与 `#` 开头行跳过，不计入错误 |
| 数值 | 自动剥离 `¥ ￥ ,` 与空格；`qty`/`min_stock` 必须是整数，小数 → 该行失败 |
| 行号 | 报错里的 `row` 是 **CSV 物理行号**（与 Excel 行号一致；表头是第 1 行） |

### 12''.4 响应

`preview`：

```json
{"code":0,"msg":"ok","data":{
  "kind":"products","total":812,"valid":806,"invalid":6,
  "errors":[{"row":12,"field":"barcode","value":"6901234567890","msg":"条码已被「可乐 330ml」占用"}],
  "errors_truncated":false,
  "preview":[{"name":"矿泉水 550ml","barcode":"6900000000001"}],
  "unknown_columns":["备注2"]
}}
```

`commit`：在上述基础上多 `ok_count` / `failed_count` / `duplicated_count` / `failed_rows[]`；
`skip_invalid=false` 且存在错误行 → `422/1019`，`data.rows` 是逐行错误（整批未落库）。

**统计字段口径（`preview` 与 `commit` 必须完全一致，客户端可对比）**：

| 字段 | 语义 |
|---|---|
| `total` | 文件里的**数据行数**（不含表头、空行、`#` 注释行）；`preview` 与 `commit` 同值 |
| `valid` | 校验通过、可导入的行数（= `total - invalid`） |
| `invalid` | 校验不通过的行数；`skip_invalid=true` 时这些行被跳过、计入本字段 |
| `ok_count` / `failed_count` / `duplicated_count` | 只统计 `valid` 行的**执行**结果（成功入库 / 入库失败 / 已存在跳过） |

`commit` 的 `total` 不再只统计合法行 —— 否则同一份文件在预检与提交时会给出两个不同的「总行数」。
只有表头没有数据行 → `422/1019 文件没有数据行`；有数据行但全部不合法 → `200` + `ok_count=0, invalid>0`（不是空文件）。

### 12''.5 期初建账的硬约束

1. 只在**账本为空**（`stock_logs` 为 0 行）时允许；否则 `409/1021`。
2. **先判幂等再判非空**：同一份文件重复提交返回 `duplicated_count>0`、`duplicated:true`，不重复加库存（T-40）。
   顺序反了会让「重复提交」永远被 1021 误拦。
3. 流水写 `ref_type='OPENING'`、`ref_id=0`（期初不指向单据，用 0 占位以满足「同有同无」）、
   `partner_id=NULL`（**不得**污染供应商对账）、`remark` 缺省「期初建账」、`unit_price` 取 `products.cost_price`。
4. 幂等键：`uuid5(barcode|warehouse_code)` 按 v4 重打包（与 `bill_msg_id` 同一套路），
   **同一商品同一仓库在文件内重复出现会被挡下**（否则两行共用一个幂等键，第二行会静默丢失）。

### 12''.6 新增错误码

| code | HTTP | 语义 |
|---|---|---|
| 1019 | 422 | 导入文件非法（编码无法识别 / 表头缺列 / 空数据 / 单元格格式错） |
| 1020 | 422 | 导入超限（>5000 行 或 >5MB） |
| 1021 | 409 | 期初库存已存在（账本非空，不允许重复建账） |

---

## 12'''. 盘点单（P0-2）—— 新增 9 个接口

> 依据 `docs/14-上线前功能补齐方案.md` 第 4 章。实现见 `desktop/api/wms_stocktake.py` + `services/stocktake_service.py`。
> ★ 盘点单是**第四类单据**（PO / PI / GO / **ST**），但它过账写的是 `ADJUST`，
>   因此**不能**复用 `post_bill_lines`（只收 IN/OUT 正数），改走 `stock_service.post_adjust_lines()`。
>   两者规则一致：整单一个事务、幂等键由单据派生、每条流水带 `ref_type`+`ref_id`。

### 12'''.1 状态机

```
DRAFT ──submit──▶ SUBMITTED ──post(admin)──▶ POSTED（posted_at 落值，不可改不可作废）
  │                   └──reject(admin, reason 必填)──▶ REJECTED ──改──▶ SUBMITTED
  └──cancel(DELETE)──▶ CANCELED（作废只改状态，明细一律保留）
```

### 12'''.2 接口表

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/wms/stocktakes` | 已登录 | 分页；`status` / `warehouse_id` / `date_from` / `date_to` / `keyword` |
| POST | `/api/wms/stocktakes` | 已登录 | 建单 + 生成快照：`name`(1~50)、`warehouse_id`、`category`(可选)、`remark` |
| GET | `/api/wms/stocktakes/<id>` | 已登录 | 含 `details[]`、`diff_count`、`diff_abs_qty`、`period_changes` |
| PUT | `/api/wms/stocktakes/<id>` | 已登录 | 仅 `DRAFT`/`REJECTED`：改 `name`/`remark`、录 `lines[]`、追加 `append[]` |
| DELETE | `/api/wms/stocktakes/<id>` | 已登录 | 仅 `DRAFT`/`REJECTED` 可作废（置 `CANCELED`，不删明细） |
| POST | `/api/wms/stocktakes/<id>/submit` | 已登录 | 至少 1 行已录实盘数，否则 `422 {code:1015}` |
| POST | `/api/wms/stocktakes/<id>/reject` | **admin** | `reason` 必填（写入 `remark` 后缀） |
| POST | `/api/wms/stocktakes/<id>/post` | **admin** | 过账；全单无差异 → `409 {code:1014}` |
| GET | `/api/wms/stocktakes/<id>/print` | 已登录 | `mode=blank`（空白盘点表）/ `mode=diff`（差异表，快照口径） |

批量录入（避免一百次 HTTP）：

```json
{ "lines":  [ {"detail_id": 12, "real_qty": 8}, {"detail_id": 13, "real_qty": 0} ],
  "append": [ {"barcode": "6900000000001", "real_qty": 3, "remark": "货架底层发现未入账"} ] }
```

- `lines[].real_qty` 传 `null` 表示「撤销录入」（回到未盘状态）；`0` 是合法实盘数（全盘亏）。
- `append[]` 用于「商品不在快照里」的盘盈：`book_qty` 记 0，商品须存在且启用（否则 `403/1004`、`404/1007`）。
- 明细归属强校验：`detail_id` 不属于本单 → `404 {code:1006}`（防跨单改数）。

### 12'''.3 快照与过账口径（最容易做错的一处）

| 项 | 规定 |
|---|---|
| 快照范围 | 建单时刻该仓库 `stock_current.qty <> 0` 的**启用**商品；零库存与停用商品不参与（盘盈走 `append`） |
| `book_qty` | 建单快照的账面数，**只用于差异报表** |
| `diff_qty` | 录入实盘数时算好存下：`real_qty - book_qty`（快照口径） |
| 过账数量 | `quantity = real_qty - 实时当前 qty`（**B 口径**，见 docs/14 4.3）：账最终一定等于实盘数 |
| 无差异行 | 不生成流水（`quantity != 0` 的 CHECK 也不允许 0） |
| 未盘行 | `real_qty IS NULL` → 跳过，不出流水 |
| 流水 | `type='ADJUST'`、`ref_type='STOCKTAKE'`、`ref_id=<盘点单 id>`、`client_msg_id=bill_msg_id(code, detail_id)` |
| 流水备注 | `盘点实盘 X（盘面 Y，期间变动 Z）`，Z 为建单后该商品实时变动量 |

### 12'''.4 异常处理

| 场景 | 处理 |
|---|---|
| 建单时仓库停用 | `422 {code:1008}` |
| 建单时该仓库无库存可盘 | `409 {code:1014}` |
| 提交时实盘行数为 0 | `422 {code:1015}` |
| 过账时全单无差异 | `409 {code:1014}` |
| 重复过账 / 重复提交 | `409 {code:1014}`（状态条件更新 0 行；**不是 200**，避免 UI 误判成功） |
| 过账时某商品被停用 | 整单回滚 `403 {code:1004}`（1004 在错误码表里固定映射 403） |
| 明细超过 200 行 | 分批过账（每批 200 行），外层事务仍整单原子 |
| 期间有出入库 | 不阻断；详情回 `period_changes` 计数，前端黄条提示「盘点期间有 N 笔出入库，已按实盘数调整」 |

### 12'''.5 与其他入口的关系

- **盘点单**：周期性、成批、有单据凭据、可打印（本节）。
- **单条调整** `POST /api/stock/adjust`：临时、单点纠错，保留不动（docs/14 4.9）。
- **已过账不可冲销**：ADJUST 类流水按既有规则不可红字冲销（`POST /api/stock/reverse` 走 1014 分支），
  要更正就**再开一张盘点单**。
- **单据总开关**：盘点单也算单据，`wms_bills_enabled=false` 时所有写操作 `409 {code:1018}`（历史单据仍可查）；
  此时临时纠错走「出入库 → 单条调整」，那条路径不经过单据层。

---

## 12''''. 单据打印（P0-3）—— 新增 2 个接口

> 依据 `docs/14-上线前功能补齐方案.md` 第 5 章。实现见 `desktop/api/print.py` + `services/bill_print_service.py`。
> ★ **服务端只产数据，不产 HTML**（红线 8：`views/` 只做静态服务与回退）。版式在前端
>   `web/src/components/PrintDocument.tsx` 用原生 table 渲染，A4 与针式两套 `@page`。

### 12''''.1 设计要点

| 项 | 规定 |
|---|---|
| 覆盖单据 | 采购订单 / 采购入库单 / 领用借还调拨单 / 盘点单（盘点单的 `mode=blank` 空白表、`mode=diff` 差异表） |
| 合计口径 | `total_qty` / `total_amount` 直接取单据表上的服务端值，**前端不重算**（与详情接口逐字一致） |
| 枚举 | 状态、单据类型全部转中文（`status_label` / `op_label`），打印页不出现 `SUBMITTED` 这类英文值 |
| 签字栏 | `SIGN_OFF` 是唯一真相，随数据下发；操作单按 `op_type` 区分（领用=领用人/仓管，调拨=调出/调入仓管） |
| 抬头 | `company_name` 留空时回退设备名（`server_name`）；`company_phone`/`company_address` 为空则不打该行 |
| 纸张 | `print_paper` 设置默认值（`a4`/`dot`），打印前可在页面临时切换；针式按 241×140mm、每页约 6 行 |
| 批量 | `ids` 逗号分隔，一次最多 **20** 张，超过 `422 {code:1002}` |

### 12''''.2 接口表

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/print/meta` | 已登录 | 抬头信息、纸张预设（`a4`/`dot` 含 `@page` 参数）、四类单据的签字栏、默认纸张 |
| GET | `/api/print/<biz>/<ids>` | 已登录 | 打印数据包：`{biz, docs[], total, company, printed_by, server_time}`；`mode=blank\|diff` 仅盘点单 |

`biz` ∈ `purchase-orders` / `purchase-inbounds` / `operation-orders` / `stocktakes`（未知 → `404/1006`）。

### 12''''.3 数据结构（单张单据）

```json
{
  "biz": "purchase-orders", "biz_label": "采购订单", "kind": "bill",
  "id": 12, "code": "PO202610180001", "name": "十月备货", "status": "DRAFT",
  "status_label": "草稿", "op_type": null, "op_label": "",
  "date": "2026-10-18", "date_label": "申请日期",
  "warehouse": "", "counterparty_label": "供应商", "counterparty": "示例供应商",
  "person_label": "申请人", "person": "管理员", "remark": "",
  "lines": [{"no": 1, "product_name": "可乐", "barcode": "6901028100001", "unit": "瓶",
             "qty": 3, "price": 5.0, "amount": 15.0, "remark": ""}],
  "summary": [{"label": "数量合计", "value": 3}, {"label": "金额合计", "value": 15.0}],
  "totals": {"total_qty": 3, "total_amount": 15.0},
  "sign_off": ["采购经办", "主管审批", "供应商确认"]
}
```

盘点单的 `kind` 为 `stocktake`，明细行额外带 `real_qty`/`diff_qty`，`summary` 为
`明细行数 / 已盘行数 / 差异行数`；`mode=blank` 时 `real_qty`/`diff_qty` 恒为 `null`（供现场手填）。

### 12''''.4 异常处理

| 场景 | 处理 |
|---|---|
| 未知 `biz` | `404 {code:1006}` |
| 单据不存在 | `404 {code:1006}` |
| `ids` 非整数 / 为空 | `422 {code:1002}` |
| 一次超过 20 张 | `422 {code:1002}`（提示分批打印） |
| 打印本身 | 纯只读，**不写任何表**、不改 `stock_current`、不产生流水 |

### 12''''.5 相关设置项（`settings` 白名单）

| key | 默认 | 说明 |
|---|---|---|
| `company_name` | 空 | 单据抬头；留空回退设备名 |
| `company_phone` | 空 | 打印页联系电话；空则不显示 |
| `company_address` | 空 | 打印页地址；空则不显示 |
| `print_paper` | `a4` | `a4` / `dot`（针式连续纸 241×140mm） |

---

## 12'''''. 销售订单 / 发货 / 销售退货 / 客户对账（P1-4）—— 新增 18 个接口

> 依据 `docs/14` 第 6 章。与采购侧的差异只有三处：多了「发货」步骤、销售订单必须带出库仓库、
> 多了只读的「客户对账单」。状态机、超发拦截、幂等键派生规则与采购完全一致。

### 12'''''.1 状态机

```
销售订单 SO：DRAFT ─submit→ SUBMITTED ─approve→ APPROVED ─ship(可多次)→ [发满自动] CLOSED
                    ↑            └reject→ REJECTED ─submit→ ...
                    └cancel→ CANCELED（admin，DRAFT/SUBMITTED/REJECTED）

销售退货 SR：DRAFT ─submit→ SUBMITTED ─approve（同步过账 IN）→ APPROVED
                    └reject→ REJECTED ─submit→ ...
                    └cancel→ CANCELED（admin，未过账前）
```

★ `APPROVED` **不等于已出库**：审批只改状态，必须在 `APPROVED` 之后调 `/ship` 才动库存。
★ 发满（每行 `shipped_qty == qty`）自动置 `CLOSED`，没有手工关闭入口。

### 12'''''.2 接口表

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/wms/sales-orders` | 已登录 | 列表；`status/customer_id/warehouse_id/date_from/date_to/keyword/page/page_size` |
| POST | `/api/wms/sales-orders` | 已登录 | 建单（DRAFT） |
| GET | `/api/wms/sales-orders/<id>` | 已登录 | 详情（含 `shipped_qty` / `remaining_qty`） |
| PUT | `/api/wms/sales-orders/<id>` | 已登录 | 改单（仅 DRAFT/REJECTED） |
| POST | `/api/wms/sales-orders/<id>/submit` | 已登录 | 提交审批 |
| POST | `/api/wms/sales-orders/<id>/approve` | admin | 审批通过（**不出库**） |
| POST | `/api/wms/sales-orders/<id>/reject` | admin | 驳回（`reject_reason` 必填） |
| POST | `/api/wms/sales-orders/<id>/ship` | 已登录 | **发货出库**（可多次，`shipment_id` 幂等） |
| POST | `/api/wms/sales-orders/<id>/cancel` | admin | 作废（`status=CANCELED` + `is_active=0`） |
| GET | `/api/wms/sales-returns` | 已登录 | 退货列表 |
| POST | `/api/wms/sales-returns` | 已登录 | 建退货单（`return_reason` 必填） |
| GET | `/api/wms/sales-returns/<id>` | 已登录 | 退货详情 |
| PUT | `/api/wms/sales-returns/<id>` | 已登录 | 改退货单（仅 DRAFT/REJECTED 且未过账） |
| POST | `/api/wms/sales-returns/<id>/submit` | 已登录 | 提交审批 |
| POST | `/api/wms/sales-returns/<id>/approve` | admin | 审批通过 **并同步过账 IN** |
| POST | `/api/wms/sales-returns/<id>/reject` | admin | 驳回 |
| POST | `/api/wms/sales-returns/<id>/cancel` | admin | 作废 |
| GET | `/api/wms/customer-statement` | 已登录 | 客户对账单；`partner_id`（必填）/`from`/`to`/`export=csv` |

### 12'''''.3 发货请求体（最容易做错的一处）

```jsonc
POST /api/wms/sales-orders/12/ship
{
  "shipment_id": "8b1f...-uuid-v4",          // ★必填，前端每点一次「发货」生成一次
  "lines": [
    { "detail_id": 34, "qty": 2 },           // detail_id 必须是本单的明细行（否则 1006）
    { "detail_id": 35, "qty": 1 }
  ]
}
```

- **幂等**：`client_msg_id = uuid5(单据号#shipment_id, detail_id)` 由单据派生；同一 `shipment_id`
  重放返回 `200 {code:0, data.shipment.duplicated:true}`，库存与 `shipped_qty` 都不再变化。
- **超发**：`qty > qty - shipped_qty` → `409 {code:1016}`，`data` 带 `requested/receivable`。
  权威判定是 `bump_shipped` 的条件更新（0 行即超发），服务层先做的友好提示只是第一道。
- **库存不足**：`422 {code:1009}`，整单回滚（流水不落、`shipped_qty` 不回写）。
- **部分命中**（一半 `client_msg_id` 已存在）→ `409 {code:1014}`，提示刷新后按剩余数量重发，
  绝不二次累加 `shipped_qty`。
- 响应体 = 最新单据详情 + `data.shipment = {shipment_id, duplicated, lines[]}`。

### 12'''''.4 客户对账单

- `partner_id` 必须是 `type='CUSTOMER'`（供应商 → `422/1002`）；`from`/`to` 缺省为「本月 1 日 ~ 今天」。
- 明细只读 `stock_logs` 中 `ref_type ∈ (SALES_OUT, SALES_RETURN)` 的流水，金额取账本 `amount`。
- `totals`：`out_qty/out_amount`、`return_qty/return_amount`、`net_qty/net_amount`（净额 = 出库 − 退货）、`line_count`。
- `export=csv` 输出 `utf-8-sig` + `\r\n`，`X-Row-Count` 头给出明细行数；**只读，不写表**。
- 明确**不做**：应收凭证、账龄分析、开票联动（docs/14 2.3 范围边界）。

### 12'''''.5 单据层联动

| 位置 | 变化 |
|---|---|
| `services/bill_no_service.py` | `SO` → `wms_sales_order`、`SR` → `wms_sales_return`（日流水 4 位） |
| `services/stock_service.py` | `BILL_REF_TYPES` 增 `SALES_OUT` / `SALES_RETURN`；`post_bill_lines` 增可选 `partner_type`（退货是 IN 但挂客户） |
| `GET /api/wms/approvals` | 待办新增 `type=SALES_ORDER` / `SALES_RETURN` |
| `GET /api/wms/export/<biz>` | 新增 `biz=sales-orders` / `sales-returns` |
| `GET /api/print/<biz>/<ids>` | 新增 `biz=sales-orders`（签字栏：销售经办 / 仓管 / **客户签收**） |
| 总开关 | `wms_bills_enabled=false` 时销售单据写操作 → `409/1018`（查询仍可用） |

### 12'''''.6 异常处理

| 场景 | 处理 |
|---|---|
| 客户不存在 / 已停用 | `404/1006` / `403/1004` |
| 往来单位是供应商 | `422/1002`（`data.errors.customer_id`） |
| 出库仓库不存在 / 已停用 | `404/1006` / `403/1008` |
| 商品停用 | `403/1004` |
| 明细为空 / 超过 200 行 | `422/1015` |
| 状态不允许该操作 | `409/1014` |
| 发货缺 `shipment_id` | `422/1002` |
| 超发 / 库存不足 | `409/1016` / `422/1009` |
| 退货原因为空 | `422/1002` |
| 退货客户与源单客户不一致 | `422/1002` |
| 单据功能已关闭 | `409/1018` |

---

## 13. 接口统计与版本策略

| 模块 | 数量 | 对应章节 |
|---|---|---|
| 连接与探测 | 3 | 2.1~2.3 |
| 账号 | 4 | 3.1~3.4 |
| 账号与设备 | 6 | 9.1~9.6 |
| 基础数据 | 11 | 4.1~4.8 |
| 库存业务 | 5 | 5.1~5.4 |
| 查询报表 | 7（含 CSV 导出、流水详情、操作员下拉） | 6.1~6.5 |
| 系统 | 9（设置读/写、网络设置、系统信息、二维码、备份×4） | 7.1~7.8 |
| 商品恢复 | 1 | 4.5b |
| 标签打印 | 3 | 10.1~10.2 |
| 离线授权 | 2 | 11.1~11.2 |
| WMS 采购单据 | 14 | 12'.2~12'.3 |
| WMS 通用操作单 | 8 | 12'.4 |
| WMS 待办与导出 | 2 | 12'.5~12'.6 |
| 批量导入导出 | 4 | 12''.1 |
| WMS 盘点单 | 9 | 12'''.2 |
| 单据打印 | 2 | 12''''.2 |
| 销售订单 / 退货 / 对账 | 18 | 12'''''.2 |
| **合计** | **108** | — |

> 数字口径：`desktop/api/` 里注册的路由规则数（`/api/settings` 的 PUT+POST 计 1 个逻辑接口）。
> 改接口必须同步本表与 `01-产品需求文档.md` 8.3 —— 表里没有的接口等于不存在。

版本策略：自用项目**不做 URL 版本化**。兼容性靠规则保证 ——「只加字段不删字段、不改字段语义」；确需破坏性变更时递增 `protocol_version` 并在 `/api/ping` 暴露，APP 侧对 `protocol_version` 主版本不匹配时提示「请更新电脑端程序」。
