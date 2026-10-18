# ScanableSelf · 自研轻量级扫码出入库系统（WMS MVP）

手机当扫码枪、电脑当账本：**Win7 服务端**跑一个无窗口的 exe（HTTP 服务 + 手机发现广播），
局域网内任意机器用浏览器（Chrome/Edge 109）打开即用；手机同 WiFi 自动发现配对，扫码即出入库，
数据落在本地一个 SQLite 文件里。
目标：覆盖市售同类工具的核心能力，数据完全自主可控，运行成本 0 元。

## 当前进度

- [x] 项目骨架 + 全套开发规范 + CodeBuddy 约束与分阶段提示词
- [x] 电脑端：**服务端形态改造**（纯服务端、无窗口）+ **浏览器客户端全量改版**（React + antd），
      业务能力一项不减（商品/仓库/往来单位、入出库/盘点/冲销、流水/库存/报表/导出、标签打印、
      设置/账号与设备/备份恢复、离线激活、手机配对）
- [ ] Win7 SP1 真机验收（Python 3.8.10 运行线、体积与启动耗时、Chrome 109 实测）→ 见 `docs/12`
- [ ] 安卓端实现（V2，契约已固化在 `android/README.md`）

## 先看哪份文档

| 你是谁 | 从这里开始 |
|---|---|
| 用 CodeBuddy 写代码 | `CODEBUDDY.md`（会自动加载），然后对话输入 `/day1-base` 之类的斜杠指令 |
| 想知道要做什么 | `docs/01-产品需求文档.md`（第 0 章「规模适配声明」、2.1/2.2 技术栈必读） |
| 想看表结构 | `docs/02-数据模型.md` |
| 想对接 APP | `docs/03-API接口规范.md`（含 UDP 协议、错误码语义边界） |
| 踩过坑要复查 | `docs/04-开发规约与陷阱.md`（T-01~T-38） |
| 想知道怎么指挥 AI | `docs/05-CodeBuddy提示词手册.md` |
| 装好了要维护 | `docs/07-运维手册.md` |
| 要交付到 Win7 | `docs/12-Win7服务端部署与Python3.8验收.md`（真机验收清单） |
| 想知道为什么这么改 | `docs/09-扫雷审查与决策记录.md`（第二轮决策） |

## 目录

```
scanable-wms/
├── CODEBUDDY.md     # CodeBuddy 项目记忆（每次会话全文注入）
├── .codebuddy/      # rules/ 分层规则 · commands/ 分阶段提示词 · agents/ 只读审查子代理
├── docs/            # 开发文档（唯一真相源）
├── desktop/         # 服务端 Flask 工程 + 浏览器客户端工程（本期主交付）
│   ├── api/ services/ models/ core/ net/ license/   # 服务端
│   ├── views/spa.py + static/spa/                   # 只负责把构建产物发出去
│   └── web/                                         # 前端工程（仅构建机需要 Node）
└── android/         # 安卓端（本期不实现，只固化双端契约）
```

## 本地跑起来

```bat
cd desktop
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py initdb
python manage.py runserver        → http://localhost:5000 （默认账号 admin / admin123，首次登录强制改密）
pytest -q

:: 改过前端才需要（产物写入 desktop\static\spa，必须与代码一起提交）
cd web && npm ci && npm run build
```

**发布形态**：`pyinstaller ScanableSelf.spec` → `dist\ScanableSelf\ScanableSelf.exe`（onedir，
交付时整个目录一起拷；数据在 `%LOCALAPPDATA%\ScanableSelf\`）。
双击**不弹任何窗口**，启动后看 `%LOCALAPPDATA%\ScanableSelf\` 下的访问地址与二维码，
局域网内任意机器用浏览器打开该地址即可。
> PyInstaller 5.13.2（Win7 线）的 onedir 产物**没有 `_internal` 子目录**，别按 6.x 的手册排查（docs/04 T-32）。

## 浏览器基线

- 目标浏览器：**Chrome / Edge 109**（Win7 SP1 上可安装的最高版本）及同内核浏览器；
  下限由 antd v5 的 `:where()` 决定（Chrome 88+）。**不支持 IE**。
- 若现场是 360/QQ 极速模式等旧内核壳浏览器（常见 63~87），样式会失效 —— 先实测内核版本，
  再按 docs/04 T-37 处置。
- 客户端**不需要安装任何软件**，也不需要 Node。

## 运行前提与已知限制

- 电脑与访问设备必须**同一局域网**，且路由器**未开启 AP 隔离**（兜底：扫配对二维码或手输 IP）。
- 服务端需常驻运行（建议开机自启：`python manage.py autostart on`）；数据为本地单文件，
  **必须定期备份 + 演练恢复**（界面「备份与恢复」页可直接演练）。
- 打印依赖浏览器 `Ctrl+P`（标签页已做 `@media print`，只输出标签网格），非专业小票直连打印。
- 局域网内 HTTP、无强加密：**仅供内部使用，禁止端口映射到公网**。
- 建议同时写入的浏览器客户端不超过 3~5 个（SQLite 单写者，超时会报 `database is locked`）。
- 技术栈刻意保持简单（无 ORM、无缓存、无 Docker、无消息队列），取舍理由见 PRD 第 0 章与 2.2 节。
