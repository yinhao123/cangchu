---
description: "Day1 下午 —— 入库/出库/盘点/冲销 + 流水报表导出（核心业务闭环）"
argument-hint: "[可选：'先只做出库与并发测试']"
allowed-tools: Read, Write, Edit, Bash
---

承接 Day1 上午。**本阶段是全项目风险最高的部分，实现方式没有自由度。**

## 先读（必须逐条读完再动手）
`docs/01-产品需求文档.md` 第 5.4、5.5、5.6、9.8、9.9、12 章 → `.codebuddy/rules/10-inventory-rules.md` → `docs/04-开发规约与陷阱.md` 的 **T-06/T-07/T-08/T-09/T-10**。

## 任务

1. **库存服务 `services/stock_service.py`**
   - 入库：`BEGIN IMMEDIATE` → `INSERT OR IGNORE` 建 `stock_current` 行 → `UPDATE ... qty = qty + ?`（**禁止 `INSERT OR REPLACE`**，T-07）→ 写 `stock_logs`（含 `before_qty/after_qty/product_name_snapshot/unit_price/amount/source/trace_id`）→ COMMIT。`before/after` 取同事务内更新结果，禁止查询-计算推导。
   - 出库：**只允许**条件更新 + `rowcount` 判定（T-06）：
     `UPDATE stock_current SET qty=qty-? , updated_at=? WHERE product_id=? AND warehouse_id=? AND qty>=?`
     `rowcount==0` → ROLLBACK → `422 {code:1009}` + `data{current,requested,lack}`。**禁止**先 SELECT 判断。**禁止**"尽力扣减"（不许自动把 5 改成 3）。
   - 盘点 `ADJUST`：入参 `target_qty`，服务端算 `quantity = target - 当前`（可负），仅 admin，`remark` 必填。
   - 红字冲销 `reverse`：生成反向流水，原记录填 `reversed_by`；已冲销再冲销 → `409 {code:1010}`；`ADJUST` 不可冲销 → 422。**永不物理删除流水**。
2. **幂等（最关键）**：`POST /api/stock/in|out|adjust|reverse` 的 `client_msg_id` **必填**，缺失 → 422（**不给默认值、不生成 UUID 兜底**，T-09）。UNIQUE 冲突 → `200 {code:0, data.duplicated:true, 首次结果}`，不新增、不重复扣减。
3. **网页端** `/stock/in`、`/stock/out`、`/stock/adjust`、`/stock/reverse/<id>`：条码输入框自动聚焦 + 监听 Enter（兼容 HID 扫码枪）；条码未命中 → 弹窗给〔新增该商品（预填条码）〕/〔忽略〕；提交成功 Toast「已入库 5 件，当前 30 件」，清空数量、**保留仓库选择**、焦点回条码框；提交期间按钮置灰 + 一次性 `form_token`；表单内容 `sessionStorage` 暂存，异常恢复提示草稿。
4. **批量同步 `POST /api/sync/stock-logs`**（PRD 5.5、9.5）：单次 ≤200 条（超出 422）；**外层单事务 + 每条 SAVEPOINT** 做错误隔离（T-08，禁止逐条 autocommit）；逐条按序返回 `{client_msg_id,ok,duplicated,code,msg,log_id,after_qty}`；全局 `sync_lock` 串行，排队 >10s → `503 {code:1012}` + `Retry-After: 5`。业务错（1007/1008/1002/1009）返回 4xx 语义，**不要**返回 5xx。
5. **流水查询 + 报表 + 导出**（PRD 5.6）：日期区间（默认今日，跨度 >366 天 → 422）、条码/商品名、仓库、类型、操作员、来源、分页；列表展示商品名一律用 `product_name_snapshot`；汇总按 DAY/PRODUCT/CATEGORY/WAREHOUSE/OPERATOR；CSV 导出 **`utf-8-sig` 带 BOM + `\r\n`**（T-10），>50,000 行拒绝并提示分段。
6. **`GET /api/dashboard`** 与 `GET /api/stock/current`（含 `is_low_stock` 服务端计算、`negative_only` 排障参数）、`GET /api/stock/summary`。
7. **`manage.py recalc-stock`**：按流水全量重算 `stock_current` 并输出逐行 diff 报告（最终仲裁工具）。

## 测试（一条都不能少）

**UT-01~UT-07、UT-12、UT-13、UT-15、UT-16** 全部实现，其中：
- **UT-06 并发出库**：8 线程对同一商品同一仓库抢 10 件库存 → 断言成功总笔数 × 数量 ≤ 10 且 `qty >= 0`（这条是原子扣减的证明，必须真起线程）。
- UT-05：200 条批次中塞 1 条未登记条码 → 199 条成功**且已落库**，坏条 `code:1007`。
- UT-07：monkeypatch 让 `stock_current` 更新抛错 → 断言 `stock_logs` 也无该记录。

## 门禁

`pytest -q` 全绿；额外实跑并发脚本确认零负库存；`recalc-stock` 在正常数据下 diff 为 0 行。

## 输出

文件清单、`pytest -q` 真实输出、UT-06 的并发实测数字、偏差清单（列出来问我，不要自行拍板）。

补充说明：$ARGUMENTS
