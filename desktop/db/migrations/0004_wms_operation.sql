-- =====================================================================
-- 0004_wms_operation.sql · 通用操作单（领用 / 退库 / 归还 / 借用 / 调拨）
-- 依据：docs/13-yudao-WMS整合方案与实施计划.md 第 4.4 / 4.6 节
--
-- 五种 op_type 共用一个主子表结构（对应参照对象的
-- wms_goods_common_operation_order + wms_goods_warehousing_detail），
-- 过账动作按 4.6 映射表决定：RECEIVE/BORROW → OUT，RETURN_STOCK/RETURN_BORROW → IN，
-- TRANSFER → 源仓 OUT + 目标仓 IN（同一事务两条流水）。
--
-- ★ 资产字段只留 is_join_asset 标记（决策 4：砍掉资产台账，不做折旧/残值率）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS wms_goods_operation_order (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    code               TEXT    NOT NULL UNIQUE,          -- GO + yyyymmdd + 4 位流水
    name               TEXT    NOT NULL,
    op_type            TEXT    NOT NULL
                       CHECK (op_type IN ('RECEIVE','RETURN_STOCK','RETURN_BORROW','BORROW','TRANSFER')),
    order_id           INTEGER          REFERENCES wms_purchase_order(id) ON DELETE RESTRICT,
    in_warehousing_id  INTEGER          REFERENCES wms_purchase_in_warehousing(id) ON DELETE RESTRICT,
    warehouse_id       INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,   -- 源仓库
    to_warehouse_id    INTEGER          REFERENCES warehouses(id) ON DELETE RESTRICT,   -- 调拨目标仓
    operator_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,        -- 领用/借用/归还人
    partner_id         INTEGER          REFERENCES partners(id) ON DELETE RESTRICT,
    op_date            TEXT    NOT NULL,
    expect_return_date TEXT,                              -- 借用必填
    total_qty          INTEGER NOT NULL DEFAULT 0,
    total_amount       REAL    NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'DRAFT'
                       CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','DONE','REVERSED','CANCELED')),
    posted_at          TEXT,                              -- 非空=已过账
    posted_by          INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    submit_at          TEXT,
    approve_at         TEXT,
    approver_id        INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reject_reason      TEXT,
    reversed_at        TEXT,                              -- 冲销时刻
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
    price             REAL,                              -- 可空：领用/借用可能不计价
    amount            REAL,
    serial_number     TEXT,                              -- 序列号（按需）
    is_join_asset     INTEGER NOT NULL DEFAULT 0 CHECK (is_join_asset IN (0,1)),
    sort_order        INTEGER NOT NULL DEFAULT 0,
    remark            TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wgod_order ON wms_goods_operation_detail(order_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_wgod_sn    ON wms_goods_operation_detail(serial_number);
