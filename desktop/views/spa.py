"""SPA 静态服务与前端路由回退 —— PRD 7.1 · 陷阱 T-35 / T-36

这是**唯一**的网页端入口：旧的服务端渲染页面已全部下线，浏览器界面由 `desktop/web`
（Vite + React + antd）构建到 `static/spa/`，产物随包提交，部署机不需要 Node。

★**回退必须排除 `/api/*` 与 `/healthz`**：把 `/api/xxx` 的 JSON 404 回成 HTML，
  手机端与 SPA 都会解析失败；而 `/api/*` 已经不是「页面」了（T-35）。
★`/assets/*` 是 SPA 自己的静态资源（Vite 产物），必须照常服务；
  它同时被 `core/license_gate.py` 列为免拦截前缀 —— 未激活时也要能渲染出激活页。
★**构建产物没随包 = 白屏**（T-36）：缺 `static/spa/index.html` 时返回一页**人话提示**
  （含怎么修），而不是 500 或空白页 —— 现场没人能看着白屏猜原因。
"""
from __future__ import annotations

import logging
import os

from flask import Blueprint, Response, abort, send_from_directory

from core.paths import spa_dir

bp = Blueprint("spa", __name__)
log = logging.getLogger("scanable.spa")

#: 由本模块接管的路径前缀之外，这些前缀永远不是前端路由（必须留给 API 与健康检查）
RESERVED_PREFIXES = ("api", "healthz", "static")

#: 静态资源（文件名带内容哈希）可以长缓存；index.html 绝不能缓存
IMMUTABLE = "public, max-age=31536000, immutable"
NO_STORE = "no-store, must-revalidate"

MISSING_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>前端资源缺失</title>
<style>
  body { font-family: "Microsoft YaHei", system-ui, sans-serif; background: #f5f5f5;
         color: #1f1f1f; margin: 0; padding: 48px; }
  .card { max-width: 720px; margin: 0 auto; background: #fff; border-radius: 8px;
          border: 1px solid #f0f0f0; padding: 32px; box-shadow: 0 2px 8px rgba(0,0,0,.06); }
  h1 { font-size: 20px; margin: 0 0 12px; }
  code { background: #fafafa; border: 1px solid #f0f0f0; border-radius: 4px; padding: 2px 6px; }
  li { line-height: 2; }
</style></head>
<body><div class="card">
  <h1>前端资源缺失，页面无法显示</h1>
  <p>服务端本身是正常的（接口与库存功能不受影响），只是浏览器界面（SPA）的构建产物没有随程序一起打包。</p>
  <p>处理办法（任选其一）：</p>
  <ul>
    <li>开发机：在 <code>desktop/web</code> 目录执行 <code>npm install</code> 与 <code>npm run build</code>，
        产物会写入 <code>desktop/static/spa</code>。</li>
    <li>已打包的 exe：确认打包时 <code>ScanableSelf.spec</code> 的 <code>datas</code> 里包含
        <code>static/spa</code>，重新打包即可。</li>
  </ul>
  <p>修好后刷新本页。若仍不正常，请看 <code>%LOCALAPPDATA%\\ScanableSelf\\logs</code> 下的日志。</p>
</div></body></html>
"""


def _is_inside(root: str, candidate: str) -> bool:
    """防目录穿越。仅靠 `startswith(root)` 会放过 `.../spa2/x` 这类同前缀目录。"""
    return candidate == root or candidate.startswith(root + os.sep)


def _is_reserved(subpath: str) -> bool:
    head = subpath.split("/", 1)[0]
    return head in RESERVED_PREFIXES


@bp.get("/")
@bp.get("/<path:subpath>")
def index(subpath: str = ""):
    """未匹配的页面路径一律交给前端路由；`/api`、`/healthz` 与真实静态文件除外。"""
    if subpath and _is_reserved(subpath):
        # ★走 404 处理器（`app.py` 对 /api 前缀回 JSON 包络），绝不能回成 HTML（T-35）
        abort(404)

    root = os.path.normpath(spa_dir())
    index_file = os.path.join(root, "index.html")
    if not os.path.isfile(index_file):
        log.error("SPA 构建产物缺失：%s（未执行 npm run build 或打包未带 datas）", index_file)
        return Response(MISSING_PAGE, status=503, mimetype="text/html")

    if subpath:
        candidate = os.path.normpath(os.path.join(root, subpath))
        if _is_inside(root, candidate) and os.path.isfile(candidate):
            return _send(root, subpath, cacheable=True)
    return _send(root, "index.html", cacheable=False)


def _send(root: str, relpath: str, cacheable: bool) -> Response:
    response = send_from_directory(root, relpath)
    # 资源名带内容哈希：内容一变文件名就变，可以放心长缓存；
    # index.html 必须 no-store，否则发了新版而用户还跑着旧壳，「看到的和实际部署的不是一个版本」。
    response.headers["Cache-Control"] = IMMUTABLE if cacheable else NO_STORE
    return response
