-- =====================================================================
-- 0005_wms_stocktake.sql · 盘点单（第四类单据）
-- 依据：docs/14-上线前功能补齐方案.md 第 4.4 节（DDL 逐字一致）
--
-- ★ 盘点单**自己不算账**：过账只能调 services/stock_service.post_adjust_lines()，
--   由它写 stock_logs（ref_type='STOCKTAKE'）+ 更新 stock_current（docs/13 铁律 1/2）。
-- ★ 明细不加 warehouse_id：整单一个仓库，冗余列会制造第二处真相（跨仓请开两张单）。
-- ★ book_qty 是建单时刻的快照，只用于「差异报表」；过账数量按**实时当前值**算（docs/14 4.3 B 口径）。
-- =====================================================================

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
