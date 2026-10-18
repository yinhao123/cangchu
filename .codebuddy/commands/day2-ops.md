---
description: "Day2 下午 —— 设置 / 备份恢复 / 日志脱敏 / 错误码收口 / 降级告警"
argument-hint: "[可选：'重点做备份恢复']"
allowed-tools: Read, Write, Edit, Bash
---

承接 Day2 上午。**本阶段决定这套系统能不能放心用它记账。**

## 先读
`docs/01-产品需求文档.md` 5.9、10、15.3、15.6、16、20、23 → `docs/04-开发规约与陷阱.md` 的 T-04/T-17/T-18。

## 任务

1. **设置模块**（PRD 5.9）：`settings` 表 key-value（带 `value_type` 做类型转换）；分组：通用（绑定 IP/端口、广播开关与间隔、服务器显示名）、数据（备份开关/时间/保留份数、库存重算、恢复）、账号、关于。
   - `GET/PUT /api/settings`（admin）：key 白名单校验 + 类型转换，非法 422；响应回显新值并标 `needs_restart:true` 的项，前端**必须显式提示「需重启后生效」**，禁止「改了没反应」的不明状态。
2. **备份与恢复**（PRD 15.3，RPO ≤24h）
   - **只能用 `sqlite3.Connection.backup()` 在线一致性快照**，**禁止复制 `.db` 文件**（WAL 未 checkpoint 数据在 `-wal`，拷贝得到残缺库，T-04）。
   - 命名 `inventory_YYYYMMDD_HHMMSS.db`；保留最近 7 份 + 每月首份留 12 个月。
   - 每日 03:00 自动备份：守护线程 + `time.sleep`（**禁止 APScheduler/cron**）。
   - 关键操作前强制备份：迁移前、恢复前（当前库另存 `pre_restore_*.db`）。
   - 恢复：需输入当前账号密码（二次确认）→ 恢复后 `PRAGMA integrity_check` + 关键表行数校验 → 提示重启。
   - **备份/恢复成功与失败都必须写日志**；连续 3 次失败 → 仪表盘红条「数据备份已停止，请立即手动备份」（自用系统最坏的是静默失败）。
3. **日志与可观测性收口**（PRD 10.4）：JSON 行式（`ts/level/trace_id/user_id/username/action/source/ip/duration_ms/status/err_code/err_msg/extra`）；`before_request` 生成 8 位 `trace_id`（优先取 `X-Request-ID`）+ 回写响应头；`RotatingFileHandler` 10MB×10；脱敏**按 key 名黑名单的日志过滤器实现，不依赖开发者自觉**；每笔出入库必须 INFO 落审计日志。
4. **错误码与异常路径收口**：全项目 grep 确认 —— 未捕获异常只返回通用错误页/`1999` + `trace_id`，堆栈仅入日志；1001~1012/1999 全部有实际触发路径；无 `except: pass`、无裸 `except Exception` 后返回 200。
5. **降级与自检**（PRD 15.5/15.6）：启动跑 `PRAGMA integrity_check` + `wal_checkpoint(TRUNCATE)`，异常 → 只读模式 + 弹窗提示从备份恢复（L4）；`recalc-stock` diff≠0 → 顶栏橙条（L3）；磁盘 <500MB → 黄条 + 拒绝备份；`/api/*` 全部返回的字段可空性与超时阈值符合 `03-API接口规范.md`。
6. **运维手册**：完善 `docs/07-运维手册.md`（按 `trace_id` 查一次操作、备份恢复实操、库存对不上排查路径、APP 连不上四步排查、换电脑迁移、开机自启配法）。

## 测试

UT-19（备份文件能被 `sqlite3` 打开且行数正确）、`test_log_redaction`、迁移幂等（UT-20）、integrity_check 失败进只读、备份连续失败触发仪表盘告警、`PUT /api/settings` 非法 key/类型 422。

## 门禁

`pytest -q` 全绿；**实做一次灾难演练**：备份 → 手工改坏 `stock_current` → `recalc-stock` 看 diff → 用备份恢复 → 校验数据回正（这条不通过不许进打包阶段）。

## 输出

文件清单、pytest 输出、灾难演练各步实际结果、备份文件路径与保留份数现状。

补充说明：$ARGUMENTS
