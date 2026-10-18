"""运维命令入口 —— PRD 14.4 / 4.5 / 15.3 / 23 章（薄层：参数解析 + 调 services + 打印）

  initdb        建目录 + 建库 + 迁移 + 种子（admin 初始口令 + must_change_password=1 + 主仓库 + 默认配置）
  runserver     DevConfig + waitress 起服务（开发用；发布走 exe）
  serve         服务模式启动（= exe 的启动路径：ProdConfig + 广播 + 访问信息/二维码落盘）
  show-address  只打印局域网访问地址与二维码路径，不启动服务（现场排障用）
  autostart     开机自启开关：on | off | status（写入/删除「启动」文件夹里的 .cmd）
  recalc-stock  ★库存最终仲裁：按流水全量重算并输出 diff（--apply 才写库，写前自动备份）
  backup        立即备份（在线一致性快照，禁止拷文件，T-04）
  check         integrity_check + 外键 + 账本不变式自检
  demo-data     灌演示数据（客户演示用；只在空账本上执行，--force 才允许非空库）
  reset-admin-password
                忘记管理员口令时的本地恢复：重置为初始口令并强制下次改密（需能接触本机）
  version       程序版本与 schema 版本
"""
from __future__ import annotations

import sys

from core.paths import backups_dir, db_path, ensure_dirs

COMMANDS = ("initdb", "runserver", "serve", "show-address", "autostart",
            "recalc-stock", "backup", "check", "demo-data", "reset-admin-password", "version")


def _conn():
    from models.database import connect
    return connect(db_path())


def cmd_initdb() -> int:
    ensure_dirs()
    from core.migrator import current_version, run_migrations
    from services import bootstrap_service
    target = db_path()
    fresh = run_migrations(target)
    conn = _conn()
    created = bootstrap_service.seed_initial_data(conn)
    print(f"数据库：{target}")
    print(f"迁移：{('已应用 ' + ','.join(map(str, fresh))) if fresh else f'已是最新（v{current_version(target)}）'}")
    print(f"种子：{('新建 ' + '、'.join(created)) if created else '已存在，跳过（幂等）'}")
    if created:
        print(f"初始口令：{bootstrap_service.seed_password()}（首次登录必须修改）")
    conn.close()
    return 0


def cmd_runserver() -> int:
    ensure_dirs()
    from core.migrator import run_migrations
    run_migrations(db_path())
    from app import create_app
    from config import DevConfig
    from core.server import serve, shutdown
    app = create_app(DevConfig)
    server, bound = serve(app, host=app.config.get("BIND_HOST", "0.0.0.0"))
    from net.udp_broadcaster import start_broadcaster
    broadcaster = start_broadcaster(app, bound)
    print(f"服务已启动：http://{bound.ip}:{bound.port}  （广播={'开' if broadcaster else '关'}）")
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(server, broadcaster, app)
    return 0


def cmd_serve() -> int:
    """服务模式启动：直接复用 exe 的入口，保证「命令行验证过的路径」与「交付形态」完全一致。"""
    from app import main as app_main
    return app_main()


def cmd_show_address(port: str | None = None) -> int:
    """打印局域网访问地址并生成访问二维码；不启动服务（现场排障 / 贴给用户看）。"""
    ensure_dirs()
    from config import ProdConfig
    from core import service_mode
    from core.server import Bound, DEFAULT_PORT_MAX
    from net.local_ip import pick_primary

    configured = int(ProdConfig.HTTP_PORT)
    chosen = int(port) if port else configured
    info = service_mode.announce(Bound(host="0.0.0.0", port=chosen, ip=pick_primary()),
                                 ProdConfig.SERVER_NAME)
    qr = service_mode.write_access_qr(info)
    note = service_mode.write_access_note(info)
    print(f"局域网主地址：{info['url']}")
    for url in info["urls"][1:]:
        print(f"备选地址    ：{url}")
    print(f"配置端口    ：{configured}（被占会自动顺延至 {DEFAULT_PORT_MAX}，实际端口以服务启动日志为准）")
    print(f"数据目录    ：{db_path()}")
    print(f"访问二维码  ：{qr or '（生成失败，可改用地址手工输入）'}")
    print(f"访问说明    ：{note or '（写入失败）'}")
    print(f"需放行的端口：{service_mode.FIREWALL_PORTS}")
    for cmd in service_mode.firewall_commands():
        print(f"  {cmd}")
    print(f"开机自启    ：{'已注册' if service_mode.is_autostart_enabled() else '未注册'}")
    print(f"并发建议    ：{service_mode.CONCURRENCY_HINT}")
    return 0


def cmd_autostart(action: str = "status") -> int:
    ensure_dirs()
    from core import service_mode

    if action == "on":
        try:
            print(f"已注册开机自启：{service_mode.enable_autostart()}")
        except RuntimeError as exc:
            print(str(exc))
            return 1
        return 0
    if action == "off":
        removed = service_mode.disable_autostart()
        print("已取消开机自启。" if removed else "原本未注册开机自启。")
        return 0
    path = service_mode.autostart_file()
    print(f"开机自启：{'已注册' if service_mode.is_autostart_enabled() else '未注册'}")
    print(f"启动项文件：{path or '（当前环境无「启动」文件夹）'}")
    return 0


def cmd_recalc_stock(apply_changes: bool = False) -> int:
    ensure_dirs()
    from models import stock_repo
    conn = _conn()
    diffs = stock_repo.recompute_from_logs(conn)
    if not diffs:
        print("库存与流水一致，无需重算。")
        conn.close()
        return 0
    print(f"发现 {len(diffs)} 条不一致（账面 vs 流水重放）：")
    for d in diffs[:50]:
        line = f"  product={d['product_id']} warehouse={d['warehouse_id']} 账面={d['recorded']} 应为={d['computed']}"
        if d.get("avg_recorded") != d.get("avg_computed"):
            line += f" 均价账面={d['avg_recorded']} 应为={d['avg_computed']}"
        print(line)
    if len(diffs) > 50:
        print(f"  … 另有 {len(diffs) - 50} 条")
    if not apply_changes:
        print("演练模式（未改动数据）。确认无误后加 --apply。")
        conn.close()
        return 1
    from services.backup_service import create_backup
    info = create_backup(trigger="pre_migration")
    print(f"已自动备份：{info['filename']}")
    changed = stock_repo.apply_recompute(conn)
    conn.close()
    print(f"已重算 {changed} 条。")
    return 0


def cmd_backup() -> int:
    ensure_dirs()
    from services.backup_service import create_backup
    info = create_backup(trigger="manual")
    print(f"备份完成：{info['path']}（{info['size_bytes']} 字节）")
    return 0


def cmd_check() -> int:
    ensure_dirs()
    from models.database import run_integrity_checks
    conn = _conn()
    problems = run_integrity_checks(conn)
    conn.close()
    if problems:
        print("发现问题：")
        for p in problems:
            print("  ·", p)
        print("请按 docs/07-运维手册.md 1.4 处理（禁止手工改库）。")
        return 1
    print("自检通过：结构完整、外键无悬空、库存与流水一致、无负库存。")
    return 0


def cmd_demo_data(force: bool = False) -> int:
    """灌演示数据（P7 交付）：走 services 层，账本不变式与幂等规则照常生效。

    ★ 必须建 app 并进入 app_context —— services 通过 `g.db` 取连接，
      裸连接（models.database.connect）只能调 repo，绕 service 就失去了所有业务校验。
    """
    ensure_dirs()
    from core.migrator import run_migrations
    from app import create_app
    from config import ProdConfig
    from models import database
    from services import bootstrap_service
    from services.demo_seed_service import seed_demo_data

    run_migrations(db_path())
    app = create_app(ProdConfig)
    with app.app_context():
        # 演示数据要引用 admin 做审批人：新库先补首启种子，避免「先 initdb 再 demo-data」这一步被忘掉
        bootstrap_service.seed_initial_data(database.get_connection())
        result = seed_demo_data(force=force)
    print(result["message"])
    if not result.get("seeded"):
        print("提示：确认是演示库后，可加 --force 强制灌入（不会删除既有数据，只会在其后追加演示记录）。")
        return 1
    print(f"仓库 {result['warehouses']} 个 / 商品 {result['products']} 个 / 往来单位 {result['partners']} 个")
    print(f"库存流水 {result['logs']} 条 / 单据 {result['bills']} 张（含待审批）")
    print(f"演示账号：{result['operator']}（口令见 docs/使用说明.md 第 7 章）")
    print("接着执行：python manage.py runserver")
    return 0


def cmd_reset_admin_password(username: str = "admin") -> int:
    """忘记管理员口令时的本地恢复（P7 交付补入：交付后用户只剩 exe，没有这个口子就永远进不去）。

    ★ 只重置**管理员**口令，并强制下次登录改密；不改任何业务数据。
    ★ 前提是能接触本机并运行程序 —— 能运行的人本来就能读到数据目录里的库文件，威胁模型不变。
    """
    ensure_dirs()
    from core.migrator import run_migrations
    from models import user_repo
    from services import bootstrap_service
    from services.auth_service import hash_password

    run_migrations(db_path())
    conn = _conn()
    user = user_repo.find_by_username(conn, username)
    if user is None:
        print(f"没有这个账号：{username}")
        print("现有账号：")
        for row in user_repo.list_all(conn):
            print(f"  {row['username']}（{row['role']}）")
        conn.close()
        return 1
    if user["role"] != "admin":
        print(f"{username} 不是管理员账号；本命令只用于恢复管理员口令。")
        conn.close()
        return 1
    password = bootstrap_service.seed_password()
    # clear_must_change=False → 置 must_change_password=1：重置后必须先改密才能用其他功能
    user_repo.set_password(conn, user["id"], hash_password(password), clear_must_change=False)
    conn.close()
    print(f"已重置账号 {username} 的口令：{password}")
    print("下次登录会强制要求改密。请立刻登录并改成你自己的口令。")
    return 0


def cmd_version() -> int:
    from app import __version__
    from core.migrator import current_version
    import os
    schema = current_version(db_path()) if os.path.exists(db_path()) else "（库未创建）"
    print(f"ScanableSelf {__version__} / schema {schema} / 数据目录 {db_path()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in COMMANDS:
        print(f"用法: python manage.py <{'|'.join(COMMANDS)}> [选项]")
        return 2
    cmd = argv[0]
    if cmd == "initdb":
        return cmd_initdb()
    if cmd == "runserver":
        return cmd_runserver()
    if cmd == "serve":
        return cmd_serve()
    if cmd == "show-address":
        return cmd_show_address(argv[1] if len(argv) > 1 else None)
    if cmd == "autostart":
        return cmd_autostart(argv[1] if len(argv) > 1 else "status")
    if cmd == "recalc-stock":
        return cmd_recalc_stock("--apply" in argv[1:])
    if cmd == "backup":
        return cmd_backup()
    if cmd == "check":
        return cmd_check()
    if cmd == "demo-data":
        return cmd_demo_data("--force" in argv[1:])
    if cmd == "reset-admin-password":
        return cmd_reset_admin_password(argv[1] if len(argv) > 1 else "admin")
    return cmd_version()


if __name__ == "__main__":
    raise SystemExit(main())
