-- =====================================================================
-- 0002_wms_bills.sql · 单据层地基
-- 依据：docs/13-yudao-WMS整合方案与实施计划.md 第 4.1 / 4.5 节
--
-- 1) warehouses 升级为两级树：code / parent_id / level / category
-- 2) stock_logs 增加单据溯源列：ref_type / ref_id
--
-- ★ 只用 ALTER TABLE ADD COLUMN，禁止 DROP TABLE：
--   迁移器连接开了外键约束，DROP TABLE warehouses 会触发 stock_current 的
--   ON DELETE CASCADE —— 库存余额会被静默清空（docs/02 第 6 章规则 2 的补充）。
-- ★ 只追加，禁止修改 0001。
-- =====================================================================

-- ---- 1. warehouses：两级树（历史行 parent_id=0 / level=1，语义等价于「原扁平仓库」） ----
ALTER TABLE warehouses ADD COLUMN code      TEXT;
ALTER TABLE warehouses ADD COLUMN parent_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE warehouses ADD COLUMN level     INTEGER NOT NULL DEFAULT 1;
ALTER TABLE warehouses ADD COLUMN category  TEXT;

-- code 可空，SQLite 的唯一索引允许多个 NULL，历史行不受影响
CREATE UNIQUE INDEX IF NOT EXISTS idx_warehouses_code   ON warehouses(code);
CREATE INDEX        IF NOT EXISTS idx_warehouses_parent ON warehouses(parent_id, is_active);

-- ---- 2. stock_logs：单据溯源（不在 trg_logs_immutable 锁定的账目列清单内） ----
ALTER TABLE stock_logs ADD COLUMN ref_type TEXT;
ALTER TABLE stock_logs ADD COLUMN ref_id   INTEGER;
CREATE INDEX IF NOT EXISTS idx_logs_ref ON stock_logs(ref_type, ref_id);
