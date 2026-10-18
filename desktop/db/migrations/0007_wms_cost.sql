-- =====================================================================
-- 0007 · P1-5 成本核算（移动加权平均）—— docs/14 第 7 章
--
-- 只加列，不动历史行（docs/02 第 6 章铁律）：
--   stock_logs.unit_cost / cost_amount  = 成本口径（系统算，业务不得填）
--   stock_current.avg_cost              = 该商品在该仓库的移动加权单价
-- unit_price / amount 仍是「结算价」（采购价 / 销售价），两个口径不复用同一列。
-- =====================================================================

ALTER TABLE stock_logs ADD COLUMN unit_cost   REAL;   -- 本笔流水的成本单价
ALTER TABLE stock_logs ADD COLUMN cost_amount REAL;   -- 本笔流水的成本金额（= |quantity| * unit_cost）

ALTER TABLE stock_current ADD COLUMN avg_cost REAL NOT NULL DEFAULT 0;

-- ★ 必须重建不可变触发器：BEFORE UPDATE OF 的列清单是静态枚举，
--   新增列不在其中就能被静默篡改。任何给 stock_logs 加列的迁移都必须重建本触发器。
DROP TRIGGER IF EXISTS trg_logs_immutable;
CREATE TRIGGER trg_logs_immutable
BEFORE UPDATE OF type, quantity, before_qty, after_qty, product_id, warehouse_id,
                 client_msg_id, product_name_snapshot, operator_id, source, created_at,
                 unit_price, amount, unit_cost, cost_amount
ON stock_logs
BEGIN
    SELECT RAISE(ABORT, 'stock_logs 是账本：禁止修改历史行的账目字段（更正请用红字冲销或 ADJUST）');
END;
