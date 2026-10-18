# 更新记录

## v1.0（骨架与规范，2026-10-15）
- 建立双端项目骨架：`desktop/`（Flask 工程，本期主交付）+ `android/`（仅契约，V2 实现）
- 交付 8 份开发文档：PRD、数据模型（权威 DDL）、API 契约（31 接口 + UDP 协议）、开发规约与陷阱（T-01~T-22）、
  CodeBuddy 提示词手册、目录结构、运维手册、上线检查清单
- CodeBuddy 约束体系：`CODEBUDDY.md` + `.codebuddy/rules/`（5 条分层规则）+ `commands/`（8 条分阶段指令）+ `agents/`（1 个只读审查子代理）
- 相对原始蓝图的增补：`ADJUST` 盘点类型、`client_msg_id` 强制幂等、红字冲销（不删流水）、
  在线一致性备份（禁文件拷贝）、库存重算仲裁工具、端口自动切换、多网卡 IP 手选兜底、规模适配声明（砍掉不适用的生产化复杂度）

## v1.1（电脑端实现，2026-10-15）
按 `.codebuddy/commands/` 的阶段顺序实现，**全程 TDD**（每条测试都先跑失败再实现）。

- **D0 地基**：`core/{paths,migrator,response,errors,logging_setup,server}.py` + `models/database.py`（每请求一连接 / PRAGMA / 可重入事务 / SAVEPOINT 批处理）+ 测试夹具。
- **Day1 上午**：`timeutil` / `validators` / `login_guard` / `api_auth` / `auth_service` / `user_repo`，网页会话登录（Flask-Login + 强制改密 + 失败锁定）与商品/仓库/往来单位三层（repo→service→api）+ 仪表盘服务。
- **Day1 下午**：`stock_repo` / `stock_service`（入库、出库、盘点、红字冲销、批量同步）+ `api/stock.py` + `api/sync.py`。**并发出库 8 线程实测零负库存**。
- **Day2**：`net/protocol.py`（UDP 报文双向）、`net/local_ip.py`、`net/udp_broadcaster.py`（定向→全局回落、失败只降级）、`core/server.py` 端口自动切换、`report_service` + 报表/导出/系统接口、`backup_service`（在线快照 + 轮转 + 恢复校验）、`settings_service`。
- **CLI**：`manage.py` 六个命令全部可用（initdb / runserver / recalc-stock / backup / check / version），已实跑验证。
- 33 个 REST 接口全部注册实现；测试 196 项全绿。

### 实现期发现并修正的设计问题（详见 `docs/09` 末节与 `docs/04` D2/E 节）
1. **令牌「复用明文」不可能**：库里只存哈希 → 改为「同设备重登作废旧的发新」，安卓侧必须覆盖本地令牌（`docs/03` 3.1、`android/README.md` 1）。
2. **`quantity CHECK` 挡不住负的入库单** → 升级为按 `type` 分别约束（`docs/02`）。
3. **备份文件名秒级粒度会让同秒两次备份互相覆盖**（静默丢一份）→ 撞名追加序号；列表排序改按解析出的时间戳而非文件名字典序（新增 T-24）。
4. **事务原语不可重入导致批量同步 500** → 嵌套时自动降级 SAVEPOINT（新增 T-23）。
5. **`/api/products/by-barcode` 未命中同时存在 1006/1007 两种码** → 统一 `404 + 1007`（`docs/03` 4.2）。
6. **waitress `port=0` 读不到真实端口**（它到 run() 才 bind）→ 自行 bind 后传 `sockets=[...]`（`docs/01` 12.2-8）。
7. **`login_user(dict)` 抛 AttributeError**，只在真点登录时暴露 → `auth_service.User` 包装（`docs/01` 12.2-9）。
8. **登录失败计数是进程级全局态**，会污染测试 → conftest autouse 复位，并写入 `docs/04` 全局副作用清单。

## v2.0（交付形态改造：Win7 局域网服务端 + 浏览器客户端，2026-10-16）

**形态**：单机桌面程序（pywebview 内嵌窗口）→ **纯服务端 exe + 浏览器客户端**。
Win7 SP1 上没有 WebView2，内嵌窗口方案不可用；新形态双击即起服务，局域网内任意机器用浏览器接入。
决策依据与取舍见 `docs/09` 第二轮决策表（S1~S5 / A1~A5 / U1~U5）。

- **服务端**：移除 pywebview / pythonnet / Flask-Login；运行时线锁 **Python 3.8.10 + PyInstaller 5.13.2**
  （waitress 2.1.2 / Pillow 9.5.0 / cryptography 41.0.7）；新增 `core/service_mode.py`（访问地址/二维码/防火墙命令/并发建议落盘 + 开机自启）；
  `manage.py` 增 `serve` / `show-address` / `autostart`。**schema 未变**（仍 v1，无新增迁移）。
- **接口**：34 → **51** 个。新增 账号与设备（6）、标签打印（3）、离线授权（2）、系统信息/网络设置/重算（4）。
  授权与令牌语义收口：`/api/*` 全量强制改密拦截、`1003` 只表示登录失效、口令类错误一律 `1002/422`、
  `PUT /api/settings` 拒收网络项（否则「改端口要输口令」形同虚设）。
- **浏览器客户端**：新增 `desktop/web/`（Vite 5 + React 18.3.1 + TS 5 + antd 5 + react-router 6 + dayjs），
  15 个页面（登录/激活/改密/仪表盘/出入库扫码台/流水/库存/汇总/商品/往来单位/仓库/标签打印/手机配对/设置/账号与设备/备份），
  产物构建到 `desktop/static/spa/` 并**随包提交**（部署机与打包机都不装 Node）。
- **旧界面全量下线**：28 个 Jinja 页面与自研 Fluent 皮肤（`static/css`、`static/js`）删除，
  `views/` 只剩 SPA 回退（`views/spa.py`），`templates/` 只剩 4 个自包含错误页。
- **测试**：196 → **275 passed**。两个网页用例文件重写为 `/api/*` 契约用例；
  新增 SPA 服务与回退、账号与设备、离线激活、标签打印用例。
- **文档**：新增 `docs/12`（Win7 运行时线与真机验收）；PRD §0/§2.1/§2.2/§3.3/§7.1/§7.5/§8.1/§8.3/§10.3/§11/§14.2/§20/§28.2 重写；
  `docs/03` 补 17 个接口章节与错误码边界；`docs/04` 重写 T-32、作废 T-31/33/34、新增 T-35~T-38；
  `docs/06/07/08/09/10/11`、`README`、`CODEBUDDY`、`.codebuddy/rules` 同步。

### 本机已验证 / 真机待验
- ✅ 本机（Win10 + Python 3.12）：`pytest -q` 275 passed；Playwright 端到端（登录→强制改密→建品→扫码入库→
  出库拦截→流水→标签→深色→窄屏，10 张截图）；spec 的 `datas` 与动态导入经实打验证（含 `static/spa`）；
  冻结 exe 冷启后 `/healthz` 200、未激活时 `/api/ping` 403、`/` → 302 `/activate`。证据见 `docs/12` §2.4。
- ⏳ 真机（Win7 SP1 + Chrome/Edge 109）：依赖安装、打包与体积/启动耗时、浏览器实测、
  防火墙取消授权时的降级、同网段另一台机器访问 —— **不得凭推测勾选**，按 `docs/12` §3 与 `docs/08` C/D/H 组执行。
- ⛔ 发版阻塞：`license/config.py:VERIFY_SIGNATURE` 仍为联调值 `False`，正式发版前必须改回 `True`。

## 待办（剩余）
- Day3：Win7 真机打包与验收（`docs/12` §3 / `docs/08` C/D/H 组）+ `docs/使用说明.md` 配图 + `docs/08` 逐条勾选
- 安卓端 V2（契约与提示词已就绪）
