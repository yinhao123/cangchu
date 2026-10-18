-- =============================================================================
-- ScanableSelf WMS MVP · schema v1
-- 本文件必须与 docs/02-数据模型.md 第 2 章逐字一致（/contract-check 第 1 项会核对）
-- 改表流程：新增 db/migrations/NNNN_*.sql → 同步本文件 → 递增 schema_meta.version
-- PRAGMA 不在此文件（journal_mode 除外，它持久化）；其余每连接设置见 models/database.py
-- =============================================================================
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_meta (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT    NOT NULL UNIQUE,
    display_name         TEXT,
    password_hash        TEXT    NOT NULL,
    role                 TEXT    NOT NULL DEFAULT 'operator'
                         CHECK (role IN ('admin','operator')),
    is_active            INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0,1)),
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,
    last_login_at        TEXT
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT    NOT NULL UNIQUE,
    client_type  TEXT    NOT NULL DEFAULT 'android'
                 CHECK (client_type IN ('android','web','pda')),
    device_name  TEXT,
    created_at   TEXT    NOT NULL,
    last_used_at TEXT,
    expires_at   TEXT,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens(user_id, revoked_at);

CREATE TABLE IF NOT EXISTS warehouses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    UNIQUE,
    name       TEXT    NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 30),
    parent_id  INTEGER NOT NULL DEFAULT 0,   -- 0=顶级；两级树（1 仓库 / 2 库位）
    level      INTEGER NOT NULL DEFAULT 1,
    category   TEXT,
    address    TEXT,
    is_active  INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_warehouses_code   ON warehouses(code);
CREATE INDEX        IF NOT EXISTS idx_warehouses_parent ON warehouses(parent_id, is_active);

CREATE TABLE IF NOT EXISTS partners (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 50),
    type       TEXT    NOT NULL CHECK (type IN ('CUSTOMER','SUPPLIER')),
    contact    TEXT,
    phone      TEXT,
    address    TEXT,
    remark     TEXT,
    is_active  INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    UNIQUE (name, type)
);
CREATE INDEX IF NOT EXISTS idx_partners_type ON partners(type, is_active);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    barcode      TEXT    UNIQUE,
    category     TEXT    DEFAULT '未分类',
    unit         TEXT    NOT NULL DEFAULT '件',
    cost_price   REAL    NOT NULL DEFAULT 0 CHECK (cost_price >= 0),
    sale_price   REAL    NOT NULL DEFAULT 0 CHECK (sale_price >= 0),
    min_stock    INTEGER NOT NULL DEFAULT 0 CHECK (min_stock >= 0),
    remark       TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    deleted_at   TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_name     ON products(name);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category, is_active);
CREATE INDEX IF NOT EXISTS idx_products_active   ON products(is_active, id);

-- 核心账本表：禁止物理删除行（纠错=红字冲销 / ADJUST）
CREATE TABLE IF NOT EXISTS stock_logs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    client_msg_id         TEXT    NOT NULL UNIQUE,
    product_id            INTEGER NOT NULL REFERENCES products(id)   ON DELETE RESTRICT,
    warehouse_id          INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
    operator_id           INTEGER NOT NULL REFERENCES users(id)      ON DELETE RESTRICT,
    partner_id            INTEGER          REFERENCES partners(id)   ON DELETE SET NULL,
    type                  TEXT    NOT NULL CHECK (type IN ('IN','OUT','ADJUST')),
    -- 方向必须由 type 表达：IN/OUT 的 quantity 恒正；ADJUST 可正可负但不得为 0。
    -- 只靠服务层校验挡不住误写的 SQL 或 AI 生成的批量语句，因此锁在库级。
    quantity              INTEGER NOT NULL CHECK (
                              (type IN ('IN','OUT') AND quantity > 0)
                           OR (type = 'ADJUST'     AND quantity != 0)
                          ),
    before_qty            INTEGER NOT NULL,
    after_qty             INTEGER NOT NULL,
    product_name_snapshot TEXT    NOT NULL,
    unit_price            REAL,                     -- 结算价（采购价 / 销售价），单据带过来
    amount                REAL,                     -- = abs(quantity) * unit_price
    unit_cost             REAL,                     -- 0007 起：成本单价（系统算，业务不得填）
    cost_amount           REAL,                     -- 0007 起：= abs(quantity) * unit_cost
    source                TEXT    NOT NULL DEFAULT 'WEB' CHECK (source IN ('WEB','APP','PDA')),
    device_name           TEXT,
    remark                TEXT,
    reversed_by           INTEGER,
    ref_type              TEXT,
    ref_id                INTEGER,
    trace_id              TEXT,
    created_at            TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_created   ON stock_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_product   ON stock_logs(product_id, warehouse_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_type_date ON stock_logs(type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_operator  ON stock_logs(operator_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_warehouse ON stock_logs(warehouse_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_partner   ON stock_logs(partner_id);
CREATE INDEX IF NOT EXISTS idx_logs_ref       ON stock_logs(ref_type, ref_id);

-- ============ 账本不可变性（DB 级硬约束）============
-- 外键 RESTRICT 只保护父记录（删商品/仓库/用户会被拒），**并不阻止删除或篡改流水本身**。
-- 因此账本保护必须由触发器承担：任何代码路径（含误写的 SQL、AI 生成的批量清理）都无法抹掉历史。
-- 允许的更正方式只有两种：红字冲销（INSERT 反向行）、ADJUST 盘点（INSERT 调整行）。
CREATE TRIGGER IF NOT EXISTS trg_logs_no_delete
BEFORE DELETE ON stock_logs
BEGIN
    SELECT RAISE(ABORT, 'stock_logs 是账本：禁止物理删除历史行（错单请用红字冲销，账实不符请用 ADJUST）');
END;

-- 仅锁定「账目语义」列；reversed_by / remark 允许更新（冲销需要回写 reversed_by 指针）。
CREATE TRIGGER IF NOT EXISTS trg_logs_immutable
BEFORE UPDATE OF type, quantity, before_qty, after_qty, product_id, warehouse_id,
                 client_msg_id, product_name_snapshot, operator_id, source, created_at,
                 unit_price, amount, unit_cost, cost_amount
ON stock_logs
BEGIN
    SELECT RAISE(ABORT, 'stock_logs 是账本：禁止修改历史行的账目字段（更正请用红字冲销或 ADJUST）');
END;

-- 派生表：可由 stock_logs 全量重算（manage.py recalc-stock）
CREATE TABLE IF NOT EXISTS stock_current (
    product_id   INTEGER NOT NULL REFERENCES products(id)   ON DELETE CASCADE,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    qty          INTEGER NOT NULL DEFAULT 0,
    avg_cost     REAL    NOT NULL DEFAULT 0,   -- 0007 起：移动加权单价（金额 = qty*avg_cost，现算不存）
    updated_at   TEXT    NOT NULL,
    PRIMARY KEY (product_id, warehouse_id)
);
CREATE INDEX IF NOT EXISTS idx_stock_wh ON stock_current(warehouse_id, qty);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'string' CHECK (value_type IN ('string','int','bool','json')),
    updated_at TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

-- ================= 单据层（0003 迁移，docs/13 第 4.2 / 4.3 节） =================
-- 单据只管「要收多少 / 收了多少」；库存一律由 services/stock_service.post_bill_lines
-- 落到 stock_logs + stock_current，流水带 ref_type/ref_id 指回单据。
CREATE TABLE IF NOT EXISTS wms_purchase_order (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT    NOT NULL UNIQUE,
    name              TEXT    NOT NULL,
    applicant_user_id INTEGER NOT NULL REFERENCES users(id)    ON DELETE RESTRICT,
    partner_id        INTEGER          REFERENCES partners(id) ON DELETE RESTRICT,
    partner_name_snap TEXT,
    applicant_date    TEXT    NOT NULL,
    total_qty         INTEGER NOT NULL DEFAULT 0,
    total_amount      REAL    NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'DRAFT'
                      CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CLOSED','CANCELED')),
    submit_at         TEXT,
    approve_at        TEXT,
    approver_id       INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reject_reason     TEXT,
    remark            TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wpo_status  ON wms_purchase_order(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wpo_partner ON wms_purchase_order(partner_id);

CREATE TABLE IF NOT EXISTS wms_purchase_order_detail (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER NOT NULL REFERENCES wms_purchase_order(id) ON DELETE CASCADE,
    product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name_snap TEXT    NOT NULL,
    barcode_snap      TEXT,
    unit_snap         TEXT    NOT NULL,
    price             REAL    NOT NULL DEFAULT 0 CHECK (price >= 0),
    qty               INTEGER NOT NULL CHECK (qty > 0),
    amount            REAL    NOT NULL DEFAULT 0,
    received_qty      INTEGER NOT NULL DEFAULT 0,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    remark            TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wpod_order ON wms_purchase_order_detail(order_id, sort_order);

CREATE TABLE IF NOT EXISTS wms_purchase_in_warehousing (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT    NOT NULL UNIQUE,
    name              TEXT    NOT NULL,
    order_id          INTEGER          REFERENCES wms_purchase_order(id) ON DELETE RESTRICT,
    order_code_snap   TEXT,
    warehouse_id      INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
    partner_id        INTEGER          REFERENCES partners(id)   ON DELETE RESTRICT,
    in_date           TEXT    NOT NULL,
    total_qty         INTEGER NOT NULL DEFAULT 0,
    total_amount      REAL    NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'DRAFT'
                      CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CANCELED')),
    posted_at         TEXT,
    posted_by         INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    submit_at         TEXT,
    approve_at        TEXT,
    approver_id       INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reject_reason     TEXT,
    remark            TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wpi_status ON wms_purchase_in_warehousing(status, in_date DESC);
CREATE INDEX IF NOT EXISTS idx_wpi_order  ON wms_purchase_in_warehousing(order_id);

CREATE TABLE IF NOT EXISTS wms_purchase_in_warehousing_detail (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_id        INTEGER NOT NULL REFERENCES wms_purchase_in_warehousing(id) ON DELETE CASCADE,
    order_detail_id   INTEGER          REFERENCES wms_purchase_order_detail(id)   ON DELETE SET NULL,
    product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name_snap TEXT    NOT NULL,
    unit_snap         TEXT    NOT NULL,
    qty               INTEGER NOT NULL CHECK (qty > 0),
    price             REAL    NOT NULL DEFAULT 0 CHECK (price >= 0),
    amount            REAL    NOT NULL DEFAULT 0,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    remark            TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wpid_inbound ON wms_purchase_in_warehousing_detail(inbound_id, sort_order);

-- ================= 通用操作单（0004 迁移，docs/13 第 4.4 节） =================
CREATE TABLE IF NOT EXISTS wms_goods_operation_order (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    code               TEXT    NOT NULL UNIQUE,
    name               TEXT    NOT NULL,
    op_type            TEXT    NOT NULL
                       CHECK (op_type IN ('RECEIVE','RETURN_STOCK','RETURN_BORROW','BORROW','TRANSFER')),
    order_id           INTEGER          REFERENCES wms_purchase_order(id) ON DELETE RESTRICT,
    in_warehousing_id  INTEGER          REFERENCES wms_purchase_in_warehousing(id) ON DELETE RESTRICT,
    warehouse_id       INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
    to_warehouse_id    INTEGER          REFERENCES warehouses(id) ON DELETE RESTRICT,
    operator_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    partner_id         INTEGER          REFERENCES partners(id) ON DELETE RESTRICT,
    op_date            TEXT    NOT NULL,
    expect_return_date TEXT,
    total_qty          INTEGER NOT NULL DEFAULT 0,
    total_amount       REAL    NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'DRAFT'
                       CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','DONE','REVERSED','CANCELED')),
    posted_at          TEXT,
    posted_by          INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    submit_at          TEXT,
    approve_at         TEXT,
    approver_id        INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reject_reason      TEXT,
    reversed_at        TEXT,
    reversed_by        INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    remark             TEXT,
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wgo_status ON wms_goods_operation_order(status, op_date DESC);
CREATE INDEX IF NOT EXISTS idx_wgo_type   ON wms_goods_operation_order(op_type, status);

CREATE TABLE IF NOT EXISTS wms_goods_operation_detail (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER NOT NULL REFERENCES wms_goods_operation_order(id) ON DELETE CASCADE,
    product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name_snap TEXT    NOT NULL,
    barcode_snap      TEXT,
    unit_snap         TEXT    NOT NULL,
    qty               INTEGER NOT NULL CHECK (qty > 0),
    price             REAL,
    amount            REAL,
    serial_number     TEXT,
    is_join_asset     INTEGER NOT NULL DEFAULT 0 CHECK (is_join_asset IN (0,1)),
    sort_order        INTEGER NOT NULL DEFAULT 0,
    remark            TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wgod_order ON wms_goods_operation_detail(order_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_wgod_sn    ON wms_goods_operation_detail(serial_number);

-- ================= 盘点单（0005 迁移，docs/14 第 4.4 节） =================
CREATE TABLE IF NOT EXISTS wms_stocktake (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT    NOT NULL UNIQUE,          -- ST + yyyymmdd + 4 位流水
    name           TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 50),
    warehouse_id   INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
    scope_category TEXT,                             -- 生成时的分类筛选（仅记录，不参与过账）
    status         TEXT    NOT NULL DEFAULT 'DRAFT'
                   CHECK (status IN ('DRAFT','SUBMITTED','REJECTED','POSTED','CANCELED')),
    book_locked_at TEXT    NOT NULL,                 -- 快照时刻（账面基准时间）
    posted_at      TEXT,                             -- 过账时刻
    operator_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    submitter_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    approver_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    remark         TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    -- 状态与时间戳必须自洽，防止"已过账但没 posted_at"这类半截状态
    CHECK ((status = 'POSTED') = (posted_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_stocktake_status ON wms_stocktake(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stocktake_wh     ON wms_stocktake(warehouse_id, created_at DESC);

CREATE TABLE IF NOT EXISTS wms_stocktake_detail (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stocktake_id  INTEGER NOT NULL REFERENCES wms_stocktake(id) ON DELETE CASCADE,
    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    book_qty      INTEGER NOT NULL DEFAULT 0,        -- 快照账面数
    real_qty      INTEGER,                          -- 实盘数（NULL = 未盘，不参与过账）
    diff_qty      INTEGER NOT NULL DEFAULT 0,        -- real_qty - book_qty（录入时算好存下）
    product_name_snapshot TEXT NOT NULL,             -- 防改名（与 stock_logs 同口径）
    remark        TEXT,
    created_at    TEXT    NOT NULL,
    UNIQUE (stocktake_id, product_id),
    CHECK (real_qty IS NULL OR real_qty >= 0)
);
CREATE INDEX IF NOT EXISTS idx_stocktake_detail_st ON wms_stocktake_detail(stocktake_id);

-- ================= 销售订单 / 销售退货（0006 迁移，docs/14 第 6 章） =================
CREATE TABLE IF NOT EXISTS wms_sales_order (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT    NOT NULL UNIQUE,          -- SO + yyyymmdd + 4 位流水
    name              TEXT    NOT NULL,
    applicant_user_id INTEGER NOT NULL REFERENCES users(id)    ON DELETE RESTRICT,
    customer_id       INTEGER          REFERENCES partners(id) ON DELETE RESTRICT,  -- type='CUSTOMER'
    customer_name_snap TEXT,                             -- ★快照：客户改名不影响历史单据
    warehouse_id      INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT, -- 出库仓库
    order_date        TEXT    NOT NULL,
    total_qty         INTEGER NOT NULL DEFAULT 0,
    total_amount      REAL    NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'DRAFT'
                      CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CLOSED','CANCELED')),
    submit_at         TEXT,
    approve_at        TEXT,
    approver_id       INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reject_reason     TEXT,
    remark            TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),  -- 0=作废（软删，永不物理删）
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wso_status   ON wms_sales_order(status, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_wso_customer ON wms_sales_order(customer_id, order_date);

CREATE TABLE IF NOT EXISTS wms_sales_order_detail (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER NOT NULL REFERENCES wms_sales_order(id) ON DELETE CASCADE,
    product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name_snap TEXT    NOT NULL,
    barcode_snap      TEXT,
    unit_snap         TEXT    NOT NULL,
    price             REAL    NOT NULL DEFAULT 0 CHECK (price >= 0),
    qty               INTEGER NOT NULL CHECK (qty > 0),
    amount            REAL    NOT NULL DEFAULT 0,
    shipped_qty       INTEGER NOT NULL DEFAULT 0,        -- 累计发货数量（≤ qty，发货时条件更新回写）
    sort_order        INTEGER NOT NULL DEFAULT 0,
    remark            TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wsod_order ON wms_sales_order_detail(order_id, sort_order);

CREATE TABLE IF NOT EXISTS wms_sales_return (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT    NOT NULL UNIQUE,          -- SR + yyyymmdd + 4 位流水
    name              TEXT    NOT NULL,
    source_order_id   INTEGER          REFERENCES wms_sales_order(id) ON DELETE RESTRICT,  -- 可空=无单退货
    order_code_snap   TEXT,
    customer_id       INTEGER NOT NULL REFERENCES partners(id)    ON DELETE RESTRICT,
    customer_name_snap TEXT,
    warehouse_id      INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,  -- 退回哪个仓库
    return_date       TEXT    NOT NULL,
    total_qty         INTEGER NOT NULL DEFAULT 0,
    total_amount      REAL    NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'DRAFT'
                      CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CANCELED')),
    posted_at         TEXT,                              -- ★过账时刻（写 IN 流水），非空=已入库
    posted_by         INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    submit_at         TEXT,
    approve_at        TEXT,
    approver_id       INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reject_reason     TEXT,
    return_reason     TEXT    NOT NULL CHECK (length(return_reason) BETWEEN 1 AND 200),
    remark            TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wsr_status   ON wms_sales_return(status, return_date DESC);
CREATE INDEX IF NOT EXISTS idx_wsr_customer ON wms_sales_return(customer_id, return_date);

CREATE TABLE IF NOT EXISTS wms_sales_return_detail (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id         INTEGER NOT NULL REFERENCES wms_sales_return(id) ON DELETE CASCADE,
    order_detail_id   INTEGER          REFERENCES wms_sales_order_detail(id) ON DELETE SET NULL,
    product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name_snap TEXT    NOT NULL,
    barcode_snap      TEXT,
    unit_snap         TEXT    NOT NULL,
    qty               INTEGER NOT NULL CHECK (qty > 0),
    price             REAL    NOT NULL DEFAULT 0 CHECK (price >= 0),
    amount            REAL    NOT NULL DEFAULT 0,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    remark            TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wsrd_return ON wms_sales_return_detail(return_id, sort_order);
