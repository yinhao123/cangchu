-- =====================================================================
-- 0003_wms_purchase.sql · 采购订单 + 采购入库单
-- 依据：docs/13-yudao-WMS整合方案与实施计划.md 第 4.2 / 4.3 / 4.6 节
--
-- 单据层只管「要收多少 / 收了多少」，库存一律由 services/stock_service.post_bill_lines
-- 落到 stock_logs + stock_current（docs/13 第 2 章铁律 1）。
-- 单据可改，流水不可变：单据作废走 is_active=0，已过账单据只走冲销。
-- =====================================================================

CREATE TABLE IF NOT EXISTS wms_purchase_order (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT    NOT NULL UNIQUE,          -- PO + yyyymmdd + 4 位流水
    name              TEXT    NOT NULL,
    applicant_user_id INTEGER NOT NULL REFERENCES users(id)    ON DELETE RESTRICT,
    partner_id        INTEGER          REFERENCES partners(id) ON DELETE RESTRICT,  -- 供应商（type='SUPPLIER'）
    partner_name_snap TEXT,                              -- ★快照：往来单位改名不影响历史单据
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
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),  -- 0=作废（软删，永不物理删）
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL                   -- 兼作乐观锁版本
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
    received_qty      INTEGER NOT NULL DEFAULT 0,        -- 累计入库数量（≤ qty，过账时回写）
    sort_order        INTEGER NOT NULL DEFAULT 0,
    remark            TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wpod_order ON wms_purchase_order_detail(order_id, sort_order);

CREATE TABLE IF NOT EXISTS wms_purchase_in_warehousing (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT    NOT NULL UNIQUE,          -- PI + yyyymmdd + 4 位流水
    name              TEXT    NOT NULL,
    order_id          INTEGER          REFERENCES wms_purchase_order(id) ON DELETE RESTRICT,  -- 可空=无源单直接入库
    order_code_snap   TEXT,
    warehouse_id      INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
    partner_id        INTEGER          REFERENCES partners(id)   ON DELETE RESTRICT,
    in_date           TEXT    NOT NULL,
    total_qty         INTEGER NOT NULL DEFAULT 0,
    total_amount      REAL    NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'DRAFT'
                      CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CANCELED')),
    posted_at         TEXT,                              -- ★过账时刻（写 stock_logs 的时刻），非空=已过账
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
