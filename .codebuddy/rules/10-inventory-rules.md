---
description: 库存业务铁律（涉及 stock_logs / stock_current 时绝对遵守）
alwaysApply: true
enabled: true
---

# 库存业务铁律

## 1. 扣减只能写成「条件更新 + rowcount 判定」

正确（唯一允许）写法：
```sql
UPDATE stock_current SET qty = qty - :q, updated_at = :now
 WHERE product_id = :p AND warehouse_id = :w AND qty >= :q;
```
`cursor.rowcount == 0` → 判定库存不足 → `ROLLBACK` → 返回 `422 {code:1009}`，`data` 含 `current/requested/lack`。

**禁止**先 `SELECT qty` 判断再 `UPDATE`（并发下会出现负库存，T-06）。

## 2. 事务铁律

一次业务操作 = 一个事务内完成：`INSERT stock_logs` + `UPDATE stock_current`。任一失败整体回滚，两表都不留痕。
批量同步用「外层 `BEGIN IMMEDIATE` 单事务 + 每条 `SAVEPOINT`」实现错误隔离（T-08），禁止逐条 autocommit（200 条 = 200 次 fsync）。

## 3. 幂等

所有库存写接口（`/api/stock/in|out|adjust|reverse`、`/api/sync/stock-logs`）**必须**要求 `client_msg_id`（UUID v4，≤36 字符，`stock_logs.client_msg_id UNIQUE`）。
- 缺失 → `422 {code:1002}`，**不给默认值**。
- 重复提交 → 返回首次结果 `200 {code:0, data.duplicated:true}`，不报错、不新增流水、不重复扣减（T-09）。

## 4. 数量与类型语义

| type | quantity | 谁能发起 |
|---|---|---|
| `IN` | 必须 > 0 | 已登录 |
| `OUT` | 必须 > 0（方向由 type 表达，禁止用负数） | 已登录 |
| `ADJUST` | 可正可负（= 盘点目标值 − 当前值） | 仅 `admin`，`remark` 必填 |

`quantity` 是 INTEGER，1~100000；小数/0/负数（IN、OUT）一律 422。
> 库级兜底：`stock_logs.quantity` 的 CHECK 已按 `type` 分别约束（IN/OUT 必须 >0，ADJUST 不得为 0）。
> 服务层校验是第一道、CHECK 是第二道——**不要**为了"让某条批量 SQL 通过"去放宽 CHECK。

## 5. 必须写快照字段

`stock_logs` 写入时必须填：`before_qty`、`after_qty`（取同事务内更新结果，禁止查询-计算推导）、`product_name_snapshot`（防改名后历史报表失真）、`source`（`WEB`/`APP`/`PDA`）。

## 6. 纠错方式（账本不可变，且由数据库强制）

> `stock_logs` 上有 `trg_logs_no_delete`（禁删）与 `trg_logs_immutable`（禁改账目字段）两个触发器。
> 任何 `DELETE FROM stock_logs` 或 `UPDATE stock_logs SET quantity=...` 都会直接报错——这是护栏，不是 bug。
> 不要因为"触发器导致我的批量清理/归档/测试脚本失败"而去 DROP 它；改成走冲销或 ADJUST。
> 测试需要重置数据时：整库删文件（tmp_path 独立库），或只 `UPDATE ... SET reversed_by/remark`（这两列允许）。

- 错单 → 红字冲销 `POST /api/stock/reverse/<id>`，生成反向流水，原记录填 `reversed_by`；已冲销再冲销 → `409 {code:1010}`，绝不生成第二条冲销。
- 账实不符 → `ADJUST`。
- 系统性偏差 → `python manage.py recalc-stock`（按流水全量重算并输出 diff），它是最终仲裁工具。
- **永远不允许**手工 `UPDATE stock_current`。

## 7. 不变式（任何时候必须成立）

`stock_current.qty == SUM(IN) - SUM(OUT) + SUM(ADJUST)`；`qty < 0` 视为数据异常，UI 必须标红显示，禁止 `MAX(0,...)` 掩盖。

## 8. 单据层（采购订单 / 采购入库 / 领用退库借用调拨）与账本的关系

> 依据 `docs/13-yudao-WMS整合方案与实施计划.md` 第 2 章。单据层是 0002/0003 迁移新增的。

1. **单据禁止自己写 `stock_current`**：过账只能调 `services/stock_service.post_bill_lines()`，
   由它统一走 `increase_stock` / `decrease_stock` + `insert_log`（铁律 1、2 在此继续生效）。
2. **过账必须整单原子**：任一行失败（库存不足 / 商品停用 / 仓库停用 / 超收）整个单据回滚，
   绝不留下「单据已审批、库存没加」的半截状态（这是参照对象 yudao-module-wms 的短板）。
3. **溯源必写**：每条由单据产生的流水必须带 `ref_type` + `ref_id`，二者同有同无
   （`docs/02` 不变式检查 5）。
4. **幂等键由单据派生**：`client_msg_id` 用 `_bill_msg_id(单据号, 明细行id)` 确定性生成
   （必须是 UUID v4），**不要**每次过账现生成随机 UUID —— 否则重复点击审批失去幂等保护。
5. **单据可改、流水不可变**：单据作废走 `cancel`（`status=CANCELED` + `is_active=0`），
   已过账单据禁止改明细；要更正走红字冲销 `/api/stock/reverse/<log_id>`。
6. **超收/超发用条件更新拦**：`bump_received` 返回 0 行即超量 → `409 {code:1016}`，
   禁止「先查可收量再决定写不写」。

## 9. 仓库与商品的可操作性校验

写库存前必须确认：商品存在且 `is_active=1`、仓库存在且 `is_active=1`。失效仓库 → `1008`，条码未登记 → `1007`，商品停用 → `1004` 语义提示。删除仓库需先校验该仓库无库存余额（`qty != 0` → 409）。
