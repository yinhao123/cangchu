---
description: "Day1 上午 —— 登录鉴权 + 商品/仓库/往来单位 CRUD + 仪表盘"
argument-hint: "[可选：只做其中某几项，如 '登录+商品']"
allowed-tools: Read, Write, Edit, Bash
---

承接已通过的 D0 门禁。**本阶段只做下面 6 项，不做出入库、不做 UDP。**

## 先读
`CODEBUDDY.md` → `docs/01-产品需求文档.md` 第 3、5.1、5.2、5.3、7、8.1~8.4、10 章 → `docs/03-API接口规范.md` 第 3、4 章 → `docs/04-开发规约与陷阱.md` 的 T-05/T-11/T-19/T-20。

## 任务

1. **认证与会话**（PRD 5.1、3.3）
   - `before_request` 分派：白名单（`/api/ping`、`/api/login`、`/healthz`、`/static/*`）放行；`/api/*` 走 `X-Auth-Token`（查 `api_tokens.token_hash`，SHA-256 比对，校验 `revoked_at`/`expires_at`）；其余走 Flask-Login Session。
   - `require_login` / `require_admin` 装饰器；`must_change_password=1` 时除改密外一律 403。
   - 密码：`pbkdf2:sha256:600000`；新密码 ≥8 且含字母数字；种子 `admin/admin123` 且 `must_change_password=1`。
   - 登录保护：同用户名 5 次/同 IP 20 次失败锁 15 分钟 → `429 {code:1005}` + `data.retry_after`。错误文案统一「账号或密码错误」（不区分账号是否存在）。
   - 接口：`POST /api/login`（同用户同 `device_name` 复用有效 Token）、`POST /api/logout`、`GET /api/me`、`POST /api/me/password`。网页 `/login`、`/logout`。
2. **参数校验层** `services/validators.py`：集中实现必填/类型/范围/长度/字符集；条码规范化 = `strip` + 全角转半角 + 去空格，3~48 位 `[0-9A-Za-z-]`，**不合规直接 422，不自动修正**。所有接口共用，禁止在各 api 里重复写校验。
3. **商品管理**（PRD 5.3）：Repository + Service + 网页 CRUD + API（`GET/POST/PUT/DELETE /api/products`、`GET /api/products/by-barcode/<barcode>`）。
   - 条码唯一冲突 → `409 {code:1010}` + `data.conflict`（回传占用它的商品 id/name/is_active）。**软删除的商品条码永久占用，不得复用**。
   - `PUT` 带 `expected_updated_at` 乐观校验，不匹配 → `409 {code:1011}`。
   - `barcode` 不传=不改，传 `null`=清空（语义必须在代码与文档同时锁定）。
   - 删除默认软删（`is_active=0, deleted_at`）；物理删除仅限「无任何流水」+ admin。
   - 无条码商品自动生成内码 `S` + 10 位补零 id。
   - 列表页：搜索（`barcode` 精确优先、`name` 模糊）、分类筛选、分页 20/50/100、`sort` 白名单。
4. **仓库管理**：CRUD + 停用；删除有库存余额的仓库 → `409` 并列出原因。
5. **往来单位**：`partners` CRUD（`type` 枚举、`(name,type)` 唯一）。
6. **仪表盘**（PRD 5.2）：4 个聚合查询**各自走索引，禁止模板里循环查库（T-22）**；「今日」用 `substr(created_at,1,10)=?`；`SUM(qty)` **含负数原样展示并标红**；预警列表 ≤50 行；服务状态区显示实际 IP:端口、广播状态、最近备份时间。查询失败 → 页面黄条「部分数据加载失败：原因」，其余卡片照常渲染（不整页 500）。

## 测试（本阶段必须新增）

UT-08、UT-09、UT-10、UT-11、UT-14、UT-15、UT-17 + `test_log_redaction`。再加：条码规范化用例、软删条码复用被拒、乐观锁冲突、`barcode` 传 null 与不传的差别、登录锁定倒计时。

## 门禁

`pytest -q` 全绿；`runserver` 后人工过一遍：登录 → 强制改密 → 新增商品 → 扫码框输条码回车带出商品 → 建仓库 → 仪表盘数字正确。

## 输出

文件清单（+ 一句话职责）、`pytest -q` 真实输出、与 PRD 的偏差清单（**不要自行拍板**，列出来问我）。

补充说明：$ARGUMENTS
