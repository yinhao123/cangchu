# 12 · Win7 服务端部署与 Python 3.8 验收

> **本文是本项目「Windows 7 SP1 服务端」交付线的唯一施工单。**
> 背景：目标机是 Win7 SP1，可用的最高 Python 官方版本是 **3.8.10**（3.9+ 安装器会因缺 KB2533623 等补丁拒绝安装），
> 而 3.9 移除了 PEP 585/604 的「导入期不求值」容忍度、PyInstaller 6.x 也放弃了 Win7。
> 因此**运行线必须整体下移到 Python 3.8 + PyInstaller 5.13.2**，这份文档把「锁定清单 / 本机已验证 / 真机待验收」三件事分开写清楚。

**状态标注约定**（贯穿全文，不得混用）：

| 标记 | 含义 |
|---|---|
| ✅ 本机已验证 | 在开发机（Windows 10 + Python 3.12）实跑过、有可复现证据 |
| ⚠️ 真机验收项 | **开发机无法验证**，必须在 Win7 SP1 真机上按 §3 逐条执行并记录。**不得凭推测写成已完成** |

---

## 0. 结论速览

| 项 | 状态 | 说明 |
|---|---|---|
| 源码能否在 Python 3.8 下 import | ✅ 本机已验证 | 全量静态审计 **0 缺口**，见 §2 |
| 依赖版本线能否在 Py3.8 解析安装 | ⚠️ 真机验收项 | 清单见 §1，`pip freeze` 结果必须回写 §3.3 |
| 打包 spec 是否正确（datas / 动态导入） | ✅ 本机已验证 | 用本机 3.12+PyInstaller 6.10 实打一次，产物内含 `static/spa`（index.html + 47 个 assets）、`templates/errors`、`keys/public.pem`、`db/schema.sql`、barcode 字体；冻结 exe 冷启可服务，详见 §2.4 |
| `desktop/dist/` 能否交付 Win7 | ❌ 不可交付（已清理） | 本机验证产物是 **Python 3.12 + PyInstaller 6.10**（`_internal` 布局），且体积 75MB 是这两者的参考值；Win7 线必须按 §3 重打并重测 |
| 浏览器客户端能否在目标浏览器跑通 | ✅ 本机已验证（Chromium 内核） | Playwright 端到端跑通登录→改密→建品→扫码入库→出库拦截→流水→标签→深色→窄屏，10 张截图见 §2.4；**Chrome/Edge 109 真机仍需实测** |
| 启动即崩类问题（缺 future import） | ✅ 本机已验证 | 本轮已补齐 5 处，详见 §2.2 |
| 离线授权是否可正式发版 | ❌ 阻塞 | `keys/public.pem` 已放置；但 `license/config.py:VERIFY_SIGNATURE=False`（联调开关）未关，见 §4.4 |

---

## 1. 依赖锁定清单（Windows 7 SP1 + Python 3.8.10 线）

对齐 `desktop/requirements.txt` / `requirements-dev.txt`，版本一律 `==`。

### 1.1 运行时（`requirements.txt`）

| 包 | 旧版本 | **锁定版本** | 变更理由 |
|---|---|---|---|
| Python | 3.11.9 | **3.8.10（x64）** | Win7 SP1 上可用的最后一个官方版本；3.9+ 安装器拒绝运行 |
| Flask | 3.0.3 | 3.0.3 | 不变（`requires-python >= 3.8`） |
| waitress | 3.0.0 | **2.1.2** | ⬇️ 3.0.0 的 `requires-python` 是 `>= 3.9` |
| Flask-Login | 0.6.3 | 0.6.3 | 不变 |
| python-dotenv | 1.0.1 | 1.0.1 | 不变（纯 Python） |
| qrcode[pil] | 7.4.2 | 7.4.2 | 不变（纯 Python） |
| python-barcode | 0.15.1 | 0.15.1 | 不变（纯 Python） |
| Pillow | 10.4.0 | **9.5.0** | ⬇️ 10.x 的 Win7 wheel 未在真机验证过 |
| cryptography | 43.0.1 | **41.0.7** | ⬇️ 43.x 的 Rust 扩展对 Win7 的支持未在真机验证过 |
| ~~pywebview~~ | 6.2.1 | **移除** | Win7 **没有 WebView2 运行时**；且新形态是纯服务端，不弹窗口 |
| ~~pythonnet~~ | 3.2.1 | **移除** | 仅因 pywebview 的 EdgeChromium 后端需要而引入，随之一并移除 |

**为什么 python-dotenv 留着**：`.env` 读取仍在使用路径上（`config.py`），且纯 Python 无兼容风险。

### 1.2 构建/测试（`requirements-dev.txt`）

| 包 | 旧版本 | **锁定版本** | 理由 |
|---|---|---|---|
| pyinstaller | 6.10.0 | **5.13.2** | 6.x 已放弃 Win7；5.13.2 是最后一个支持 Win7 的版本 |
| pytest | 8.3.2 | 8.3.2 | 仅构建机使用 |
| pip-audit | 2.7.3 | 2.7.3 | 仅构建机使用 |

### 1.3 间接依赖（⚠️ 真机验收项）

`requirements.txt` 只锁顶层，**传递依赖交由 pip 按 `requires-python` 自行筛选**。Python 3.8 上 pip 会避开
`Werkzeug 3.1+`（要求 3.9）、`click 8.2+`（要求 3.10）、`blinker 1.9+`（要求 3.9）等版本 —— 这是**机制**而非运气，
但**必须实测确认**，不能想当然：

```powershell
# Win7 真机装完后立即固化，回写到本节（这是交付物的一部分）
pip freeze > requirements-win7.lock.txt
```

> 若真机 `pip freeze` 出现任何要求 3.9+ 的包，说明 pip 版本过旧（不读 `requires-python`），
> 升级 pip 后重装；仍不行则用 `--only-binary=:all:` 逼出平台 wheel，禁止源码编译（Win7 上没有 MSVC 编译环境）。

---

## 2. 本机已验证项（开发机：Windows 10 + Python 3.12）

### 2.1 结论

对 `desktop/` 下全部 **86 个 `*.py`**（排除 `build/`、`dist/`、`.pyc`）做了 Python 3.8 兼容性静态审计：

| 检查项 | 结果 |
|---|---|
| 使用 PEP 585/604 注解的文件数 | 41 个 |
| 顶部带 `from __future__ import annotations` 的文件数 | **63 个** |
| 用了 PEP 585/604 却**缺** future import 的文件 | **0 个** ✅ |
| `match` 语句 / 海象符 / `dict \|=` 就地合并 | 0 处 ✅ |
| 3.9+ 专属 API（`str.removeprefix/removesuffix`、`functools.cache`、`zoneinfo`、`graphlib`、`math.lcm`、`datetime.UTC`、`tomllib`、`TypeAlias`…） | 0 处 ✅ |
| 3.9+ 专属 typing（`Annotated`、`TypeGuard`、`ParamSpec`、`Self`、`LiteralString`） | 0 处 ✅ |
| 「future import 也救不了」的运行期求值场景（类型别名赋值 / `cast` / `TypedDict` / `NamedTuple`） | 0 处 ✅ |

**为什么 future import 是这件事的关键**：Python 3.8 会在**导入期真的对函数签名、`@dataclass` 字段、模块级变量注解求值**，
遇到 `dict[int, int]` 或 `int | None` 直接抛
`TypeError: 'type' object is not subscriptable` / `unsupported operand type(s) for |`。
本项目的差异在于 `core/errors.py` 被 `app.py` / `core/response.py` 早期导入 ——
**它崩 = 整个程序起不来**，且现场表现是「双击没反应」，而不是好看的红字。

### 2.2 本轮补齐的 5 处（都是「缺 future import 却用了 PEP 585/604」）

| 文件 | 位置 | 修复 |
|---|---|---|
| `desktop/core/errors.py` | docstring 后 | 加 `from __future__ import annotations`（**启动路径上，最致命**） |
| `desktop/license/errors.py` | `from typing import Final` 前 | 同上 |
| `desktop/license/machine_id.py` | `import hashlib` 前 | 同上 |
| `desktop/license/store.py` | docstring 后 | 同上 |
| `desktop/license/verifier.py` | docstring 后 | 同上 |

统一注释文案：`# ★Python 3.8（Win7 服务端）会在导入期求值注解，缺这行用 PEP 585/604 会 TypeError（docs/12）`。

### 2.3 启动导入链（决定「哪些模块崩了就全崩」）

`python app.py` / exe 的导入顺序，**第 1、2 段任一模块 import 失败 = 双击无反应**：

1. 模块级：`flask` → `config` → `core`（`license_gate`）→ **`core.errors`** → `core.logging_setup` → `core.paths`
   → **`core.response`** → `models.database` → `net.local_ip`
2. `create_app()`：`flask` →（缺 SECRET_KEY 时）`core.config_runtime` → `database.init_app`
   → `services.auth_service` → `license.service`（**`cryptography` 在此进入**）→ `api.*` / `views.*` 蓝图注册
   （`flask_login` 已下线，不再出现在启动链上）
3. `main()`：`install_excepthooks` → `ensure_dirs` → `core.migrator` → `create_app` → **`core.server`（waitress）** → `net.udp_broadcaster`

**延迟导入的包**（不在启动路径上，缺了也不会「启动即崩」，只会用到时才报）：
`qrcode`、`barcode`、`PIL`（经前者间接）。

### 2.4 本轮新增的本机验证证据（开发机 Windows 10 + Python 3.12，**不能替代真机**）

| 验证项 | 结果 |
|---|---|
| `pytest -q` | **275 passed**（旧 Jinja 页面用例已重写为 `/api/*` 契约用例） |
| SPA 构建 | `cd desktop/web && npm run build` → `desktop/static/spa/`（index.html + assets，`base=/`） |
| 浏览器端端到端 | Playwright（系统 Edge/Chromium）跑通：登录 → 首登强制改密 → 新增商品 → 扫码入库（回车提交，结存 1）→ 出库→ 再出库被「库存不足：现有 0 / 要出 1 / 缺 1」拦下 → 流水查询（2 条）→ 标签打印（打印区生成标签与 blob 条码图）→ 深色主题（`data-theme=dark`，顶栏/状态栏无白带）→ 480px 窄屏（导航变抽屉可展开）→ 系统设置（4 组按服务端元数据渲染，网络组需口令） |
| 端到端脚本与截图 | `desktop/.e2e/verify_dark.js` + `01~11-*.png`（该目录已 `.gitignore`，不进版本库） |
| spec 机械校验 | 本机 PyInstaller 实打一次：产物含 `static/spa/index.html`（+47 assets）、`templates/errors/500.html`、`keys/public.pem`、`db/schema.sql`、`barcode/fonts/DejaVuSansMono.ttf` |
| 冻结 exe 烟测（本机 6.10/3.12 产物） | `/healthz` → 200；`/api/ping` → 403（**未激活时门禁 fail-closed 生效**，手机据此收到 1013）；`/` → 302 `/activate`；`/activate` → 200 SPA 外壳 |
| 体积参考值 | 本机 6.10 + Python 3.12 的 onedir 共 **75 MB**（含 `_internal`）。**5.13.2 + 3.8 线必须重测**，PRD G6 的体积口径需按新形态重新定义 |

> ⚠️ 本机 PyInstaller 是 **6.10**（`_internal` 布局），与 Win7 线的 **5.13.2**（无 `_internal`）**布局不同**。
> 上表的 spec 校验证明的是「datas 与动态导入写对了」，**不能**用来证明 Win7 产物可用。

---

## 3. Win7 SP1 真机验收 Runbook（⚠️ 全部为真机验收项）

### 3.1 前置条件

| # | 项 | 说明 |
|---|---|---|
| 1 | 系统补丁 | SP1 基础上必须已装 **KB2533623**；建议补齐所有 Windows Update |
| 2 | VC++ 运行库 | 安装 **Microsoft Visual C++ 2015-2022 Redistributable (x64)** —— Pillow / cryptography 的 wheel 依赖它 |
| 3 | Python | **python-3.8.10-amd64.exe**，安装时勾选 `Add Python 3.8 to PATH` |
| 4 | 位数一致性 | Python、依赖 wheel、打包产物三者必须同为 **x64**（本机 `dist/` 是 x64，保持一致） |
| 5 | 离线 install | Win7 裸机常无外网：先在联网机器 `pip download -r requirements.txt -d wheels --only-binary=:all: --platform win_amd64 --python-version 38`，再拷进去 `pip install --no-index --find-links wheels -r requirements.txt` |

### 3.2 验收步骤（逐条执行，勾选并记录）

| # | 命令 / 动作 | 期望结果 | 通过 |
|---|---|---|---|
| S1 | `python -V` | `Python 3.8.10` | ☐ |
| S2 | `python -c "import flask, waitress, flask_login, cryptography, PIL, qrcode, barcode; print('ok')"` | 打印 `ok`，**无 TypeError / ImportError** | ☐ |
| S3 | `pip install -r requirements.txt`（见 §1.3 的离线方式） | 全部 `Successfully installed`，无源码编译 | ☐ |
| S4 | `pip freeze > requirements-win7.lock.txt` | 生成锁文件；回写 §1.3 | ☐ |
| S5 | `python -c "import app; app.create_app()"` | 不抛异常（**S2 过了 S5 才可能过**） | ☐ |
| S6 | `python manage.py initdb` | 建库成功；`%LOCALAPPDATA%\ScanableSelf\` 下出现 `inventory.db` | ☐ |
| S7 | `python manage.py runserver` | 控制台打印 `服务已就绪 http://<ip>:<port>`；浏览器本机可登录 | ☐ |
| S8 | `pytest -q` | 全绿（Win7 上跑一次，确认无平台相关跳错） | ☐ |
| S9 | `python manage.py check` | 账本不变式、外键、`integrity_check` 全部通过 | ☐ |
| S10 | 局域网另一台机器访问 `http://<Win7 的 IP>:5000/` | 能打开登录页（**防火墙未放行时**先按 §3.4 处理） | ☐ |
| S11 | 手机 APP / 扫码配对 | 能收到 UDP 广播并完成配对 | ☐ |
| S12 | `pyinstaller ScanableSelf.spec`（先改 spec，见 §4.1） | 产出 `dist\ScanableSelf\ScanableSelf.exe` | ☐ |
| S13 | 双击 `dist\ScanableSelf\ScanableSelf.exe` | 无窗口形态下服务正常起；**不弹任何窗口**（新形态设计如此） | ☐ |
| S14 | 记录 `dist\ScanableSelf\` 体积与「从双击到能打开登录页」的耗时 | 回写 §4.3（旧基线已作废） | ☐ |

### 3.3 真机记录表（执行时填写）

```
机器/系统：____________   Python：____________   pip：____________
S5 结果：____________   S7 端口：____________   S11 结果：____________
S12 体积：____________   S14 启动耗时：____________
pip freeze 摘要（或附 requirements-win7.lock.txt）：
```

### 3.4 Win7 上的防火墙

首次启动服务后 Windows 会弹「是否允许」，**必须勾选专用网络**。若当时点了取消，手工放行：

```powershell
netsh advfirewall firewall add rule name="ScanableSelf HTTP" dir=in action=allow protocol=TCP localport=5000-5010
netsh advfirewall firewall add rule name="ScanableSelf UDP"  dir=in action=allow protocol=UDP localport=55555
```

---

## 4. 已知风险与回退

### 4.1 PyInstaller 5.13.2 与 6.x 的差异（会让人照抄旧文档翻车）

**5.x 的 onedir 产物没有 `_internal/` 子目录** —— DLL、`python38.dll`、`templates/`、`static/` 全部直接落在 `dist/<名>/` 下。
因此 `docs/04` T-32 里所有基于 `_internal` 的路径示例与 `Failed to load Python DLL '…\_internal\python312.dll'`
排查手法**在 5.x 上全部失效**，必须以 T-3x 的新条目为准。（spec 的同步重写见 §4.2）

### 4.2 打包前的 spec 改造（✅ 已完成，见 §2.4 的实测证据）

- `datas` 含：`templates`、`static`（**内含 `static/spa` 的 SPA 构建产物**）、`db/schema.sql`、
  `db/migrations`、`license/resources/keys/public.pem → keys`、barcode 字体。
  **漏带 `static/spa` = 打开白屏**（T-36；页面会给出「前端资源缺失」的人话提示）。
- 已删除 pywebview / pythonnet 相关条目（`collect_data_files("webview")`、`"clr"` 等）。
- 保留 `excludes=["pkg_resources", "setuptools"]`（T-32 第 1 条：否则冻结后启动即 `ImportError`）。
- 保留 `collect_submodules("api") + collect_submodules("views") + ["services.backup_service"]`（动态蓝图，静态分析看不见）。
- `console=False`（无控制台服务形态）＋ **一个 `console=True` 的 `<名>-debug.exe`** 专供现场排障
  （T-32 的 `-RedirectStandardError` 手法仍然有效）。

### 4.3 已失效的旧基线

| 项 | 旧值来源 | 处理 |
|---|---|---|
| 体积 / 启动耗时 | 基于 Python 3.12 + PyInstaller 6.10 的测量 | **作废**，按 S14 重测 |
| `dist/` 现有产物 | 3.12 冻结 | 禁止交付 Win7，必须按 S12 重打 |

### 4.4 交付前阻塞项（与 Win7 无关，但一起会炸）

1. `desktop/license/config.py:VERIFY_SIGNATURE` 当前是 **`False`**（后台出码未接通时的联调开关，
   此时注册码内容**完全不可信**）。正式发版前必须改回 `True`，否则等于没有授权校验。
2. `license/config.py:APP_CODE = "CHURUKU"` 必须与后台「软件管理」里的编码逐字符一致（大小写敏感），否则所有码报 `1005`。
3. ~~`license/resources/keys/public.pem` 尚未放置~~ —— **已放置**，并已在打包烟测中确认落进 `keys/public.pem`。

---

## 5. 排障速查（Win7 现场）

| 现象 | 首选怀疑 | 处理 |
|---|---|---|
| 双击 exe 无反应、`logs/` 不生成 | 冻结产物／启动前失败 | 用 `console=True` 版跑；或 `Start-Process <exe> -PassThru -RedirectStandardError err.txt` |
| 启动报 `DLL load failed` | 缺 VC++ 运行库 | 装 VC++ 2015-2022 x64（§3.1 第 2 条） |
| 启动报 `ModuleNotFoundError` | wheel 位数不匹配 / 漏装 | 确认 x64 三件套一致（§3.1 第 4 条） |
| 报 `TypeError: 'type' object is not subscriptable` | 有人新写了 PEP 585/604 却漏了 future import | 按 §2.2 补 `from __future__ import annotations` |
| 别机访问不了 | 防火墙 | §3.4 |
| 手机收不到广播 | 广播被网段隔离 | 状态栏黄条提示改用扫码配对，HTTP 不受影响（T-13） |
| 写库存报 `database is locked` | 并发写入超过 SQLite 单写者承受 | `busy_timeout=5000` 只排队 5s；建议同时写入者 ≤3~5（PRD 9.2） |
