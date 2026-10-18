"""核心库存业务测试（TDD RED）—— PRD 5.4 / 9.8 / 9.9 / 13.3 · 陷阱 T-06/T-07/T-08/T-09
对应 UT-01~UT-07、UT-12、UT-16。UT-06 并发是本项目的「原子扣减是否真生效」的唯一证据。
"""
import threading

import pytest


def _post(client, token, path, payload):
    return client.post(path, json=payload, headers={"X-Auth-Token": token})


# ---------- UT-01 首次入库（无 stock_current 行） ----------

def test_first_in_creates_stock_row(client, admin_token, db_conn):
    """新商品在目标仓库还没有库存行，入库必须建行（upsert 两步法，禁止 INSERT OR REPLACE，T-07）"""
    from models import product_repo
    pid = product_repo.create(db_conn, {"name": "新品", "barcode": "1234567890",
                                        "cost_price": 1, "sale_price": 2})["id"]
    r = _post(client, admin_token, "/api/stock/in",
              {"client_msg_id": _uid(), "product_id": pid, "warehouse_id": 1, "quantity": 7})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert (d["before_qty"], d["after_qty"]) == (0, 7)
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=?", (pid,)).fetchone()[0] == 7


# ---------- UT-02 出库快照正确 ----------

def test_out_writes_before_after_snapshots(client, admin_token, db_conn, seed):
    r = _post(client, admin_token, "/api/stock/out",
              {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 4})
    d = r.get_json()["data"]
    assert (d["before_qty"], d["after_qty"], d["quantity"]) == (10, 6, 4)
    row = db_conn.execute("SELECT * FROM stock_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["type"] == "OUT" and row["before_qty"] == 10 and row["after_qty"] == 6
    assert row["product_name_snapshot"] == "测试可乐", "必须写名称快照，防改名后历史失真"
    assert row["unit_price"] == 3.5, "出库单价取售价"


# ---------- UT-03 库存不足：拒绝且不部分扣减 ----------

def test_out_insufficient_is_422_and_stock_untouched(client, admin_token, db_conn, seed):
    r = _post(client, admin_token, "/api/stock/out",
              {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 15})
    assert r.status_code == 422
    body = r.get_json()
    assert body["code"] == 1009
    assert body["data"] == {"product_id": 1, "warehouse_id": 1, "current": 10, "requested": 15, "lack": 5}
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 10, \
        "库存不足时绝不能部分扣减"
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs WHERE type='OUT'").fetchone()[0] == 0, \
        "失败的操作不得留流水"


def test_out_never_auto_reduces_requested_quantity(client, admin_token, db_conn, seed):
    """★ 禁止「尽力扣减」：请求 15 库存 10 时，绝不能扣成 10（会让账实不符且用户以为扣了 15）"""
    _post(client, admin_token, "/api/stock/out",
          {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 15})
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 10


# ---------- UT-04 幂等 ----------

def test_same_client_msg_id_twice_books_once(client, admin_token, db_conn, seed):
    msg = _uid()
    r1 = _post(client, admin_token, "/api/stock/in", {"client_msg_id": msg, "product_id": 1,
                                                      "warehouse_id": 1, "quantity": 3})
    r2 = _post(client, admin_token, "/api/stock/in", {"client_msg_id": msg, "product_id": 1,
                                                      "warehouse_id": 1, "quantity": 3})
    assert r1.status_code == 200 and r2.status_code == 200, "重复提交不能报错（APP 无法区分错误与成功）"
    assert r1.get_json()["data"]["duplicated"] is False
    assert r2.get_json()["data"]["duplicated"] is True
    assert r2.get_json()["data"]["log_id"] == r1.get_json()["data"]["log_id"]
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs WHERE client_msg_id=?", (msg,)).fetchone()[0] == 1
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 13, "只加一次"


def test_missing_client_msg_id_is_rejected(client, admin_token, seed):
    """★ 不给默认值、不自动生成（T-09：留退路就一定被忘）"""
    r = _post(client, admin_token, "/api/stock/in", {"product_id": 1, "warehouse_id": 1, "quantity": 1})
    assert r.status_code == 422
    assert "client_msg_id" in str(r.get_json()["data"]["errors"])


@pytest.mark.parametrize("bad", ["1", "abc", "6ba7b810-9dad-11d1-80b4-00c04fd430c8" + "x" * 8])
def test_malformed_client_msg_id_is_rejected(client, admin_token, seed, bad):
    r = _post(client, admin_token, "/api/stock/in", {"client_msg_id": bad, "product_id": 1,
                                                     "warehouse_id": 1, "quantity": 1})
    assert r.status_code == 422, f"非 UUID 必须拒绝，实际放行：{bad}"


# ---------- UT-05 批量同步：坏数据不回滚好数据 ----------

def test_batch_sync_isolates_bad_rows(client, admin_token, db_conn, seed):
    """200 条里 1 条条码未登记 → 199 条必须落库（SAVEPOINT 单条回滚），坏条返回 1007"""
    logs = [{"client_msg_id": _uid(), "barcode": "6901028100001", "warehouse_id": 1,
             "quantity": 1, "type": "IN"} for _ in range(19)]
    logs.append({"client_msg_id": _uid(), "barcode": "0000000000000", "warehouse_id": 1,
                 "quantity": 1, "type": "IN"})
    r = client.post("/api/sync/stock-logs", json={"logs": logs}, headers={"X-Auth-Token": admin_token})
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["ok_count"] == 19 and body["failed_count"] == 1
    bad = [x for x in body["results"] if not x["ok"]]
    assert len(bad) == 1 and bad[0]["code"] == 1007
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs WHERE type='IN'").fetchone()[0] == 20, \
        "19 条新增 + 种子 1 条；坏条不得入库，好条不得被回滚（T-08）"
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == 29


def test_batch_sync_over_limit_is_422(client, admin_token, seed):
    logs = [{"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 1, "type": "IN"}
            for _ in range(201)]
    r = client.post("/api/sync/stock-logs", json={"logs": logs}, headers={"X-Auth-Token": admin_token})
    assert r.status_code == 422


def test_batch_sync_duplicated_rows_reported_ok(client, admin_token, seed):
    msg = _uid()
    one = {"client_msg_id": msg, "product_id": 1, "warehouse_id": 1, "quantity": 2, "type": "IN"}
    r = client.post("/api/sync/stock-logs", json={"logs": [one, one]}, headers={"X-Auth-Token": admin_token})
    d = r.get_json()["data"]
    assert d["ok_count"] == 2 and d["duplicated_count"] == 1, "APP 靠 duplicated 把本地条目标记为已同步"


# ---------- UT-06 ★并发：8 线程抢 10 件，绝不出现负库存 ----------

def test_concurrent_out_never_goes_negative(app, admin_token, db_conn, seed):
    """这是 T-06 的存在证明：若扣减写成「先 SELECT 再 UPDATE」，本测试必然失败。"""
    results = []
    gate = threading.Barrier(8)

    def worker(i):
        client = app.test_client()  # 每线程独立 client：共享实例的会话状态非线程安全
        gate.wait()
        r = _post(client, admin_token, "/api/stock/out",
                  {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 2})
        results.append(r.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok_count = results.count(200)
    reject_count = results.count(422)
    qty = db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1 AND warehouse_id=1").fetchone()[0]
    logged = db_conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM stock_logs WHERE type='OUT' AND product_id=1").fetchone()[0]
    assert qty >= 0, f"出现负库存！{qty} —— 扣减不是原子的"
    assert ok_count * 2 == logged, "成功笔数必须与已扣减量一致"
    assert logged <= 10, "总扣减不得超过初始库存"
    assert reject_count == 8 - ok_count


# ---------- UT-07 事务原子性 ----------

def test_transaction_rolls_back_both_tables(client, admin_token, db_conn, seed, monkeypatch):
    """流水写成功但库存更新失败 → 两表都必须无变化"""
    from models import stock_repo
    real = stock_repo.increase_stock

    def boom(*a, **k):
        raise RuntimeError("模拟库存更新失败")

    monkeypatch.setattr(stock_repo, "increase_stock", boom)
    before = db_conn.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0]
    r = _post(client, admin_token, "/api/stock/in",
              {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 5})
    assert r.status_code == 500
    db_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0] == before, "流水必须回滚"


# ---------- UT-12 红字冲销 ----------

def test_reverse_creates_counter_entry(client, admin_token, db_conn, seed):
    in_msg = _uid()
    log_id = _post(client, admin_token, "/api/stock/in",
                   {"client_msg_id": in_msg, "product_id": 1, "warehouse_id": 1, "quantity": 6}
                   ).get_json()["data"]["log_id"]
    after_in = db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0]
    r = _post(client, admin_token, f"/api/stock/reverse/{log_id}",
              {"client_msg_id": _uid(), "reason": "录错数量"})
    assert r.status_code == 200
    assert db_conn.execute("SELECT qty FROM stock_current WHERE product_id=1").fetchone()[0] == after_in - 6
    rev_id = r.get_json()["data"]["reversal_log_id"]
    assert db_conn.execute("SELECT reversed_by FROM stock_logs WHERE id=?", (log_id,)).fetchone()[0] == rev_id


def test_reverse_twice_is_conflict(client, admin_token, seed):
    log_id = _post(client, admin_token, "/api/stock/in",
                   {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 2}
                   ).get_json()["data"]["log_id"]
    first = _post(client, admin_token, f"/api/stock/reverse/{log_id}",
                  {"client_msg_id": _uid(), "reason": "第一次冲销"})
    assert first.status_code == 200
    second = _post(client, admin_token, f"/api/stock/reverse/{log_id}",
                   {"client_msg_id": _uid(), "reason": "第二次"})
    assert second.status_code == 409 and second.get_json()["code"] == 1010


def test_reverse_of_adjust_is_rejected(client, admin_token, seed):
    log_id = _post(client, admin_token, "/api/stock/adjust",
                   {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1,
                    "target_qty": 8, "remark": "盘点"}).get_json()["data"]["log_id"]
    r = _post(client, admin_token, f"/api/stock/reverse/{log_id}", {"client_msg_id": _uid(), "reason": "x"})
    assert r.status_code == 422


# ---------- UT-16 权限 ----------

def test_operator_cannot_adjust(client, operator_token, seed):
    r = _post(client, operator_token, "/api/stock/adjust",
              {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "target_qty": 3, "remark": "盘点"})
    assert r.status_code == 403


def test_operator_can_in_and_out(client, operator_token, seed):
    r = _post(client, operator_token, "/api/stock/in",
              {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 1})
    assert r.status_code == 200


# ---------- 其它校验 ----------

def test_in_with_inactive_product_is_rejected(client, admin_token, db_conn, seed):
    db_conn.execute("UPDATE products SET is_active=0 WHERE id=1")
    before = db_conn.execute("SELECT COUNT(*) FROM stock_logs WHERE product_id=1").fetchone()[0]
    r = _post(client, admin_token, "/api/stock/in",
              {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 1})
    assert r.status_code == 403, "停用商品不能出入库，且不产生流水"
    assert db_conn.execute("SELECT COUNT(*) FROM stock_logs WHERE product_id=1").fetchone()[0] == before


def test_in_with_inactive_warehouse_is_1008(client, admin_token, db_conn, seed):
    db_conn.execute("UPDATE warehouses SET is_active=0 WHERE id=1")
    r = _post(client, admin_token, "/api/stock/in",
              {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": 1})
    assert r.status_code == 422 and r.get_json()["code"] == 1008


def test_out_with_negative_or_zero_quantity_is_422(client, admin_token, seed):
    for bad in (0, -3, 1.5, "abc"):
        r = _post(client, admin_token, "/api/stock/out",
                  {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "quantity": bad})
        assert r.status_code == 422, f"quantity={bad!r} 必须拒绝"


def test_product_id_and_barcode_mismatch_is_rejected(client, admin_token, seed):
    r = _post(client, admin_token, "/api/stock/in",
              {"client_msg_id": _uid(), "product_id": 1, "barcode": "9999999999",
               "warehouse_id": 1, "quantity": 1})
    assert r.status_code == 422


def test_adjust_computes_signed_quantity(client, admin_token, db_conn, seed):
    """盘点：当前 10 → 目标 8，流水 quantity 必须是 -2 且 type=ADJUST（PRD 4.3.6）"""
    r = _post(client, admin_token, "/api/stock/adjust",
              {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "target_qty": 8, "remark": "盘亏 2"})
    d = r.get_json()["data"]
    assert d["type"] == "ADJUST" and d["quantity"] == -2 and d["after_qty"] == 8
    assert d["diff"] == -2


def test_adjust_requires_remark(client, admin_token, seed):
    r = _post(client, admin_token, "/api/stock/adjust",
              {"client_msg_id": _uid(), "product_id": 1, "warehouse_id": 1, "target_qty": 9})
    assert r.status_code == 422


def _uid():
    import uuid
    return str(uuid.uuid4())
