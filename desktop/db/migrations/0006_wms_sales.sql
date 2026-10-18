-- =====================================================================
-- 0006_wms_sales.sql · 销售订单（销货）+ 销售退货
-- 依据：docs/14-上线前功能补齐方案.md 第 6 章（P1-4）
--
-- 与采购侧逐列对齐（含 is_active / status / code / partner 快照 / qty / unit_price / amount），差异：
--   1. 客户列叫 customer_id（必须指向 partners.type='CUSTOMER'）
--   2. 订单明细用 shipped_qty（已发）替代 received_qty（已收）
--   3. 退货单必填 return_reason，source_order_id 可空（允许无单退货）
--   4. ★销售订单带 warehouse_id：出库必须知道从哪个仓库扣（采购订单不需要，入库单才有仓库）
--
-- 库存一律由 services/stock_service.post_bill_lines 落到 stock_logs + stock_current，
-- ref_type 用 SALES_OUT（发货出库）/ SALES_RETURN（退货入库）。
-- =====================================================================

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
