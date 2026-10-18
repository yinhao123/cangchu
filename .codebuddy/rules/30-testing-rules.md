---
description: 测试与验收规范（写测试、验证功能、声称完成前必须遵循）
alwaysApply: false
enabled: true
---

# 测试规范

## 基础设施

- `tests/conftest.py` 提供 4 个 fixture：`app`（`create_app(TestConfig)`）、`client`、`db_path`、`seed`。
- 每测试独立临时库文件（`tmp_path`），autouse fixture 结束即删；**禁止**复用开发库或 `data/inventory.db`。
- TestConfig 强制 `BROADCAST_ENABLED=false`、固定 `SECRET_KEY`、`TESTING=True`。**禁止**测试中真发 UDP 包。
- 清理顺序（FK 逆序）：`stock_logs → stock_current → settings → products → partners → warehouses → api_tokens → users → schema_meta`。
- 数据工厂 `tests/factories.py`：`make_product(barcode=None, **kw)`、`make_log(type='IN', qty=1, **kw)`、`make_token(role='operator')`。

## D0 门禁（先于一切业务代码）

第一个测试文件 `tests/test_smoke.py` 必须是：建 app → 建表 → 插种子 → 查一条 → 断言。跑不通不许写业务。

## 必测清单（P0，逐条实现，编号对齐 PRD 13.3）

| 编号 | 场景 | 关键断言 |
|---|---|---|
| UT-01 | 首次入库（无 `stock_current` 行） | 自动建行，qty=入库数 |
| UT-02 | 出库库存充足 | `before/after` 快照正确 |
| UT-03 | 出库库存不足 | 422+`code:1009`+`data.lack`；**库存未被部分扣减** |
| UT-04 | 同 `client_msg_id` 提交两次 | 第二次 `code:0,duplicated:true`，流水仅 1 条，只扣 1 次 |
| UT-05 | 批量同步含 1 条坏数据 | 199 条成功落库且**不被回滚**，坏条 `code:1007` |
| UT-06 | **8 线程并发出库抢 10 件库存** | 总出库 ≤ 10，**绝不出现负库存** |
| UT-07 | 流水写成功、库存更新失败 | 整体回滚，两表零变化 |
| UT-08~12 | 删有库存仓库 / 重复条码 / operator 越权 / 无 Token / 重复冲销 | 409 / 409 / 403 / 401 / 409 |
| UT-13 | 人为打乱 `stock_current` 后 `recalc-stock` | 与流水聚合完全一致 |
| UT-14 | `23:59:59` 与次日 `00:00:00` 各一笔 | 今日统计只计后者 |
| UT-15 | `quantity` 为 0/-1/1.5/"abc"、条码超长含空格、`sort=<注入串>` | 全部 422 或回落默认，**无 500、无 SQL 执行** |
| UT-16~20 | operator 发起 ADJUST / 密码与登录锁定 / UDP 报文双向解析（NAME 含空格）/ 备份文件可 open+行数正确 / 迁移幂等且重复执行不报错 | 见 PRD 13.3 |

额外必测：`test_log_redaction`（断言日志文本中不出现明文密码与 Token）。

## 门禁

提交前 `pytest -q` 必须全绿。功能未覆盖对应测试不得声称"已完成"。压力测试（5 并发 ×30 秒，含 200 条批量同步）标准：无 5xx（同步排队 503 ≤5%）、写 P95 ≤800ms、库存零偏差。
