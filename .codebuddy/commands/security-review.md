---
description: "安全与数据完整性审查 —— 按 PRD 第 10 章 + 陷阱清单做只读体检"
argument-hint: "[文件/目录路径，默认全项目]"
allowed-tools: Read, Grep, Glob, Bash
---

对 $ARGUMENTS（默认全项目）做**只读**安全与数据完整性审查，按风险从高到低输出，每条给「位置 + 风险 + 违反的规范编号 + 修法」。

## 必查清单

1. **SQL 注入**：grep 所有 `execute(`，确认值全部 `?` 参数化；表名/列名/`ORDER BY` 是否来自白名单常量映射；有无 f-string/`+`/`%`/`format` 拼 SQL（T-05）。
2. **库存原子性**：所有出库路径是否都是「条件更新 + `rowcount` 判定」；有无先 SELECT 再 UPDATE 的竞态窗口（T-06）；有无绕过 `services/stock_service.py` 直接写 `stock_current` 的地方。
3. **幂等**：库存写接口是否强制 `client_msg_id` 必填（有无默认值/自动生成兜底路径，T-09）；UNIQUE 冲突是否返回首次结果而非报错。
4. **事务边界**：`stock_logs` + `stock_current` 是否同事务；批量同步是否「单事务 + SAVEPOINT」（T-08）；有无在用户交互期间持有事务。
5. **权限**：每个 `/api/*` 与写操作路由是否有 `require_login`/`require_admin`；admin-only 清单（商品/仓库写、ADJUST、冲销、设置、备份恢复、账号）是否完整；能否通过改参数（`product_id`/`warehouse_id`/`operator_id`）越权或写入不存在实体。
6. **凭据与脱敏**：有无硬编码密码/SECRET_KEY/Token；`api_tokens` 是否只存哈希；日志过滤器能否拦截 `password`/`token`/`secret`（跑一次真实登录并读日志验证）；错误页是否泄露堆栈。
7. **XSS/CSRF/重定向**：`| safe` 出现几处、是否有白名单校验；写操作是否 POST + `form_token`；`?next=` 是否只允许站内相对路径；安全响应头是否齐全。
8. **备份与数据保全**：备份是否用 `Connection.backup()`（禁止文件拷贝，T-04）；有无任何物理删除流水的路径；恢复是否有二次确认 + 恢复前自动备份；WAL/`synchronous=FULL` 是否被悄悄降级（T-03）。
9. **局域网边界**：绑定 IP 是否为私有网段；是否有任何对外公网请求（本项目运行时**应为零**）；`/api/ping`、`/healthz` 是否泄露过多信息。
10. **桌面交付风险**：数据目录是否 `%LOCALAPPDATA%`（非安装目录，T-16）；异常是否有用户可见反馈（T-17）；`resource_path` 是否兼容 `sys._MEIPASS`。

## 禁止

不修改代码；不把"理论上可以更好"的建议混进清单（只报违反已成文规范或有实际数据风险的项）；不虚构行号，引用必须真实存在。

## 输出

🔴 必修（数据错误/丢失/越权风险）→ 🟡 应修（体验与运维）→ 🟢 建议（V2 处理）。每条格式：`[编号] 位置 | 风险一句话 | 违反 T-xx / PRD 章节 | 修法`。最后给「本次审查通过的项」列表，避免只报坏消息。
