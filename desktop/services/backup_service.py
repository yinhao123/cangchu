"""备份与恢复 —— PRD 15.3 / 5.9 · 陷阱 T-04 · RPO ≤ 24h / RTO ≤ 30min

★ 唯一正确实现：`sqlite3.Connection.backup()` 在线一致性快照。
  **禁止 shutil.copy(inventory.db)**：WAL 模式下未 checkpoint 的数据在 -wal 文件里，
  只拷主文件必然得到残缺库；写入瞬间拷贝还会拿到撕裂页（T-04，实测有专门用例守着）。
★ 备份成功/失败都必须留痕；连续失败 3 次 → 仪表盘红条（自用系统最坏的是静默失败）。
★ 恢复前必须先把当前库另存为 pre_restore_*.db，并做 integrity_check + 关键表行数校验。
★ 自动备份用守护线程 + time.sleep（禁止 APScheduler / cron，见依赖纪律）。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from datetime import timedelta

from core.paths import backups_dir, db_path
from services import timeutil

log = logging.getLogger("scanable.backup")
NAME_RE = re.compile(r"^inventory_\d{8}_\d{6}(?:_[a-z]+(?:_\d+)?|_\d+)?\.db$")
REQUIRED_TABLES = {"users", "products", "warehouses", "stock_logs", "stock_current", "settings"}
KEEP_DEFAULT = 7
MAX_CONSECUTIVE_FAILURES = 3

class RestoreError(RuntimeError):
    """备份文件不可用（损坏/不是数据库/缺表）。"""


_failures = 0
_failures_lock = threading.Lock()
_status = {"last_ok_at": None, "last_error": None, "consecutive_failures": 0}


def _mark_success(path: str) -> None:
    with _failures_lock:
        _status["last_ok_at"] = timeutil.now_iso()
        _status["last_error"] = None
        _status["consecutive_failures"] = 0
    log.info("备份成功 %s", os.path.basename(path), extra={"action": "backup.create", "status": "success"})


def _mark_failure(error: str) -> None:
    global _failures
    with _failures_lock:
        _status["consecutive_failures"] += 1
        _status["last_error"] = error[:400]
    log.error("备份失败：%s（连续第 %s 次）", error, _status["consecutive_failures"],
              extra={"action": "backup.create", "status": "failure"})


def status() -> dict:
    """供仪表盘/运维页读取；consecutive_failures ≥ 3 时必须显示红条告警（L2 降级）。"""
    return dict(_status)


def reset_status() -> None:
    """清空进程级状态。状态是全局的，测试之间必须复位（PRD 12.4 全局副作用清单）。"""
    global _failures
    with _failures_lock:
        _failures = 0
        _status.update({"last_ok_at": None, "last_error": None, "consecutive_failures": 0})


def create_backup(trigger: str = "manual", target_dir: str | None = None,
                  source_db: str | None = None) -> dict:
    """生成一致性快照。trigger ∈ manual|auto|pre_restore|pre_migration。

    source_db 显式可注入（测试用临时库；生产留空走 core.paths.db_path()）。
    """
    dest_dir = target_dir if target_dir is not None else backups_dir()
    source = source_db or db_path()
    if not os.path.isfile(source):
        raise RuntimeError(f"源数据库不存在：{source}")
    stamp = timeutil.now_dt().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{re.sub(r'[^a-z]', '', str(trigger).lower())[:12]}" if trigger != "auto" else ""
    filename = f"inventory_{stamp}{suffix}.db"
    # ★ 同一秒内的两次备份不得互相覆盖（手动 + 自动同时触发就会丢一份）：
    #   秒级时间戳不够，撞名时追加序号，保证每份快照都是独立文件。
    dest = os.path.join(dest_dir, filename)
    seq = 2
    while os.path.exists(dest):
        filename = f"inventory_{stamp}{suffix}_{seq}.db"
        dest = os.path.join(dest_dir, filename)
        seq += 1
    try:
        os.makedirs(dest_dir, exist_ok=True)
        probe = os.path.join(dest_dir, ".probe")   # 可写自检：失败要报出来，不能静默
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        src = sqlite3.connect(source)
        try:
            dst = sqlite3.connect(dest)
            try:
                with dst:
                    src.backup(dst)  # 在线备份：包含 WAL 中未落盘的数据
            finally:
                dst.close()
        finally:
            src.close()
        size = os.path.getsize(dest)
        _verify_file(dest)  # 备份写完立刻验证，不要等到真要恢复时才发现是坏的
        _mark_success(dest)
        return {"filename": filename, "path": dest, "size_bytes": size,
                "created_at": timeutil.now_iso(), "trigger": trigger}
    except Exception as exc:
        _mark_failure(str(exc))
        raise


def _verify_file(path: str) -> dict:
    try:
        check = sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise RestoreError(f"备份文件无法打开：{exc}") from exc
    try:
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:  # 不是数据库文件（比如被截断/写了垃圾）
            raise RestoreError(f"备份文件不是有效的 SQLite 数据库：{exc}") from exc
        if str(result).lower() != "ok":
            raise RestoreError(f"备份完整性检查未通过：{result}")
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise RestoreError(f"备份缺少表：{sorted(missing)}")
        counts = {"stock_logs": check.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0],
                  "products": check.execute("SELECT COUNT(*) FROM products").fetchone()[0]}
        return {"tables": sorted(tables), "counts": counts}
    finally:
        check.close()


def list_backups(directory: str | None = None) -> list[dict]:
    """备份列表。字段严格对齐 docs/03 7.4 的 `BackupInfo`：filename/size_bytes/created_at/trigger。

    ★`created_at` 必须出参：前端列表直接按它展示，缺字段会让整页渲染抛异常变白屏
      （这不是前端能兜住的，BackupInfo 是契约的一部分）。
    ★`trigger` 从文件名后缀反转（`_manual` / `_prerestore` / 无后缀=auto）——文件名由
      create_backup 生成时带了后缀，这里不再另存元数据，避免出现"文件在、元数据丢"。
    """
    d = directory if directory is not None else backups_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        if NAME_RE.match(name) and name.endswith(".db"):
            full = os.path.join(d, name)
            stat = os.stat(full)
            out.append({"filename": name, "path": full, "size_bytes": stat.st_size,
                        "created_at": timeutil.iso_from_epoch(stat.st_mtime),
                        "trigger": _trigger_of(name),
                        "mtime_epoch": stat.st_mtime})
    # 按「名字里的时间戳」倒序，而不是按文件名字典序：
    # 文件名含可选后缀（_manual / _2），字典序会把 inventory_..._183836.db 排到
    # inventory_..._183836_manual.db 之后 —— 列表顺序错了，人会恢复错文件（运维事故级）。
    return sorted(out, key=_sort_key, reverse=True)


_STAMP_RE = re.compile(r"inventory_(\d{8})_(\d{6})")

#: 文件名后缀 → 触发方式。create_backup 生成后缀时已剔除下划线（pre_restore → prerestore），
#: 这里映射回 docs/03 的枚举值，保证界面显示的是 `pre_restore` 而不是 `prerestore`。
_TRIGGER_LABELS = {"": "auto", "manual": "manual",
                   "prerestore": "pre_restore", "premigration": "pre_migration"}
_SUFFIX_RE = re.compile(r"^inventory_\d{8}_\d{6}(?:_(?P<tag>[a-z]+))?(?:_\d+)?\.db$")


def _trigger_of(filename: str) -> str:
    """从文件名解析触发方式；无法识别时原样回传后缀（不猜、不报错）。"""
    m = _SUFFIX_RE.match(filename)
    tag = (m.group("tag") or "") if m else ""
    return _TRIGGER_LABELS.get(tag, tag or "auto")


def _sort_key(item: dict):
    """时间戳优先；解析不出时间戳时退回 mtime，保证排序永不抛异常。"""
    m = _STAMP_RE.search(item["filename"])
    if m:
        base = f"{m.group(1)}{m.group(2)}"
    else:
        base = time.strftime("%Y%m%d%H%M%S", time.localtime(item.get("mtime_epoch") or 0))
    return (base, item["filename"])


def prune_old_backups(directory: str | None = None, keep: int = KEEP_DEFAULT) -> int:
    """保留最近 keep 份（每月首份另留 12 个月由调用方策略决定，此处先做简单轮转）。"""
    items = list_backups(directory)
    extra = [x for x in items[keep:]]
    removed = 0
    for item in extra:
        try:
            os.remove(item["path"])
            removed += 1
        except OSError as exc:
            log.warning("清理旧备份失败 %s: %s", item["filename"], exc)
    return removed


def restore_backup(source_file: str, target_db: str | None = None) -> dict:
    """从备份恢复。调用前必须已 create_backup(trigger='pre_restore') 保住当前库。"""
    if not os.path.isfile(source_file):
        raise RestoreError(f"备份文件不存在：{source_file}")
    _verify_file(source_file)  # 先验再恢复（坏文件不许覆盖现库）
    live = target_db or db_path()
    conn = sqlite3.connect(source_file)
    try:
        dst = sqlite3.connect(live)
        try:
            with dst:
                conn.backup(dst)
        finally:
            dst.close()
    finally:
        conn.close()
    _verify_file(live)
    log.warning("已从备份恢复：%s → %s", os.path.basename(source_file), live,
                extra={"action": "backup.restore", "status": "success"})
    return {"restored_from": os.path.basename(source_file), "target": live}


def backup_loop(app=None, interval_seconds: int = 3600, at: str = "03:00", keep: int = KEEP_DEFAULT) -> str:
    """到点自动备份（守护线程主体）。返回本轮等待秒数，便于测试注入。"""
    wait = _seconds_until(at)
    time.sleep(wait)
    try:
        info = create_backup(trigger="auto")
        prune_old_backups(keep=keep)
        return info["filename"]
    except Exception as exc:
        _mark_failure(str(exc))
        return f"error:{exc}"


def start_backup_thread(app) -> threading.Thread | None:
    """自动备份开关关闭时不创建线程（PRD 5.9 运行时可调，无需重启）。"""
    from models import settings_repo
    from models.database import connect
    conn = connect(app.config["DB_PATH"])
    try:
        if str(settings_repo.get(conn, "backup_enabled", "true")).lower() != "true":
            return None
    finally:
        conn.close()
    thread = threading.Thread(target=_backup_daemon, name="auto-backup", daemon=True)
    thread.start()
    return thread


def _backup_daemon() -> None:
    while True:  # 到点即备份，失败不退出（下次仍会尝试）
        try:
            backup_loop()
        except Exception as exc:  # 兜底：备份线程绝不能悄悄死掉
            log.critical("自动备份线程异常：%s", exc)
            time.sleep(60)


def _seconds_until(at: str, now=None) -> int:
    try:
        hh, mm = (int(x) for x in at.split(":", 1))
    except (TypeError, ValueError):
        hh, mm = 3, 0
    now = now or timeutil.now_dt()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1, int((target - now).total_seconds()))
