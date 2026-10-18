# 13 · 参照 `yudao-module-wms` 整合 WMS 能力 —— 方案与实施计划

> 本文是「把 `D:\ruoyi\ruoyi-office\yudao-module-wms`（Java / Spring Boot / MySQL）的业务能力，整合进 `cangchu`（ScanableSelf，Python / Flask / SQLite）」的落地方案。
> 本文只做**设计与计划**，不直接改代码；所有 DDL / 接口 / 文件落位都必须先过 `docs/02`、`docs/03`、`docs/04` 的一致性约束，再动实现。

---

## 0. 前置声明与假设

| 项 | 结论 |
|---|---|
| 「我的系统」 | `https://github.com/yinhao123/cangchu`（ScanableSelf WMS MVP，Python 3.8 + Flask + waitress + SQLite + React/antd SPA + Android 契约） |
| 「参照对象」 | `D:\ruoyi\ruoyi-office\yudao-module-wms`（芋道 yudao 生态的一个 WMS 模块，53 个 Java 文件） |
| 整合方式 | **能力移植 + 同构重写**（路线 A，本文主线）：把参照对象的**业务模型与单据流程**翻译成 cangchu 的技术栈，而不是引入 Java/Spring |
| 备选方式 | 路线 B（第 9 章）：把 `yudao-module-wms` 挂进 `ruoyi-office` 跑起来，两套系统通过 API 对接。**仅在你需要 Flowable 审批流 / 资产台账 / 多租户时才考虑** |

**为什么不能"直接搬代码"**：两端技术栈无任何重合（Java/Spring/MyBatis-Plus/MySQL/Flowable ↔ Python/Flask/裸 SQL/SQLite/无流程引擎），且参照对象缺少建表 SQL、无前端、`yudao-server/pom.xml` 中依赖被注释（当前单体模式根本不加载它）。因此它是**业务参考蓝本**，不是可复用组件。

---

## 1. 两端能力对照（差距分析）

### 1.1 技术栈对照

| 维度 | yudao-module-wms（参照） | cangchu（目标） | 整合动作 |
|---|---|---|---|
| 语言/框架 | Java 17 + Spring Boot + MyBatis-Plus | Python 3.8.10 + Flask（无 ORM） | 全部重写为 `services/` + `models/*_repo.py` |
| 数据库 | MySQL（`wms_*` 表，逻辑外键，无 DB 约束） | SQLite（外键 RESTRICT + 触发器保护） | 建真实外键 + 索引，DDL 见第 4 章 |
| 主键 | `Long id`（MP 自增 / Oracle Sequence） | `INTEGER PRIMARY KEY AUTOINCREMENT` | 保持 cangchu 约定 |
| 时间 | `LocalDateTime` | `TEXT` ISO-8601 东八区，唯一出口 `services/timeutil.py:now_iso()` | 强制走 `now_iso()`（T-11） |
| 金额 | `BigDecimal` | `REAL`（既有约定） | 保持 `REAL`；金额合计只在**单据**上存，账本侧沿用 `unit_price/amount` |
| 事务 | `@Transactional` | 单连接事务 + SAVEPOINT 批量隔离（T-08） | 单据 + 流水必须同事务 |
| 幂等 | **无** | `stock_logs.client_msg_id` UNIQUE（★生命线） | 单据写流水时强制传 `client_msg_id`（T-09） |
| 审批 | BPM（Flowable）`wms_purchase_order_flow` | **无流程引擎** | 自研轻量状态机，见第 5 章 |
| 权限 | `@PreAuthorize("wms:xxx:create")` 细粒度 | `require_login` / `require_admin` 两级 | 降级为两级（第 6.4 节） |
| 导出 | EasyExcel `.xls` | CSV（`utf-8-sig` BOM，T-10） | 用 `report_service` 现成 CSV 出口 |
| 多租户/数据权限 | `starter-biz-tenant`、`starter-biz-data-permission` | 单机自用，无租户 | 直接砍掉 |
| 资产模块 | `assetInfoApi.batchSaveInfo()` 审批通过写资产 | 无资产模块 | P1 砍掉，P4 可选（第 4.7 节） |

### 1.2 业务能力对照

| 参照对象能力 | 表 | cangchu 现状 | 差距 |
|---|---|---|---|
| 仓库（树形，编码/类型/地址） | `wms_warehousing` | `warehouses`（扁平，仅 name/address/sort_order/is_active） | **需升级为树** |
| 采购订单（主+明细，含供应商、金额合计） | `wms_purchase_order` + `wms_purchase_order_detail` | 无单据概念，只有直接出入库流水 | **新增** |
| 采购入库单 | `wms_purchase_in_warehousing` | 无（入库即一条 `stock_logs`） | **新增** |
| 领用/退库/归还/借用/调拨单 | `wms_goods_common_operation_order` | 无（`stock_logs.type` 只有 IN/OUT/ADJUST） | **新增** |
| 出入库明细（含资产属性、序列号、存放位置） | `wms_goods_warehousing_detail` | `stock_logs`（已含快照字段，但无序列号/资产属性） | **新增明细表**（可选补 `serial_number`） |
| 审批流 | `processInstanceId` + `processStatus` + BPM 回调 | 无 | **新增状态机**（简化版） |
| 库存增减 | **参照对象不做**（`PurchaseInWarehousingServiceImpl` 是纯 CRUD） | ★核心能力（`stock_service` + `stock_logs` + `stock_current`） | **cangchu 更强，必须保留并承接** |

> ★ 最重要的一条结论：**参照对象只有"单据"，没有"账本"；cangchu 只有"账本"，没有"单据"。**
> 整合的本质 = 在 cangchu 的账本之上，长出一层单据；任何单据一旦生效，必须按 cangchu 的铁律落到 `stock_logs` + `stock_current`。绝不能照搬参照对象"单据与库存脱节"的做法。

---

## 2. 目标架构：单据层 + 账本层

```
                    ┌──────────────── 单据层（本次新增） ────────────────┐
  采购订单 PO ──审批──▶ 采购入库单 PI ──过账──▶ 库存
       │                                          ▲
       │                    通用操作单 GO（领用/退库/归还/借用/调拨）──过账──┘
       └── 明细行 (product_id / qty / price) ──────┘
                                │
                     【过账边界：唯一写库存的入口】
                                ▼
                    ┌──────────────── 账本层（既有，不可变） ────────────────┐
                    stock_logs (type IN/OUT/ADJUST, before/after 快照)
                    stock_current (派生，可全量重算 recalc-stock)
                    └──────────────────────────────────────────────────────┘
```

**铁律（写入 `10-inventory-rules.md` 的新增条款）**

1. 单据层**禁止**直接写 `stock_current`；只能调 `services/stock_service.py` 的既有入口（`in/out/adjust/reverse`）。
2. 单据行过账 → 生成 `stock_logs`，并在**同一事务**内更新 `stock_current`（T-06/T-07/T-08）。
3. 单据作废/更正 → 走**红字冲销**（`reverse`），**禁止**物理删除单据（单据是可改的，流水是不可变的）。
4. 每张单据过账产生的流水必须带 `ref_type` + `ref_id`，保证「单据 → 流水」双向可追溯。
5. 单据过账接口必须接收 `client_msg_id`，重复提交返回首次结果，不报错。

---

## 3. 整合范围（做什么 / 不做什么）

### 做（P0~P5）

- 仓库升级为树形（编码、类型、上下级）
- 采购订单（草稿→提交→审批→关闭）+ 明细
- 采购入库单（引用采购订单，审批通过过账为 IN 流水）
- 通用操作单：领用 / 退库 / 归还 / 借用 / 调拨 + 明细
- 轻量审批状态机（admin 审批，无需 Flowable）
- 单据列表/详情/审批/导出（CSV）
- 前端 4 个新页面 + 导航

### 不做（明确砍掉）

| 砍掉项 | 理由 |
|---|---|
| Flowable / BPM 流程引擎 | 单机自用，引入成本 >> 收益；用状态机替代 |
| 多租户 `tenant_id`、数据权限 | cangchu 无租户概念 |
| 公司/部门/岗位（`companyId`/`deptId`/`adminDeptId`…） | cangchu 是单人/小团队工具，只有 `users`；保留 `remark` 承载 |
| 资产台账 `assetInfoApi`、资产折旧/残值率 | P1 不做；若要做见 4.7 |
| Feign / MQ / 本地事件三种流程回调 | 无流程引擎即无回调；状态变更在 service 内同步完成 |
| Excel 导出 | 沿用既有 CSV（`utf-8-sig`，T-10） |
| 附件字段（`purchaseOrderFile` 等） | 本地单机无对象存储；若需要，存相对路径 + 后续再加 |

---

## 4. 数据模型：新增表与迁移

> 约定沿用 `docs/02`：时间 `TEXT` ISO-8601 东八区、布尔 `INTEGER 0/1`、枚举大写、外键显式声明。
> 迁移文件：`desktop/db/migrations/0002_wms_bills.sql`（**只追加，禁止改 0001**），并与 `desktop/db/schema.sql` 同步（两者不一致时以迁移顺序执行结果为准）。

### 4.1 仓库升级为树（`warehouses` 加列）

★ **只用 `ALTER TABLE ADD COLUMN`，禁止「建新表-拷贝-改名」四步法**（实施时修正了原方案）：
迁移器 `core/migrator.py` 开连接时带外键约束，此时 `DROP TABLE warehouses` 会触发
`stock_current` 的 `ON DELETE CASCADE` —— **库存余额会被静默清空**。
`ADD COLUMN` 在全版本 SQLite 可用、不动历史行，是本项目唯一允许的加列方式。

```sql
-- 0002_wms_bills.sql：warehouses 升级为两级树（历史行自动 parent_id=0 / level=1）
ALTER TABLE warehouses ADD COLUMN code      TEXT;
ALTER TABLE warehouses ADD COLUMN parent_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE warehouses ADD COLUMN level     INTEGER NOT NULL DEFAULT 1;
ALTER TABLE warehouses ADD COLUMN category  TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_warehouses_code   ON warehouses(code);
CREATE INDEX        IF NOT EXISTS idx_warehouses_parent ON warehouses(parent_id, is_active);
```

> 删除校验（应用层 409）：有子节点、或 `stock_current.qty != 0` 时禁止删除 —— 对应参照对象的 `WAREHOUSING_EXITS_CHILDREN` / `WAREHOUSING_PARENT_IS_CHILD`，再加 cangchu 自有的「有余额禁止删」。

### 4.2 采购订单

```sql
CREATE TABLE IF NOT EXISTS wms_purchase_order (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    code                TEXT NOT NULL UNIQUE,                  -- PO + yyyymmdd + 4 位流水，事务内 SQL 现算
    name                TEXT NOT NULL,
    applicant_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    partner_id          INTEGER REFERENCES partners(id) ON DELETE RESTRICT,   -- 供应商（type='SUPPLIER'）
    partner_name_snap   TEXT NOT NULL,                         -- ★快照，防改名失真
    applicant_date      TEXT NOT NULL,                         -- 申请日期（now_iso）
    total_qty           INTEGER NOT NULL DEFAULT 0,            -- 采购物品合计
    total_amount        REAL NOT NULL DEFAULT 0,               -- 采购金额合计（明细汇总，服务层算）
    status              TEXT NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CLOSED','CANCELED')),
    submit_at           TEXT,
    approve_at          TEXT,
    approver_id         INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reject_reason       TEXT,
    remark              TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)), -- 0=作废（软删，永不物理删）
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL                          -- 兼作乐观锁版本（PUT 必须回传比对）
);
CREATE INDEX IF NOT EXISTS idx_po_status ON wms_purchase_order(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_po_partner ON wms_purchase_order(partner_id);

CREATE TABLE IF NOT EXISTS wms_purchase_order_detail (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id            INTEGER NOT NULL REFERENCES wms_purchase_order(id) ON DELETE CASCADE,
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name_snap   TEXT NOT NULL,                         -- ★快照
    barcode_snap        TEXT,
    unit_snap           TEXT NOT NULL,
    price               REAL NOT NULL DEFAULT 0 CHECK (price >= 0),
    qty                 INTEGER NOT NULL CHECK (qty > 0),
    amount              REAL NOT NULL DEFAULT 0,               -- = price * qty，冗余便于汇总
    received_qty        INTEGER NOT NULL DEFAULT 0,            -- 累计入库数量（≤ qty，服务层校验）
    sort_order          INTEGER NOT NULL DEFAULT 0,
    remark              TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pod_order ON wms_purchase_order_detail(order_id, sort_order);
```

### 4.3 采购入库单

```sql
CREATE TABLE IF NOT EXISTS wms_purchase_in_warehousing (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    code                TEXT NOT NULL UNIQUE,                  -- PI + yyyymmdd + 4 位流水
    name                TEXT NOT NULL,
    order_id            INTEGER REFERENCES wms_purchase_order(id) ON DELETE RESTRICT, -- 可空=无源单直接入库
    order_code_snap     TEXT,
    warehouse_id        INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
    partner_id          INTEGER REFERENCES partners(id) ON DELETE RESTRICT,
    in_date             TEXT NOT NULL,
    total_qty           INTEGER NOT NULL DEFAULT 0,
    total_amount        REAL NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CANCELED')),
    posted_at           TEXT,                                  -- ★过账时间（写 stock_logs 的时刻）
    posted_by           INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    submit_at           TEXT, approve_at TEXT,
    approver_id         INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reject_reason       TEXT,
    remark              TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pi_status ON wms_purchase_in_warehousing(status, in_date DESC);
CREATE INDEX IF NOT EXISTS idx_pi_order  ON wms_purchase_in_warehousing(order_id);
```

### 4.4 通用操作单（领用/退库/归还/借用/调拨）

```sql
CREATE TABLE IF NOT EXISTS wms_goods_operation_order (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    code                TEXT NOT NULL UNIQUE,                  -- GO + yyyymmdd + 4 位流水
    name                TEXT NOT NULL,
    op_type             TEXT NOT NULL CHECK (op_type IN ('RECEIVE','RETURN_STOCK','RETURN_BORROW','BORROW','TRANSFER')),
    -- RECEIVE 领用 / RETURN_STOCK 退库 / RETURN_BORROW 归还 / BORROW 借用 / TRANSFER 调拨
    order_id            INTEGER REFERENCES wms_purchase_order(id) ON DELETE RESTRICT,         -- 可选来源采购订单
    in_warehousing_id   INTEGER REFERENCES wms_purchase_in_warehousing(id) ON DELETE RESTRICT,-- 可选来源入库单
    warehouse_id        INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,        -- 源仓库
    to_warehouse_id     INTEGER REFERENCES warehouses(id) ON DELETE RESTRICT,                 -- 调拨目标仓（其余类型为空）
    operator_user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,             -- 领用/借用/归还人
    partner_id          INTEGER REFERENCES partners(id) ON DELETE RESTRICT,                   -- 可选往来单位
    op_date             TEXT NOT NULL,
    expect_return_date  TEXT,                                  -- 借用必填
    total_qty           INTEGER NOT NULL DEFAULT 0,
    total_amount        REAL NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','DONE','REVERSED','CANCELED')),
    posted_at           TEXT, posted_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    submit_at           TEXT, approve_at TEXT,
    approver_id         INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reject_reason       TEXT,
    reversed_at         TEXT,                                  -- 冲销时刻（实施时定稿：不做冲销单实体）
    reversed_by         INTEGER REFERENCES users(id) ON DELETE RESTRICT,  -- 执行冲销的操作人
    remark              TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_go_status ON wms_goods_operation_order(status, op_date DESC);
CREATE INDEX IF NOT EXISTS idx_go_type   ON wms_goods_operation_order(op_type, status);

CREATE TABLE IF NOT EXISTS wms_goods_operation_detail (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id            INTEGER NOT NULL REFERENCES wms_goods_operation_order(id) ON DELETE CASCADE,
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name_snap   TEXT NOT NULL,
    barcode_snap        TEXT,
    unit_snap           TEXT NOT NULL,
    qty                 INTEGER NOT NULL CHECK (qty > 0),
    price               REAL,                                  -- 可空（领用/借用可能不计价），IN 取成本价 OUT 取售价
    amount              REAL,
    serial_number       TEXT,                                  -- 序列号（参照对象的资产明细字段，按需）
    is_join_asset       INTEGER NOT NULL DEFAULT 0 CHECK (is_join_asset IN (0,1)), -- 是否计入资产（P1 只存标记，不建资产表）
    sort_order          INTEGER NOT NULL DEFAULT 0,
    remark              TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_god_order ON wms_goods_operation_detail(order_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_god_sn    ON wms_goods_operation_detail(serial_number);
```

### 4.5 流水溯源（`stock_logs` 加列）

`trg_logs_immutable` 只锁定账目语义列，**新增列不在锁定列表内**，因此可直接 `ALTER TABLE ADD COLUMN`（不触碰历史行）：

```sql
ALTER TABLE stock_logs ADD COLUMN ref_type TEXT;   -- 'PURCHASE_IN' | 'RECEIVE' | 'RETURN_STOCK' | 'RETURN_BORROW' | 'BORROW' | 'TRANSFER_OUT' | 'TRANSFER_IN'
ALTER TABLE stock_logs ADD COLUMN ref_id   INTEGER; -- 对应单据 id
CREATE INDEX IF NOT EXISTS idx_logs_ref ON stock_logs(ref_type, ref_id);
```

> 这一步是**整套整合的枢纽**：有了它，「单据 → 流水 → 报表」三者才能对齐，也才能做「单据冲销 → 流水冲销」的一致性校验。

### 4.6 单据类型 → 账本动作映射表（过账规则，唯一权威）

| 单据 | op_type | 过账动作 | 仓库 | partner | 校验 |
|---|---|---|---|---|---|
| 采购入库单 | — | `IN`（1 条/明细行） | 入库单 `warehouse_id` | 订单供应商 | 明细行 `received_qty + qty ≤ 订单 qty`；审批通过才过账 |
| 领用单 | `RECEIVE` | `OUT` | `warehouse_id` | 可选 | 扣减后 `qty ≥ 0`（负库存禁止，`stock_service.decrease_stock` rowcount 判定） |
| 退库单 | `RETURN_STOCK` | `IN` | `warehouse_id` | 可选 | 无 |
| 借用单 | `BORROW` | `OUT` | `warehouse_id` | 可选 | `expect_return_date` 必填 |
| 归还单 | `RETURN_BORROW` | `IN` | `warehouse_id` | 可选 | 建议引用原借用单（P2 可选） |
| 调拨单 | `TRANSFER` | `OUT`（源仓）+ `IN`（目标仓） | `warehouse_id` → `to_warehouse_id` | — | 两条流水同一事务（SAVEPOINT，T-08）；目标仓 ≠ 源仓 |

所有过账统一调用 `services/stock_service.py` 既有方法，传入 `client_msg_id`（格式建议 `<单据号>-<明细行id>`，保证重试幂等）。

### 4.7 资产（可选，P4）

参照对象在采购订单审批通过后把明细转成资产。cangchu 无资产模块，P1 只在 `wms_goods_operation_detail.is_join_asset` 打标。若后续要真做，再单独开一篇文档建 `assets` 表（含 `serial_number` / `purchase_date` / `use_account_id`），**不要**提前把参照对象那 40 多个资产字段搬进明细表（那是把简单项目复杂化的典型错误，见 `01-PRD` 第 0 章）。

---

## 5. 审批：用状态机替代 Flowable

参照对象：`submitPurchaseOrder()` → `BpmProcessInstanceApi.createProcessInstance(key='wms_purchase_order_flow')` → 回调 `updateFlowDataByKey()` → 状态 `RUNNING/APPROVE` → 写资产。

cangchu 等价实现（**同步、无回调、无 MQ**）：

```
DRAFT ──submit──▶ SUBMITTED ──approve──▶ APPROVED ──post──▶ (采购订单: CLOSED / 入库单: posted_at 落值)
  │                   │
  │                   └──reject──▶ REJECTED ──edit──▶ DRAFT
  └──cancel──▶ CANCELED
```

- 审批人：`require_admin`（两级权限，第 6.4 节）。
- 审批动作在 `services` 内**同步完成**：改状态 + 过账 + 回写明细累计量，全部在同一事务。
- 不引入 `process_instance_id`；若将来真要接 Flowable（路线 B），再增加该字段做映射。
- 状态变更必须写日志（`core/logging_setup.py`，含 `trace_id`），审计需求由 `stock_logs` + 单据状态字段共同满足。

---

## 6. 接口设计（沿用 cangchu 契约风格）

### 6.1 约定

- 前缀 `/api/wms/...`；认证 `X-Auth-Token`（与既有接口一致）；响应包络 `core/response.py:ok()/fail()`；错误走 `core/errors.py:BizError`。
- 分页/列表沿用既有查询参数风格；写操作 `POST/PUT`，参照对象的 `/create` `/update` `/delete` `/delete-list` 语义统一改成 REST 风格 + cangchu 既有命名（以 `docs/03` 现有 51 接口为准，**不改既有接口**）。
- **单据不做物理删除**：`DELETE` 一律改为「作废」（`is_active=0`），已过账单据只能「冲销」。

### 6.2 接口清单（新增约 22 个）

| 分组 | 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|---|
| 仓库 | GET | `/api/wms/warehouses/tree` | login | 树形列表 |
| 仓库 | POST/PUT | `/api/wms/warehouses` | admin | 新增/编辑（父级存在、禁自环、名称/编码唯一） |
| 仓库 | DELETE | `/api/wms/warehouses/<id>` | admin | 有子节点或有余额 → 409 |
| 采购订单 | GET | `/api/wms/purchase-orders` | login | 分页（状态/供应商/日期区间） |
| 采购订单 | GET | `/api/wms/purchase-orders/<id>` | login | 含明细 |
| 采购订单 | POST / PUT | `/api/wms/purchase-orders` | login / admin | 新增草稿 / 编辑（仅 DRAFT/REJECTED 可改） |
| 采购订单 | POST | `/api/wms/purchase-orders/<id>/submit` | login | 提交（DRAFT→SUBMITTED） |
| 采购订单 | POST | `/api/wms/purchase-orders/<id>/approve` | admin | 通过（→APPROVED）/ 驳回（→REJECTED，reject_reason 必填） |
| 采购订单 | POST | `/api/wms/purchase-orders/<id>/cancel` | admin | 作废（未过账才可） |
| 采购入库 | GET | `/api/wms/purchase-inbounds` / `<id>` | login | 分页 / 详情（含明细） |
| 采购入库 | POST / PUT | `/api/wms/purchase-inbounds` | login | 新增（可引用订单带出明细）/ 编辑 |
| 采购入库 | POST | `/api/wms/purchase-inbounds/<id>/submit` \| `/approve` \| `/post` | admin | 提交 / 审批 / **过账写库存** |
| 操作单 | GET | `/api/wms/operation-orders` / `<id>` | login | 分页（按 op_type 过滤）/ 详情 |
| 操作单 | POST / PUT | `/api/wms/operation-orders` | login | 新增 / 编辑（五种 op_type 共用一套） |
| 操作单 | POST | `/api/wms/operation-orders/<id>/submit` \| `/approve` \| `/post` | admin | 过账按 4.6 映射表 |
| 操作单 | POST | `/api/wms/operation-orders/<id>/reverse` | admin | 红字冲销（生成反向流水 + `reversed_by` 指针） |
| 导出 | GET | `/api/wms/export/<biz>` | login | CSV（`utf-8-sig` BOM，T-10），复用 `report_service` |

### 6.3 错误码（新增段，避免与既有冲突）

参照对象的 `2_101_xxx ~ 2_106_xxx` 是 yudao 的编码规则，**不要照搬**。cangchu 侧在 `core/errors.py` 追加一段（**具体起始号以 `docs/03` 现有已用区间为准，由实现时确认后填入**）：

已确定（顺延既有 1001~1013 区间，写入 `core/errors.py` + `docs/03` 第 12'.4 节）：

| 语义 | code | HTTP |
|---|---|---|
| 单据不存在 | 1006（复用） | 404 |
| **单据状态不允许该操作**（含已过账不可改） | **1014** | 409 |
| **单据明细非法**（空明细 / 行非对象 / direction 非法 / 超 200 行） | **1015** | 422 |
| **超出单据可收（可发）数量** | **1016** | 409 |
| **仓库层级非法**（父级不存在 / 超两级 / 改挂上级 / 有子库位禁停用） | **1017** | 409 |
| 库存不足（负库存） | 1009（复用） | 422 |
| `client_msg_id` 缺失 | 1002（复用，T-09） | 422 |

### 6.4 权限

沿用两级，不做 `wms:xxx:create` 细粒度：

- 查询/新增/编辑/提交：`require_login`
- 审批 / 过账 / 作废 / 冲销 / 仓库增删改：`require_admin`
- 抢手场景（手机上直接扫码出入库）**不走单据**，仍走既有 `/api/stock/in|out`，单据只在电脑端浏览器使用 —— 保证 MVP 的高频路径不被拖慢。

---

## 7. 代码落位（严格贴合 `docs/06` 分层）

```
desktop/
├── db/
│   ├── schema.sql                       # 同步追加本次 DDL
│   └── migrations/0002_wms_bills.sql    # ★新增，禁止改 0001
├── models/
│   ├── warehouse_repo.py                # ★改：树形（parent_id/level/code/category）
│   ├── stock_repo.py                    # ★改：stock_logs 写入带 ref_type/ref_id
│   ├── purchase_order_repo.py           # ★新增（主+明细，事务内）
│   ├── purchase_inbound_repo.py         # ★新增
│   └── operation_order_repo.py          # ★新增（主+明细）
├── services/
│   ├── bill_no_service.py               # ★新增：单据号生成（PO/PI/GO + 日期 + 流水，事务内 SQL 现算）
│   ├── purchase_order_service.py        # ★新增：CRUD + 状态机
│   ├── purchase_inbound_service.py      # ★新增：CRUD + 过账（调 stock_service.in）
│   ├── operation_order_service.py       # ★新增：CRUD + 过账（调 stock_service.in/out，调拨双流水）
│   ├── validators.py                    # ★改：补单据级校验（数量、状态流转、调拨仓不同）
│   ├── stock_service.py                 # ★只扩不改：新增 ref_type/ref_id 透传，禁止改既有语义
│   └── report_service.py                # ★改：单据导出 CSV
├── api/
│   ├── wms_warehouses.py                # ★新增
│   ├── wms_purchase.py                  # ★新增（订单 + 入库）
│   └── wms_operations.py                # ★新增（领用/退库/归还/借用/调拨）
└── tests/
    ├── test_wms_purchase.py             # ★新增：状态机 + 过账 + 幂等
    ├── test_wms_operations.py           # ★新增：五种类型过账映射 + 调拨双流水 + 冲销
    └── test_wms_warehouse_tree.py       # ★新增：树校验
web/src/
├── api/wms.ts                           # ★新增（挂在既有 client.ts 出口上）
├── pages/{PurchaseOrders,PurchaseInbounds,OperationOrders,WarehouseTree}.tsx
└── layout/nav.ts                        # ★改：加「采购 / 出入库单据 / 仓库」菜单
```

**分层铁律**：`api/` 薄层（取参 → 校验 → 调 service → 包络）；`services/` 是事务边界，不碰 HTTP、不 `sqlite3.connect()`；`models/` 是唯一 SQL 出口（参数化、无 `SELECT *`，T-05/T-19）。

---

## 8. 数据迁移、回滚与验收

### 8.1 迁移

1. 迁移前**强制自动备份**（`docs/02` 6.3 / `backup_service`，`Connection.backup()` 在线快照）。
2. 单事务执行 `0002_wms_bills.sql`，成功写 `schema_meta(version=2)`；失败**整体回滚并终止启动**。
3. `warehouses` 四步法重建后，立即跑 `PRAGMA foreign_key_check` + `docs/02` 第 5 章的 4 条不变式检查。

### 8.2 回滚

- 代码回滚：单据功能用设置项 **`wms_bills_enabled`**（`settings` 表，`value_type='bool'`，默认 `true`）做开关：
  关闭后所有单据写操作返回 `409 {code:1018}`，历史单据仍可查，`stock_logs`/`stock_current` 一行不动，
  等于回到纯流水模式，**不影响既有出入库**。
- 数据回滚：恢复迁移前自动备份；因为旧库没有单据数据，回滚是干净的。
- 已过账单据**不可回滚数据**（流水不可变），只能走冲销。

### 8.3 验收清单（可勾选）

- [ ] `pytest -q` 全绿；新增用例覆盖：五种 op_type 过账、调拨双流水同事务、重复提交幂等、超收拦截、负库存拦截
- [ ] 单据过账后 `docs/02` 第 5 章 4 条不变式检查均为 0 行
- [ ] `stock_logs.ref_type/ref_id` 与单据一一对应，无孤儿（新增第 5 条不变式：`SELECT * FROM stock_logs WHERE ref_type IS NOT NULL AND ref_id IS NULL` 期望 0 行）
- [ ] `python manage.py recalc-stock` 后 `stock_current` 与流水聚合一致
- [ ] 作废/冲销路径：流水只增不改，`reversed_by` 指针正确
- [ ] 前端：Chrome/Edge 109 实测（Win7 线），antd v5 `:where()` 无样式丢失
- [ ] 导出 CSV 用 Excel 打开中文不乱码（BOM）
- [ ] 打包：`pyinstaller ScanableSelf.spec` 通过，新增模块已进 `hiddenimports`
- [ ] `docs/02` / `docs/03` / `docs/06` 与代码四方对齐 → 跑 `/contract-check`

---

## 9. 备选路线 B：挂进 ruoyi-office 做双系统对接（仅在特定条件下选）

**适用条件**：你确实需要 Flowable 可视化审批流、资产台账、多租户/数据权限，或团队已经在 yudao 生态里。

**需要做的事**（参照对象当前并不"开箱可用"）：

1. 取消 `yudao-server/pom.xml:57-62` 里 `yudao-module-wms-server` 依赖的注释。
2. 补建 6 张 `wms_*` 表（`sql/` 下目前**没有任何 wms 脚本**，需按 DO 字段反推 DDL）、补菜单与 `wms:*` 权限 SQL。
3. 补前端（当前 `yudao-ui` 下 `wms` **0 命中**，需全新开发）。
4. 补 BPM 流程定义 `wms_purchase_order_flow`（`FlowCodeEnum` 已声明该 key，但流程模型需自行部署）。
5. 硬依赖 `yudao-module-bpm-api`（审批）与 `yudao-module-asset-api`（审批通过写资产）——这两个模块必须在，否则要改 `submitPurchaseOrder` 与三个 `process/*` 监听器。
6. 错误码段 `2_101_xxx~2_106_xxx` 与目标系统冲突检查。
7. 另写一套 cangchu ↔ yudao 的同步接口（局域网 HTTP + Token），并处理双写一致性。

**代价**：多一套 Java 运行环境、MySQL、Redis、Nacos；违背 cangchu「零成本、单机、Win7 可跑、0 元运维」的核心定位（见 `01-PRD` 第 0 章）。

> **建议：除非出现明确的强流程/多租户需求，一律走路线 A。**

---

## 10. 风险清单

| ID | 风险 | 影响 | 处置 |
|---|---|---|---|
| R1 | 照搬参照对象「单据不写库存」的做法 | 单据与账本脱节，库存失真（L3 级事故） | 强制第 2 章 5 条铁律，写入 `.codebuddy/rules/10-inventory-rules.md` |
| R2 | ~~`warehouses` 四步法重建丢数据~~ | — | **已消除**：改用 `ADD COLUMN`，不再 DROP TABLE（外键 ON 时 DROP 会级联清空 `stock_current`） |
| R3 | SQLite 版本过低不支持 `ALTER TABLE ADD COLUMN` | 迁移失败 | `ADD COLUMN` 全版本可用；`0002` 已在 Python 3.13 / SQLite 3.50 实测通过，Win7 线（3.8.10）仍需真机跑一次迁移 |
| R4 | 单据号并发重号 | 单号冲突 | 单号在**事务内 SQL 现算**（`MAX(code)` + 补零），与商品内码生成方式一致；UNIQUE 兜底 |
| R5 | 审批过账并发（两人同时点通过） | 重复过账 | 状态机前置校验 + `client_msg_id` 幂等 + 单写者串行（SQLite 天然单写） |
| R6 | 调拨双流水只写一条 | 库存凭空增减 | 同一事务 SAVEPOINT（T-08），失败整体回滚；测试必覆盖 |
| R7 | 负库存 | 数据异常 | 复用 `stock_service.decrease_stock` 的 rowcount 条件更新，不做「先查后写」 |
| R8 | 单据字段过度膨胀（照搬 40+ 资产字段） | 项目复杂化，违背规模适配 | 严格执行第 3 章「不做」清单 |
| R9 | 前端新页面在 Chrome 109 样式失效 | 现场不可用 | 实测内核版本；antd v5 `:where()` 下限 Chrome 88；按 T-37 处置旧内核壳浏览器 |
| R10 | 打包后新模块未进 `hiddenimports` | exe 启动 500 | `ScanableSelf.spec` 同步加 `collect_submodules('api')` 已在，新增子包需确认；干净机器实测 |
| R11 | 单据影响既有高频扫码路径 | 仓管员操作变慢 | 手机端继续走 `/api/stock/*`，单据只在电脑端 |
| R12 | **迁移编号与目标库已有版本冲突** | 新迁移被静默跳过，库里出现「表建了、列没加」的半迁移状态（实测已踩到） | 合并前核对 `schema_meta` 与 `db/migrations` 编号一一对应；按「列不存在才 ADD」补齐，见 `docs/02` 第 6 章 |

---

## 11. 实施计划

> 单位为「人日」（含自测）。假设由 AI 辅助编码（`docs/05` 投喂策略），实际可压缩 30%~50%。
> 每个阶段结束必须：跑 `pytest -q` + 不变式检查 + 备份演练，再进入下一阶段。

### P0 · 对齐与地基（0.5d）

- 确认本文第 12 章的 6 个待定决策
- 在 `docs/02`、`docs/03`、`docs/06` 追加本次变更（先改文档再改代码，三步顺序不可颠倒）
- `.codebuddy/rules/10-inventory-rules.md` 追加第 2 章 5 条铁律
- **出口**：文档更新 + 规则更新，代码零改动

### P1 · 仓库树 + 流水溯源列（1d）

- `0002_wms_bills.sql` 第 1 段（warehouses 四步法）+ `stock_logs` 加 `ref_type/ref_id`
- `warehouse_repo.py` 树形改造、`stock_repo.py` 透传新列
- 测试：树校验（父不存在 / 自环 / 有子节点删除 / 有余额删除）、不变式 4 条
- **出口**：`python manage.py check` 通过，既有 51 接口回归全绿

### P2 · 采购订单（1.5d）

- 建表、`purchase_order_repo.py`、`purchase_order_service.py`、`api/wms_purchase.py`（订单部分）
- 状态机 DRAFT→SUBMITTED→APPROVED/REJECTED/CLOSED/CANCELED
- 明细子表「全量替换式」更新（对应参照对象的 `diffList` 增量，cangchu 侧简化为：事务内删旧插新 + 未过账才允许）
- 单据号服务 `bill_no_service.py`
- **出口**：订单 CRUD + 状态流转 + 幂等测试

### P3 · 采购入库 + 过账（2d，★最高风险）

- 建表、`purchase_inbound_repo/service`、`/submit` `/approve` `/post`
- **过账**：审批通过后按明细行调 `stock_service.in()`，写 `ref_type='PURCHASE_IN'`；回写 `received_qty`；`posted_at/posted_by` 落值
- 超收拦截（`received_qty + qty ≤ 订单 qty`）
- **出口**：过账后不变式 0 行；重复点过账不产生第二条流水

### P4 · 通用操作单（2.5d）

- 建表、`operation_order_repo/service`、`api/wms_operations.py`
- 五种 `op_type` 按 4.6 映射表过账；调拨双流水同事务
- `reverse` 冲销（生成反向流水 + `reversed_by`）
- **出口**：五种类型各一条用例 + 调拨事务回滚用例 + 冲销用例

### P5 · 审批交互、导出与报表（1.5d）

- 审批列表、驳回原因、CSV 导出（复用 `report_service`）
- 仪表盘/汇总可选挂接单据维度
- **出口**：CSV 中文不乱码；报表数字与 `stock_logs` 聚合一致

### P6 · 前端（3d）

- `web/src/api/wms.ts` + 4 个页面 + `nav.ts`
- 单据表单（主子表）、审批抽屉、状态标签
- `npm run build`，产物提交 `desktop/static/spa`
- **出口**：Chrome/Edge 109 实测；Win7 真机验收清单（`docs/12`）追加单据模块

### P7 · 打包、回归与交付（2d）

- `ScanableSelf.spec` 检查、`pytest -q` 全量、备份恢复演练
- 更新 `docs/07` 运维手册、`docs/08` 上线检查清单、`使用说明.md`
- 跑 `/contract-check` 与 `/security-review`
- **出口**：干净机器 exe 实测通过

### 合计

| 阶段 | P0 | P1 | P2 | P3 | P4 | P5 | P6 | P7 | 合计 |
|---|---|---|---|---|---|---|---|---|---|
| 人日 | 0.5 | 1 | 1.5 | 2 | 2.5 | 1.5 | 3 | 2 | **14** |

**关键路径**：P1（仓库树）→ P3（过账）→ P4（调拨双流水）。P3 是全局风险最高点，建议单独留 0.5d 缓冲。

---

## 12. 需要你确认的 6 个决策（确认后再动 P1）

1. **路线**：确认走路线 A（能力移植到 cangchu）而不是路线 B（挂 ruoyi）。→ 我的建议：A。
2. **审批粒度**：是否只要「admin 一键通过/驳回」，还是需要多级（如 operator 提交 → admin 审批 → 老板终审）？多级会显著增加状态机复杂度。→ 建议：单级。
3. **是否强制审批**：采购入库 / 领用是否必须审批后才过账？还是可以「直接过账、审批仅留痕」？→ 建议：入库强制审批，领用/退库可配置为免审（设置项 `wms.approve.required`）。
4. **资产**：P1 是否彻底砍掉资产台账，只留 `is_join_asset` 标记？→ 建议：砍掉，P4 再议。
5. **仓库层级**：是否真需要树形（如「总仓 → 区 → 货架」），还是只用「仓库 + 备注」就够？树形会带来 P1 的重建风险。→ 建议：保留树形但限两级。
6. **单据是否上手机端**：Android 端（V2）是否也要支持单据？→ 建议：V2 阶段仍只做扫码出入库，单据留给电脑端。

---

## 附录 A · 参照对象文件清单（来源与用途）

| 文件 | 用途 |
|---|---|
| `dal/dataobject/warehousing/WarehousingDO.java` | 仓库树字段 → 4.1 |
| `dal/dataobject/purchaseorder/PurchaseOrderDO.java` | 采购订单字段 → 4.2 |
| `dal/dataobject/purchaseorder/PurchaseOrderDetailDO.java` | 订单明细字段 → 4.2 |
| `dal/dataobject/purchaseinwarehousing/PurchaseInWarehousingDO.java` | 采购入库字段 → 4.3 |
| `dal/dataobject/goodscommonoperationorder/GoodsCommonOperationOrderDO.java` | 通用操作单字段 → 4.4 |
| `dal/dataobject/goodswarehousingdetail/GoodsWarehousingDetailDO.java` | 明细字段（含资产属性，大部分砍掉）→ 4.4 / 4.7 |
| `service/purchaseorder/PurchaseOrderServiceImpl.java` | 提交审批、审批通过写资产 → 5 |
| `service/warehousing/WarehousingServiceImpl.java` | 树校验规则（名称/编码唯一、父级存在、禁自环、有子节点禁删）→ 4.1 |
| `controller/admin/**` | URL 与权限语义 → 6.2 / 6.4 |
| `enums/ErrorCodeConstants.java` | 错误语义（编号规则不照搬）→ 6.3 |
| `framework/security/process/{fegin,local,mq}/*` | 流程回调三种实现（路线 A 不需要）→ 9 |

**注**：参照对象**没有** SQL 脚本、`yudao-server/pom.xml` 中依赖被注释、`yudao-ui` 无前端页面 —— 它是一份业务设计稿 + 骨架代码，不是可直接运行的模块。

---

## 附录 B · 决策确认与实施进度

### 第 12 章的 6 个决策（已按建议确认）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 路线 | **A**：能力移植到 cangchu，不挂 ruoyi |
| 2 | 审批粒度 | **单级**：operator 提交 → admin 审批 |
| 3 | 是否强制审批 | 采购入库**强制**审批后过账；领用/退库等 P4 再做可配免审 |
| 4 | 资产 | **砍掉**，只保留 `is_join_asset` 标记位（P4 随操作单一起建） |
| 5 | 仓库层级 | **两级**（仓库 / 库位） |
| 6 | 手机端 | V2 仍只做扫码出入库，单据只在电脑端 |

### 进度

| 阶段 | 状态 | 产出 |
|---|---|---|
| P0 文档与规则对齐 | ✅ | `docs/02`（DDL + 不变式检查 5 + 迁移清单）、`docs/03` 第 12' 章（15 接口 + 4 错误码）、`docs/06` 文件落位、`.codebuddy/rules/10` 第 8 节铁律 |
| P1 仓库树 + 流水溯源 | ✅ | `migrations/0002_wms_bills.sql`、`warehouse_repo/service` 两级树、`stock_repo.insert_log` 带 ref、`stock_service.post_bill_lines`、`tests/test_wms_bills_foundation.py` |
| P2 采购订单 | ✅ | `migrations/0003_wms_purchase.sql`、`models/purchase_repo.py`、`services/bill_no_service.py`、`services/purchase_service.py`（状态机） |
| P3 采购入库 + 过账 | ✅ | 审批同步过账、`ref_type='PURCHASE_IN'`、可收量回写、收满自动 CLOSED、`tests/test_wms_purchase.py` |
| P4 通用操作单（领用/退库/归还/借用/调拨） | ✅ | `migrations/0004_wms_operation.sql`、`models/operation_repo.py`、`services/operation_service.py`（五种 op_type 过账映射 + 调拨双流水同事务 + 冲销）、`api/wms_operations.py`、`tests/test_wms_operations.py` |
| P5 审批交互 / 导出 / 开关 | ✅ | `services/approval_service.py`（待办合并视图）、`services/wms_export_service.py`（CSV + BOM + 5 万行上限）、`api/wms_common.py`、`wms_bills_enabled` 总开关（1018）、`wms_operation_approve_required` 免审开关、`tests/test_wms_settings_and_export.py` |
| P6 前端 | ✅ | `web/src/api/wms.ts`、`components/ProductSelect.tsx`、`pages/{PurchaseOrders,PurchaseInbounds,OperationOrders,Approvals}.tsx`、`nav.ts`（新增「单据」分组）、`router.tsx`、仓库页升级为两级树；`static/spa` 产物已重建 |
| P7 打包与交付 | 🟡 部分完成 | 已做：浏览器端全流程实测（登录 → 强制改密 → 采购订单 → 审批 → 采购入库审批过账 → 领用单提交 → 待办审批 → 冲销 → 库存与流水核对）。待做：CSV 导出按钮实测、`ScanableSelf.spec` 打包检查、Win7 / Python 3.8.10 真机验收、运维手册与 `使用说明.md` 更新、`/contract-check` |

### 待办提醒（P7 交付前必读）

- 开关键名统一为 `wms_bills_enabled`（与既有 `snake_case` 设置项一致，也避免点号出现在 URL/表单里）；
  配套免审开关是 `wms_operation_approve_required`。
- 开关只拦**写操作**；关闭后历史单据仍可查、`stock_logs`/`stock_current` 一行不动（已用测试固定该行为）。
- P1~P5 的后端测试在本机 Python 3.13 + SQLite 3.50 跑通；**Win7 / Python 3.8.10 真机尚未验证**，
  交付前必须按 `docs/12` 跑一次（迁移 + 单据全流程 + CSV 用 Excel 打开验证 BOM）。
- 前端已在真实浏览器里跑通主链路（登录 → 改密 → 采购订单 → 审批 → 采购入库审批过账 → 领用单
  提交 → 待办审批 → 冲销），并据此修了 2 个展示缺陷：待办「提交人」为空（SQL 别名缺 `submitter`）、
  三张列表的单号/状态列被固定操作列遮挡。**CSV 导出按钮尚未点过**，Win7 线（Chrome/Edge 109）仍待实测。
