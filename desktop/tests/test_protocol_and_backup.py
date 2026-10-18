"""Day2 测试（TDD RED）—— docs/03 第 8 章 UDP 协议 · PRD 15.3 备份 · UT-18 / UT-19

UDP 报文与备份是双端契约和数据保全的命脉，二者都必须用真实字节验证，不能用mock糊过去。
"""
import os
import sqlite3

import pytest

from net.protocol import build_payload, parse_payload


# ---------- UDP 报文（协议一旦发出就被 APP 依赖）----------

def test_payload_exact_wire_format():
    got = build_payload(ip="192.168.1.10", port=5000, name="仓库电脑")
    assert isinstance(got, bytes), "广播要发字节，不是字符串"
    assert got.decode("utf-8") == "INVENTORY_SERVER v1.0 IP:192.168.1.10 PORT:5000 NAME:仓库电脑"


def test_payload_omits_name_when_absent():
    assert build_payload(ip="10.0.0.5", port=5001).decode("ascii") == \
        "INVENTORY_SERVER v1.0 IP:10.0.0.5 PORT:5001"


def test_payload_never_exceeds_512_bytes():
    long_name = "很长的服务器名字" * 20
    got = build_payload(ip="192.168.1.10", port=5000, name=long_name)
    assert len(got) <= 512, f"报文 {len(got)} 字节，超过协议上限"


def test_parse_roundtrip_with_space_in_name():
    """NAME 可含空格，解析规则是「取 NAME: 后到行尾」（T-12）"""
    raw = build_payload(ip="192.168.1.10", port=5000, name="My Warehouse PC")
    parsed = parse_payload(raw.decode("utf-8"))
    assert parsed == {"ip": "192.168.1.10", "port": 5000, "name": "My Warehouse PC"}


def test_parse_ignores_unknown_keys():
    """向前兼容：新增字段不得让旧 APP 崩掉"""
    parsed = parse_payload("INVENTORY_SERVER v1.0 IP:192.168.1.9 PORT:5000 FUTURE:abc NAME:x")
    assert parsed["ip"] == "192.168.1.9" and parsed["port"] == 5000 and parsed["name"] == "x"


@pytest.mark.parametrize("bad", [
    "OTHER_APP v1.0 IP:1.1.1.1 PORT:5000",       # 魔数不对
    "INVENTORY_SERVER v2.0 IP:192.168.1.9 PORT:5000",  # 版本不对（本期只认 1.0）
    "INVENTORY_SERVER v1.0 PORT:5000",            # 缺 IP
    "INVENTORY_SERVER v1.0 IP:192.168.1.9",       # 缺 PORT
    "INVENTORY_SERVER v1.0 IP:192.168.1.9 PORT:abc",  # PORT 非法
    "INVENTORY_SERVER v1.0 IP:999.1.1.1 PORT:5000",  # IP 非法
    "", "   ",
])
def test_malformed_payload_is_dropped(bad):
    assert parse_payload(bad) is None, f"非法报文必须整包丢弃，实际被接受：{bad!r}"


def test_parse_returns_int_port():
    assert parse_payload("INVENTORY_SERVER v1.0 IP:10.1.2.3 PORT:5107")["port"] == 5107


def test_broadcast_targets_subnet_first_then_global():
    """T-13：部分路由器不回全局广播，必须先试子网定向广播"""
    from net.local_ip import subnet_broadcast_ip
    assert subnet_broadcast_ip("192.168.1.10") == "192.168.1.255"
    assert subnet_broadcast_ip("10.20.30.40") == "10.20.30.255"


def test_private_ip_selection_skips_loopback_and_link_local():
    from net.local_ip import is_usable_ip
    assert is_usable_ip("192.168.1.10") is True
    assert is_usable_ip("127.0.0.1") is False
    assert is_usable_ip("169.254.1.1") is False
    assert is_usable_ip("8.8.8.8") is False, "公网 IP 不能当局域网地址（PRD 10.1 拒绝公网绑定）"


def test_private_ip_priority_order():
    from net.local_ip import rank_candidates
    ranked = rank_candidates(["172.18.0.1", "10.1.1.5", "192.168.1.10", "127.0.0.1"])
    assert ranked[0] == "192.168.1.10", f"192.168 应优先（家用路由最常见），实际 {ranked}"
    assert "127.0.0.1" not in ranked


# ---------- 备份：WAL 下必须用在线备份 API（T-04）----------

def test_backup_file_is_a_valid_database_with_same_data(db_conn, db_path, seed, tmp_path):
    from services.backup_service import create_backup
    info = create_backup(trigger="manual", target_dir=str(tmp_path), source_db=db_path)
    assert os.path.isfile(info["path"])
    check = sqlite3.connect(info["path"])
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert check.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0] == 1
    assert check.execute("SELECT name FROM products WHERE id=1").fetchone()[0] == "测试可乐"
    check.close()


def test_backup_includes_uncheckpointed_wal_data(db_conn, db_path, seed, tmp_path):
    """★ T-04：WAL 里未 checkpoint 的数据不在 .db 主文件中，直接拷文件会丢最近写入"""
    from services import timeutil
    from services.backup_service import create_backup
    db_conn.execute(
        "INSERT INTO stock_logs(client_msg_id,product_id,warehouse_id,operator_id,type,quantity,"
        "before_qty,after_qty,product_name_snapshot,created_at) VALUES('late-write',1,1,1,'IN',1,10,11,'x',?)",
        (timeutil.now_iso(),))
    info = create_backup(trigger="manual", target_dir=str(tmp_path), source_db=db_path)
    check = sqlite3.connect(info["path"])
    assert check.execute("SELECT COUNT(*) FROM stock_logs WHERE client_msg_id='late-write'").fetchone()[0] == 1
    check.close()


def test_backup_names_are_unique_and_timestamped(db_conn, db_path, seed, tmp_path):
    """同秒内的两次备份必须是两个文件（互相覆盖＝丢一份），命名可解析出时间。
       顺序本身交给 test_backup_list_orders_by_real_time_not_string_luck 用真实时间差验证。"""
    from services.backup_service import create_backup, list_backups
    a = create_backup(trigger="manual", target_dir=str(tmp_path), source_db=db_path)
    b = create_backup(trigger="auto", target_dir=str(tmp_path), source_db=db_path)
    names = [x["filename"] for x in list_backups(str(tmp_path))]
    assert len(names) == len(set(names)) == 2, f"同秒备份撞名会互相覆盖：{names}"
    assert a["filename"].startswith("inventory_") and a["filename"].endswith(".db")
    assert all(n.startswith("inventory_2") for n in names)


def test_backup_prunes_to_keep_count(db_conn, db_path, seed, tmp_path):
    from services.backup_service import create_backup, list_backups, prune_old_backups
    for _ in range(9):
        create_backup(trigger="auto", target_dir=str(tmp_path), source_db=db_path)
    removed = prune_old_backups(str(tmp_path), keep=7)
    assert removed == 2
    assert len(list_backups(str(tmp_path))) == 7


def test_restore_verifies_before_accepting(tmp_path):
    """恢复必须校验（integrity_check + 表齐全），坏文件不能假装成功"""
    from services.backup_service import RestoreError, restore_backup
    bogus = tmp_path / "inventory_20260101_000000.db"
    bogus.write_bytes(b"this is not a database")
    with pytest.raises(RestoreError):
        restore_backup(str(bogus), target_db=str(tmp_path / "live.db"))


def test_backup_failure_is_recorded_not_silent(db_conn, db_path, seed, tmp_path):
    """自用系统最坏的是静默失败：失败必须留下可读状态供仪表盘告警（PRD 15.3）"""
    from services.backup_service import create_backup, status
    blocker = tmp_path / "blocker"
    blocker.write_text("这是一个文件，不是目录", encoding="utf-8")
    before = status()
    with pytest.raises(Exception):
        # 备份目录路径被文件占住 → makedirs 必然失败，这是生产环境真实会遇到的情况（磁盘/权限/命名冲突）
        create_backup(trigger="manual", target_dir=str(blocker / "sub"), source_db=db_path)
    after = status()
    assert after["consecutive_failures"] >= 1, "失败必须留下可读状态，供仪表盘红条告警（不许静默）"
    assert after["last_error"]


def test_public_ip_payload_is_dropped():
    """本系统禁止绑定/连向公网地址（PRD 10.1），广播解析也必须拒掉它"""
    assert parse_payload("INVENTORY_SERVER v1.0 IP:8.8.8.8 PORT:5000") is None


def test_backup_list_orders_by_real_time_not_string_luck(db_conn, db_path, seed, tmp_path):
    """排序正确性是运维问题：列表顺序错了，恢复时就会挑错文件。
       混合命名（带 / 不带 trigger 后缀、带 _2 序号）必须仍按时间倒序。"""
    import time as _t

    from services.backup_service import create_backup, list_backups
    first = create_backup(trigger="auto", target_dir=str(tmp_path), source_db=db_path)
    _t.sleep(1.05)
    second = create_backup(trigger="manual", target_dir=str(tmp_path), source_db=db_path)
    names = [x["filename"] for x in list_backups(str(tmp_path))]
    assert names[0] == second["filename"], f"最新备份必须排第一，实际顺序：{names}"
    assert names[-1] == first["filename"]
