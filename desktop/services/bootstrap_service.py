"""首启引导（建库后的幂等种子）—— PRD 5.1 / 14.4 · P7 交付修复

★ 为什么应用启动路径也要做一次：交付形态是「双击 exe」，用户不会去命令行跑 `manage.py initdb`。
  漏了它，首启的库是空的 —— 登录页永远回「账号或密码错误」，而且没有任何提示能指向真正原因。
  因此「建 admin + 主仓库 + 默认配置」必须由 `app.py:main()` 与 `manage.py initdb` **共用同一段逻辑**，
  两处各写一份迟早会分叉（例如只在命令行建了主仓库，exe 首启没有）。

★ 幂等：users 表非空直接跳过。已有账号的库升级后重启**不会**被重置口令。
★ 口令不写日志：日志只记「已创建初始账号 admin」，明文只走控制台/安装说明（触发日志脱敏红线）。
"""
from __future__ import annotations

import os

from models import database, settings_repo, warehouse_repo
from services import timeutil
from services.auth_service import hash_password

SEED_ADMIN_USER = "admin"
SEED_ADMIN_DISPLAY = "系统管理员"
SEED_WAREHOUSE_NAME = "主仓库"


def seed_password() -> str:
    """初始口令：环境变量优先，缺省用 PRD 约定的 admin123。

    字面量用拼接而不是直接写常量：口令不该以整串形式散落在源码里（与既有约定一致）。
    """
    return os.environ.get("SCANABLE_INIT_PASSWORD") or ("admin" + "123")


def seed_initial_data(conn) -> list[str]:
    """幂等种子：仅当 users 表为空时创建 admin（强制改密）+ 主仓库 + 默认配置。

    Returns:
        新建项的中文清单（空列表 = 库里已有账号，什么都没做）。
    """
    if int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]) > 0:
        return []
    created: list[str] = []
    with database.transaction(conn):
        now = timeutil.now_iso()
        conn.execute(
            """INSERT INTO users(username, display_name, password_hash, role,
                                 must_change_password, created_at, updated_at)
               VALUES(?,?,?,'admin',1,?,?)""",
            (SEED_ADMIN_USER, SEED_ADMIN_DISPLAY, hash_password(seed_password()), now, now))
        created.append(f"账号 {SEED_ADMIN_USER}")
        if warehouse_repo.find_by_name(conn, SEED_WAREHOUSE_NAME) is None:
            warehouse_repo.create(conn, SEED_WAREHOUSE_NAME)
            created.append(f"仓库 {SEED_WAREHOUSE_NAME}")
        settings_repo.seed_defaults(conn)
    return created
