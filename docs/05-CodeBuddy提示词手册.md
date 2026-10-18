# 05 · CodeBuddy 提示词手册（分阶段投喂）

> 本文件是「怎么指挥 AI 把这套系统写出来」的操作手册。
> 与 `.codebuddy/commands/` 内容一一对应：commands 是**可直接触发的斜杠指令**，本手册是**为什么这样拆、怎么用、失败了怎么救**。

---

## 1. 三层文件的关系

CodeBuddy 会按顺序自动加载项目上下文，本项目据此分层放置约束：

| 层 | 位置 | 加载方式 | 放什么 | 本项目现状 |
|---|---|---|---|---|
| 记忆文件 | 根目录 `CODEBUDDY.md` | **每次会话全文注入** | 技术栈锁定、分层、禁止事项、开发红线、协作方式 | 已生成，约 70 行 |
| 项目规则 | `.codebuddy/rules/*.md`（带 `alwaysApply` 元数据） | `true` 者每次注入；`false` 者由 Agent 判断按需读取 | 分主题的硬约束 | 5 个文件 |
| 详细规范 | `docs/*.md` | 由 `CODEBUDDY.md` 用 `@路径` 引用，Agent 按需读 | PRD、DDL、API 契约、陷阱、测试 | 5 个文件 |
| 自定义指令 | `.codebuddy/commands/*.md` | 对话输入 `/` 触发 | 分阶段任务提示词（含读哪些文档、做什么、门禁、输出要求） | 8 个指令 |
| 子代理 | `.codebuddy/agents/*.md` | 「用 contract-auditor 检查…」 | 只读审查角色 | 1 个 |

**本项目规则清单**

| 文件 | `alwaysApply` | 主题 |
|---|---|---|
| `00-core-always.md` | true | 架构决策 + 禁止事项 + 工作方式 |
| `10-inventory-rules.md` | true | 库存扣减、事务、幂等、快照、纠错 |
| `20-stack-conventions.md` | true | SQLite/类型/响应/认证/安全头/UDP/打包/前端 |
| `30-testing-rules.md` | false（按需） | fixture 隔离、UT-01~20、门禁 |
| `40-android-contract.md` | false（按需） | 安卓契约兼容性责任 |

> 生效提示：规则与会话上下文绑定，**改完规则要新开对话**才生效。
> 首次使用建议在项目根执行 `/init` 让 CodeBuddy 自己扫一遍结构，但**不要用它生成的 `CODEBUDDY.md` 覆盖我们已有的**——它不知道 T-01~T-22 这些坑。

---

## 2. 标准投喂流程（电脑端 6 步）

按顺序执行，**一步一确认，不要合并**：

| 步 | 指令 | 产出 | 过关判据 |
|---|---|---|---|
| 0 | `/day0-scaffold` | 骨架、config、paths、database、migrator、response、app factory、conftest、smoke 测试、spec | `pytest -q` 绿 + exe 双击出登录页 |
| 1 | `/contract-check` | 骨架与文档的对齐报告 | 无 ❌（有则回第 0 步修） |
| 2 | `/day1-base` | 登录鉴权 + 商品/仓库/往来单位 CRUD + 仪表盘 | UT-08/09/10/11/14/15/17 绿 |
| 3 | `/day1-biz` | 入库/出库/盘点/冲销 + 流水/报表/导出 + 批量同步 + recalc | **UT-01~07/12/13 绿，尤其 UT-06 并发无负库存** |
| 4 | `/day2-connect` | UDP 广播 + `/api/ping` + 配对二维码 + 条码打印 + 34 接口收口 | 另一台电脑能访问；广播能被解析出正确端口 |
| 5 | `/day2-ops` | 设置 / 备份恢复 / 日志脱敏 / 错误码收口 / 降级告警 | **实做一次「改坏→重算→恢复」演练通过** |
| 6 | `/security-review` → `/day3-package` | 审查报告 → 正式 exe + 干净机器实测 + 使用说明 | 4 条打包门禁全过 |

**为什么这么拆**
- 第 0 步是**地基门禁**：路径/连接/打包这类问题如果留到第 3 步才发现，前面写的业务代码全要在错误的座上重写。这是自用项目最常见的翻车点。
- 第 2、3 步分开：CRUD 与库存事务的风险等级不同。库存扣减的并发正确性（T-06）必须单独一轮、单独一个测试（UT-06）逼出来，混在 CRUD 里会被"看起来能跑"蒙过去。
- 第 4 步单独：UDP 协议字段一旦发出就被安卓端依赖，**改协议 = 双端返工**，所以它必须是一轮独立的、以「与文档逐字对齐」为判据的工作。
- 第 5 步放在联调之前：没有备份/恢复的系统不能算可用，而灾难演练必须在还有时间的时候做。

**每步之间的固定动作**：先 `/contract-check` 或 `/security-review`（只读），确认无 P0，再进下一步。审查指令不改代码，安全地插在任意两步之间。

---

## 3. 指令速查

| 场景 | 指令 |
|---|---|
| 搭地基 | `/day0-scaffold` |
| 基础数据与登录 | `/day1-base` |
| 核心库存业务 | `/day1-biz` |
| 连接与协议 | `/day2-connect` |
| 运维与安全收口 | `/day2-ops` |
| 打包与交付 | `/day3-package` |
| 随时体检（只读） | `/contract-check`、`/security-review desktop/api` |
| 子代理审查 | 「用 contract-auditor 检查我刚才改的库存服务」 |
| CodeBuddy 内置可用 | `/cr`（代码审查）、`/tests`（补测试）、`/explain`、`/fix`、`/summarize`（上下文超 100K 时压缩） |

内置指令的推荐用法：
- 写完 `stock_service.py` 立刻 `/cr desktop/services/stock_service.py 重点检查库存扣减的并发安全与事务边界`。
- 补测试 `/tests desktop/services/stock_service.py`（但要人工核对是否覆盖了 UT-06 并发场景，AI 生成的测试通常不测并发）。

---

## 4. 失败模式与补救

| 现象 | 大概率原因 | 补救 |
|---|---|---|
| 生成的代码引入了新库（如 SQLAlchemy、pydantic） | 规则被淹没/会话未新开 | 新开对话；把该条禁令在指令里重复一次；`/security-review` 复查 |
| 声称"已完成"但测试没跑 | AI 汇报式幻觉 | 要求「贴 `pytest -q` 真实输出」作为交付的一部分（commands 已写死此要求） |
| 库存逻辑写成先查再改 | 没读到 `10-inventory-rules.md` | 新开对话 + 在指令里点名「出库必须条件更新 + rowcount 判定，禁止先 SELECT 再 UPDATE」 |
| 报 `TemplateNotFound`（exe 里） | 未走 `resource_path()` | 回补 `/day0-scaffold` 第 2、7 项，重打包验证 |
| 备份文件恢复后数据残缺 | 用文件拷贝而非 `backup()` | 见 T-04，强制改实现并重跑 UT-19 |
| 时间字段出现两种格式 | 混用 `CURRENT_TIMESTAMP` 与 Python 时间 | 全局 grep 清理，补 UT-14 边界测试 |
| 对话越来越慢/答非所问 | 上下文 >100K | `/summarize` 压缩，或新开对话（因为 `CODEBUDDY.md` + rules 会自动重建上下文） |
| 改了规则不生效 | 规则只注入会话开头 | **新开对话** |
| 一次让它做两步 | 违反单步确认原则 | 拒绝并接受重做；上下文污染后修起来更贵 |

---

## 5. 给 AI 投喂时的三条纪律

1. **契约不让步**：字段名、枚举值、错误码、UDP 报文格式，AI 说"这样设计更好"也不改 —— 改了就和文档与安卓端不一致。要改先改文档，再走一轮。
2. **一步一测**：每阶段结束必须真跑 `pytest -q`，不许用「逻辑上应该没问题」代替执行结果。
3. **不确定就问**：`barcode` 传 `null` 与不传的差别、软删条码能否复用、冲销是否允许链式（答：不允许）——这类语义 AI 会猜。commands 里已把「偏差列出来问我」写成硬要求，别跳过那一步。

---

## 6. 安卓端接入时的提示词（本期不用，V2 直接取用）

前置：电脑端已通过 `/day3-package`，`docs/03-API接口规范.md` 冻结。

| 指令（届时新建） | 核心内容要点 |
|---|---|
| `/day2-android-pair` | Compose 骨架 + M3 底部导航；`DatagramSocket(55555)` 监听（`soBroadcast=true`，捕获端口占用异常回落扫码）；解析 `KEY:VALUE` 报文（`NAME` 取到行尾，未知 KEY 忽略）；`/api/ping` 二次确认后才显示「可连接」；扫码 ZXing 解析 JSON 填 host/port；Token 存 EncryptedSharedPreferences |
| `/day2-android-scan` | Room `pending_ops` 表（`client_msg_id` **在扫码瞬间生成，永久不变**）；提交 → 本地落库 `PENDING` → 即时上传；**HTTP 超时保留 PENDING 并用同一 UUID 重发（超时≠失败）**；`ok:true`（含 `duplicated`）才置 SYNCED；`retry_count≥5` 转人工处理列表；震动/滴声；401 清 Token 回配对页且**不清队列** |
| `/day3-android-release` | Release 签名；Android 9+ 用 `networkSecurityConfig` **按网段**允许明文 HTTP（禁止全局 `usesCleartextTraffic=true`）；电池优化白名单引导（否则后台队列延迟同步）；断网 20 笔实测无重复 |

给安卓阶段的固定告诫（写进指令）：「所有请求必须带 `X-Auth-Token`；响应字段可能为 `null`，data class 全部给默认值；端口以 `/api/ping` 返回为准，禁止写死 5000；`is_low_stock`、`total_qty` 用服务端返回值，不要本地重算。」
