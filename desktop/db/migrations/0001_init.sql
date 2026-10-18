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
    name       TEXT    NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 30),
    address    TEXT,
    is_active  INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

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
    unit_price            REAL,
    amount                REAL,
    source                TEXT    NOT NULL DEFAULT 'WEB' CHECK (source IN ('WEB','APP','PDA')),
    device_name           TEXT,
    remark                TEXT,
    reversed_by           INTEGER,
    trace_id              TEXT,
    created_at            TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_created   ON stock_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_product   ON stock_logs(product_id, warehouse_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_type_date ON stock_logs(type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_operator  ON stock_logs(operator_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_warehouse ON stock_logs(warehouse_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_partner   ON stock_logs(partner_id);

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
                 client_msg_id, product_name_snapshot, operator_id, source, created_at, unit_price, amount
ON stock_logs
BEGIN
    SELECT RAISE(ABORT, 'stock_logs 是账本：禁止修改历史行的账目字段（更正请用红字冲销或 ADJUST）');
END;

-- 派生表：可由 stock_logs 全量重算（manage.py recalc-stock）
CREATE TABLE IF NOT EXISTS stock_current (
    product_id   INTEGER NOT NULL REFERENCES products(id)   ON DELETE CASCADE,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    qty          INTEGER NOT NULL DEFAULT 0,
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

-- 迁移基线额外动作：写入版本号（种子数据由 manage.py initdb 插入，不放此处，避免测试污染）
INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES (1, '1970-01-01T00:00:00+08:00');
