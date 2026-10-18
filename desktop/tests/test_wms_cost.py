"""P1-5 成本核算（移动加权平均）—— docs/14 第 7 章 · 陷阱 T-06 / T-21

覆盖：
  1. 移动加权：10@10 + 10@20 → 均价 15、金额 300，且 unit_cost 与 unit_price 分列
  2. 出库成本 = 出库前均价，均价不变
  3. 冲销出库按原成本加回，均价回到 15
  4. 冲销入库减回原成本，库存归零时均价归零且 remark 留痕
  5. 调拨：目标仓 unit_cost 取源仓成本（即使明细填了别的 unit_price）
  6. 盘点：unit_cost 取当前均价，均价不变
  7. 以上全部操作后 recalc 无 diff（实时写入与全量重放同源）
  8. 成本列进入不可变触发器（账本口径不得被事后改写）
  9. 人为改坏 avg_cost 能被 recalc 检出并修复
"""
import sqlite3
import uuid

import pytest


def _uid() -> str:
    return str(uuid.uuid4())


def _post(client, token, path, payload):
    return client.post(path, json=payload, headers={"X-Auth-Token": token})


def _new_product(conn, cost_price: float, name: str = "成本测试品") -> int:
    from models import product_repo
    return product_repo.create(conn, {"name": name, "barcode": "C" + uuid.uuid4().hex[:10],
                                      "cost_price": cost_price, "sale_price": cost_price + 1})["id"]


def _new_warehouse(conn, name: str) -> int:
    from services import timeutil
    now = timeutil.now_iso()
    return int(conn.execute("INSERT INTO warehouses(name,created_at,updated_at) VALUES(?,?,?)",
                            (name, now, now)).lastrowid)


def _set_cost_price(conn, product_id: int, price: float) -> None:
    conn.execute("UPDATE products SET cost_price=? WHERE id=?", (price, product_id))


def _stock_in(client, token, product_id: int, warehouse_id: int, quantity: int) -> dict:
    r = _post(client, token, "/api/stock/in",
              {"client_msg_id": _uid(), "product_id": product_id,
               "warehouse_id": warehouse_id, "quantity": quantity})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def _stock_out(client, token, product_id: int, warehouse_id: int, quantity: int) -> dict:
    r = _post(client, token, "/api/stock/out",
              {"client_msg_id": _uid(), "product_id": product_id,
               "warehouse_id": warehouse_id, "quantity": quantity})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def _log(db_conn, log_id: int) -> dict:
    return dict(db_conn.execute("SELECT * FROM stock_logs WHERE id=?", (log_id,)).fetchone())


def _avg(db_conn, product_id: int, warehouse_id: int) -> float:
    row = db_conn.execute("SELECT avg_cost FROM stock_current WHERE product_id=? AND warehouse_id=?",
                          (product_id, warehouse_id)).fetchone()
    return round(float(row["avg_cost"]), 4) if row else 0.0


def _qty(db_conn, product_id: int, warehouse_id: int) -> int:
    row = db_conn.execute("SELECT qty FROM stock_current WHERE product_id=? AND warehouse_id=?",
                          (product_id, warehouse_id)).fetchone()
    return int(row["qty"]) if row else 0


# ---------- 1. 移动加权 + 两个口径分列 ----------

def test_inbound_weighted_average_and_split_accounting(client, admin_token, db_conn, seed):
    pid = _new_product(db_conn, 10)
    wid = _new_warehouse(db_conn, "成本仓A")

    _stock_in(client, admin_token, pid, wid, 10)          # 10 @ 10
    _set_cost_price(db_conn, pid, 20)                      # 下次采购价 20
    second = _stock_in(client, admin_token, pid, wid, 10)  # 10 @ 20

    assert _qty(db_conn, pid, wid) == 20
    assert _avg(db_conn, pid, wid) == 15, "移动加权：(10*10 + 10*20) / 20 = 15"

    row = _log(db_conn, second["log_id"])
    assert (row["unit_cost"], row["cost_amount"]) == (20.0, 200.0)
    assert (row["unit_price"], row["amount"]) == (20.0, 200.0), "采购时两个口径数值相同但必须分列"

    total_cost = db_conn.execute(
        "SELECT SUM(cost_amount) FROM stock_logs WHERE product_id=?", (pid,)).fetchone()[0]
    assert round(total_cost, 2) == 300.0, "库存成本金额 = 100 + 200"


# ---------- 2. 出库成本 = 出库前均价 ----------

def test_outbound_uses_avg_cost_and_keeps_average(client, admin_token, db_conn, seed):
    pid = _new_product(db_conn, 10)
    wid = _new_warehouse(db_conn, "成本仓B")
    _stock_in(client, admin_token, pid, wid, 10)
    _set_cost_price(db_conn, pid, 20)
    _stock_in(client, admin_token, pid, wid, 10)

    out = _stock_out(client, admin_token, pid, wid, 5)
    row = _log(db_conn, out["log_id"])
    assert (row["unit_cost"], row["cost_amount"]) == (15.0, 75.0)
    assert row["unit_price"] == 11.0, "出库结算价仍是售价（11），与成本 15 分列"
    assert _avg(db_conn, pid, wid) == 15, "出库不改变均价"


# ---------- 3. 冲销出库：按原成本加回 ----------

def test_reverse_out_restores_qty_and_average(client, admin_token, db_conn, seed):
    pid = _new_product(db_conn, 10)
    wid = _new_warehouse(db_conn, "成本仓C")
    _stock_in(client, admin_token, pid, wid, 10)
    _set_cost_price(db_conn, pid, 20)
    _stock_in(client, admin_token, pid, wid, 10)
    out = _stock_out(client, admin_token, pid, wid, 5)

    r = _post(client, admin_token, f"/api/stock/reverse/{out['log_id']}",
              {"client_msg_id": _uid(), "reason": "记错数量"})
    assert r.status_code == 200, r.get_json()

    assert _qty(db_conn, pid, wid) == 20
    assert _avg(db_conn, pid, wid) == 15, "冲销出库按原成本 15 加回"
    reversal = _log(db_conn, r.get_json()["data"]["reversal_log_id"])
    assert (reversal["type"], reversal["unit_cost"]) == ("IN", 15.0)
    assert reversal["cost_amount"] == 75.0


# ---------- 4. 冲销入库：减回原成本；归零时均价归零并留痕 ----------

def test_reverse_in_subtracts_cost_and_zeroes_average_when_empty(client, admin_token, db_conn, seed):
    pid = _new_product(db_conn, 10)
    wid = _new_warehouse(db_conn, "成本仓D")
    first = _stock_in(client, admin_token, pid, wid, 10)

    r = _post(client, admin_token, f"/api/stock/reverse/{first['log_id']}",
              {"client_msg_id": _uid(), "reason": "入库单录错"})
    assert r.status_code == 200, r.get_json()

    assert _qty(db_conn, pid, wid) == 0
    assert _avg(db_conn, pid, wid) == 0, "库存归零时均价必须归零（除零不能靠异常兜底）"
    reversal = _log(db_conn, r.get_json()["data"]["reversal_log_id"])
    assert (reversal["type"], reversal["unit_cost"]) == ("OUT", 10.0)
    assert "均价归零" in reversal["remark"], "除零归零要写进 remark，便于事后审计"


# ---------- 5. 调拨：目标仓成本取源仓 ----------

def test_transfer_in_uses_source_cost_not_settlement_price(client, app, admin_token, db_conn, seed):
    from services import stock_service

    pid = _new_product(db_conn, 10)
    src = _new_warehouse(db_conn, "调出仓")
    dst = _new_warehouse(db_conn, "调入仓")
    _stock_in(client, admin_token, pid, src, 5)       # 5 @ 10
    _set_cost_price(db_conn, pid, 20)
    _stock_in(client, admin_token, pid, src, 5)       # 再 5 @ 20 → 源仓均价 15

    _set_cost_price(db_conn, pid, 8)
    _stock_in(client, admin_token, pid, dst, 5)       # 目标仓 5 @ 8 → 均价 8

    bill_id = 9001
    with app.app_context():
        stock_service.post_bill_lines(
            [{"client_msg_id": _uid(), "product_id": pid, "warehouse_id": src,
              "direction": "OUT", "quantity": 5}],
            {"id": 1}, "TRANSFER_OUT", bill_id)
        in_views = stock_service.post_bill_lines(
            [{"client_msg_id": _uid(), "product_id": pid, "warehouse_id": dst,
              "direction": "IN", "quantity": 5, "unit_price": 999}],
            {"id": 1}, "TRANSFER_IN", bill_id)

    row = _log(db_conn, in_views[0]["log_id"])
    assert row["unit_price"] == 999.0, "结算价照单据落库"
    assert row["unit_cost"] == 15.0, "★ 成本必须取源仓出库成本，不能取明细单价"
    assert _avg(db_conn, pid, dst) == 11.5, "(5*8 + 5*15) / 10 = 11.5"


# ---------- 6. 盘点：成本取当前均价，均价不变 ----------

def test_adjust_cost_uses_current_average_and_keeps_it(client, admin_token, db_conn, seed):
    pid = _new_product(db_conn, 10)
    wid = _new_warehouse(db_conn, "盘点仓")
    _stock_in(client, admin_token, pid, wid, 10)

    gain = _post(client, admin_token, "/api/stock/adjust",
                 {"client_msg_id": _uid(), "product_id": pid, "warehouse_id": wid,
                  "target_qty": 15, "remark": "盘盈"})
    assert gain.status_code == 200, gain.get_json()
    gain_log = _log(db_conn, gain.get_json()["data"]["log_id"])
    assert (gain_log["quantity"], gain_log["unit_cost"]) == (5, 10.0)
    assert _avg(db_conn, pid, wid) == 10

    loss = _post(client, admin_token, "/api/stock/adjust",
                 {"client_msg_id": _uid(), "product_id": pid, "warehouse_id": wid,
                  "target_qty": 3, "remark": "盘亏"})
    assert loss.status_code == 200, loss.get_json()
    loss_log = _log(db_conn, loss.get_json()["data"]["log_id"])
    assert (loss_log["quantity"], loss_log["unit_cost"]) == (-12, 10.0)
    assert _avg(db_conn, pid, wid) == 10, "盘点不改变均价"


# ---------- 7. 实时写入与全量重放同源：recalc 无 diff ----------

def test_recalc_has_no_cost_diff_after_operations(client, app, admin_token, db_conn, seed):
    from services import stock_service

    pid = _new_product(db_conn, 10)
    wid = _new_warehouse(db_conn, "对账仓")
    _stock_in(client, admin_token, pid, wid, 10)
    _set_cost_price(db_conn, pid, 20)
    _stock_in(client, admin_token, pid, wid, 10)
    out = _stock_out(client, admin_token, pid, wid, 4)
    _post(client, admin_token, f"/api/stock/reverse/{out['log_id']}",
          {"client_msg_id": _uid(), "reason": "纠正"})
    _post(client, admin_token, "/api/stock/adjust",
          {"client_msg_id": _uid(), "product_id": pid, "warehouse_id": wid,
           "target_qty": 18, "remark": "盘点"})
    _stock_out(client, admin_token, pid, wid, 3)

    with app.app_context():
        assert stock_service.recalc_preview() == [], "数量与均价都必须能被流水完整重放"


# ---------- 8. 成本列不可事后改写 ----------

def test_cost_columns_are_immutable(db_conn, seed):
    log_id = db_conn.execute("SELECT id FROM stock_logs LIMIT 1").fetchone()[0]
    for column in ("unit_cost", "cost_amount"):
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(f"UPDATE stock_logs SET {column}=0 WHERE id=?", (log_id,))


# ---------- 9. 改坏 avg_cost 能被检出并修复 ----------

def test_recalc_detects_and_fixes_broken_avg_cost(client, admin_token, db_conn, seed):
    pid = _new_product(db_conn, 10)
    wid = _new_warehouse(db_conn, "修复仓")
    _stock_in(client, admin_token, pid, wid, 10)
    db_conn.execute("UPDATE stock_current SET avg_cost=1 WHERE product_id=? AND warehouse_id=?",
                    (pid, wid))

    preview = client.get("/api/backup/recalc", headers={"X-Auth-Token": admin_token})
    body = preview.get_json()["data"]
    assert body["count"] == 1
    assert (body["list"][0]["avg_recorded"], body["list"][0]["avg_computed"]) == (1.0, 10.0)

    applied = client.post("/api/backup/recalc", headers={"X-Auth-Token": admin_token})
    assert applied.status_code == 200 and applied.get_json()["data"]["changed"] == 1
    assert _avg(db_conn, pid, wid) == 10

    again = client.get("/api/backup/recalc", headers={"X-Auth-Token": admin_token})
    assert again.get_json()["data"]["count"] == 0
