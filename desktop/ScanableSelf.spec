# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 配置 —— PRD 14.2 · 陷阱 T-16（资源路径）/ barcode 字体缺失 / waitress hidden-import
# 说明：用 spec 而非命令行，因为需要显式携带 templates/static/db/migrations 与 barcode 字体配置。
# 打包后必须实测「双击 exe → 登录页打开 → 一笔入库 → 备份」（PyInstaller 升级会静默破坏打包，T-15）。
# 同时产出一个 console=True 的调试版用于排查（T-17：无控制台模式下异常看不见）。

import os
import sys

import barcode  # 只为定位站点包目录：运行期 python-barcode 按包内相对路径读字体
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
APP_NAME = "ScanableSelf"

# ★api/ 与 views/ 的蓝图是 importlib.import_module(f"{pkg}.{name}") 动态注册的
#   （desktop/api/__init__.py:MODULES、desktop/views/__init__.py:MODULES），
#   services/dashboard_service.py 里还有一处 __import__("services.backup_service")。
#   动态导入静态分析看不见 → 冻结后启动即 "No module named 'api.ping'"，双击只剩弹窗。
sys.path.insert(0, SPECPATH)   # collect_submodules 必须能 import 到本项目包
DYNAMIC_MODULES = (
    collect_submodules("api") + collect_submodules("views") + ["services.backup_service"]
)

# ★本产物是**纯服务端**（Win7 SP1 上没有 WebView2 运行时，也承载不了内嵌窗口）：
#   不收集 webview / pythonnet，不挂窗口，界面由局域网内任意机器的浏览器访问。
#   旧版 T-33/T-34（内嵌原生窗口的启动顺序与打包要点）随 pywebview 一并作废。

# python-barcode 的字体与 EAN 目录必须随包携带，否则运行期 FileNotFoundError。
# ★不写死 ../.venv/...：本机依赖装在系统 Python 里，写死会在打包期直接找不到源文件。
BARCODE_PKG = os.path.dirname(barcode.__file__)

a = Analysis(
    ["app.py"],
    pathex=["."],
    datas=[
        ("templates", "templates"),
        ("static", "static"),
        ("db/schema.sql", "db"),
        ("db/migrations", "db/migrations"),
        # 离线授权内置公钥（docs/11 §4.5）。目标目录固定为 keys/，与 license/config.py:resource_path 对应。
        # 只带 public.pem：私钥只在卖家后台，源码树与打包产物里都不允许出现任何 private 文件。
        ("license/resources/keys/public.pem", "keys"),
        # python-barcode 只有 fonts 是资源目录：ImageWriter 写人可读文本要用 DejaVuSansMono.ttf，
        # 不带会在「打印标签」时 FileNotFoundError。EAN/UPC/charsets 都是 .py 模块，由 PYZ 自动收集，
        # 不要写进 datas —— 0.15.1 里根本没有 upc/ean 子目录，写了直接打包失败。
        (os.path.join(BARCODE_PKG, "fonts"), "barcode/fonts"),
    ],
    hiddenimports=[
        "waitress", "waitress.task", "waitress.channel", "waitress.server",
        "qrcode", "qrcode.image.pil", "barcode", "barcode.writer", "barcode.codex",
    ] + DYNAMIC_MODULES,
    # 注：不要再写 "waitress.rfb" —— waitress 没有这个模块，PyInstaller 会报
    #     ERROR: Hidden import 'waitress.rfb' not found，属于会掩盖真错误的假条目。
    # ★必须排除 pkg_resources：本机 setuptools 65.5.0 的 pkg_resources 在 Python 3.12 上
    #   import 就抛 AttributeError（pkgutil.ImpImporter 已被移除），而 PyInstaller 一见到
    #   pkg_resources 就挂 pyi_rth_pkgres 运行期钩子 → 冻结后启动即
    #   "ImportError: The 'jaraco' package is required"，双击 exe 只剩一个错误弹窗、无任何日志。
    #   全项目对它的引用只有 qrcode CLI 与 werkzeug testapp（try/except 包着的调试页），都不在运行路径上。
    excludes=["pkg_resources", "setuptools"],
    hookspath=[],
    runtime_hooks=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
# 目录模式（onedir）：onefile 会把所有文件自解压到临时目录，杀软对此行为误报率很高，
# 且 docs/11 §4.5 要求内置公钥落在稳定的 keys/ 下。
# ★PyInstaller 5.13.2（Win7 线）的 onedir **没有 _internal 子目录**：DLL 与资源直接落在
#   dist/<名>/ 下，_MEIPASS 也指向那里。6.x 的 _internal 布局与相关排查手法在此不适用（docs/12 §4.1）。
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name=APP_NAME, debug=False, strip=False, upx=False, console=False)
# 调试版：带控制台。无控制台形态下启动异常只能靠弹窗（T-17），现场排障需要这一版看堆栈。
exe_debug = EXE(pyz, a.scripts, [], exclude_binaries=True,
                name=APP_NAME + "-debug", debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, exe_debug, a.binaries, a.zipfiles, a.datas,
               strip=False, upx=False, name=APP_NAME)
