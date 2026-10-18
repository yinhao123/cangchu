"""库存数据访问 —— PRD 5.4 / 9.8 · 陷阱 T-06 / T-07 / T-08

★ 本层只写 SQL 不判业务。业务在 services/stock_service.py。
★ 出库扣减只能走 decrease_stock()：条件更新 + rowcount 判定。**禁止**任何「先查再改」（T-06）。
★ stock_current 只能用 ensure_row + increase/decrease 两步 upsert；**禁止 INSERT OR REPLACE**（T-07）。
"""
from __future__ import annotations

from services import timeutil


def get_qty(conn, product_id: int, warehouse_id: int) -> int | None:
    row = conn.execute("SELECT qty FROM stock_current WHERE product_id=? AND warehouse_id=?",
                       (product_id, warehouse_id)).fetchone()
    return int(row["qty"]) if row else None


def get_avg_cost(conn, product_id: int, warehouse_id: int) -> float | None:
    """移动加权均价（0007 起）。无库存行时返回 None（调用方按 0 处理）。"""
    row = conn.execute("SELECT avg_cost FROM stock_current WHERE product_id=? AND warehouse_id=?",
                       (product_id, warehouse_id)).fetchone()
    return float(row["avg_cost"] or 0) if row else None


def set_avg_cost(conn, product_id: int, warehouse_id: int, avg_cost: float) -> None:
    """只改派生表的均价列 —— 库存行必须已由 increase/decrease/set_stock 建好。"""
    conn.execute("UPDATE stock_current SET avg_cost=?, updated_at=? "
                 "WHERE product_id=? AND warehouse_id=?",
                 (round(float(avg_cost), 4), timeutil.now_iso(), product_id, warehouse_id))


def ensure_row(conn, product_id: int, warehouse_id: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO stock_current(product_id, warehouse_id, qty, updated_at)
           VALUES(?, ?, 0, ?)""", (product_id, warehouse_id, timeutil.now_iso()))


def increase_stock(conn, product_id: int, warehouse_id: int, qty: int) -> tuple[int, int]:
    """入库/正调整。返回 (before, after) —— 必须在同一事务内取值，禁止查询-计算推导。"""
    ensure_row(conn, product_id, warehouse_id)
    before = get_qty(conn, product_id, warehouse_id) or 0
    conn.execute(
        "UPDATE stock_current SET qty = qty + ?, updated_at = ? WHERE product_id=? AND warehouse_id=?",
        (qty, timeutil.now_iso(), product_id, warehouse_id))
    return before, before + qty


def decrease_stock(conn, product_id: int, warehouse_id: int, qty: int) -> tuple[int, int] | None:
    """出库扣减：唯一允许的实现（T-06）。

    rowcount==0 → 库存不足或行不存在 → 返回 None（调用方必须 ROLLBACK 并返回 1009）。
    """
    now = timeutil.now_iso()
    row = conn.execute("SELECT qty FROM stock_current WHERE product_id=? AND warehouse_id=? AND qty >= ?",
                       (product_id, warehouse_id, qty)).fetchone()
    if row is None:
        return None
    before = int(row["qty"])
    changed = conn.execute(
        """UPDATE stock_current SET qty = qty - ?, updated_at = ?
            WHERE product_id = ? AND warehouse_id = ? AND qty >= ?""",
        (qty, now, product_id, warehouse_id, qty)).rowcount
    if changed == 0:  # 并发窗口：读时够、写时已被别人扣走
        return None
    return before, before - qty


def set_stock(conn, product_id: int, warehouse_id: int, target_qty: int) -> tuple[int, int]:
    """盘点置为目标值（ADJUST）。返回 (before, after)。"""
    ensure_row(conn, product_id, warehouse_id)
    before = get_qty(conn, product_id, warehouse_id) or 0
    conn.execute("UPDATE stock_current SET qty = ?, updated_at = ? WHERE product_id=? AND warehouse_id=?",
                 (target_qty, timeutil.now_iso(), product_id, warehouse_id))
    return before, target_qty


def find_log_by_client_msg_id(conn, client_msg_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM stock_logs WHERE client_msg_id = ?", (client_msg_id,)).fetchone()
    return dict(row) if row else None


def insert_log(conn, fields: dict) -> int:
    """写入一条流水。ref_type/ref_id 为单据溯源（0002 起），二者必须同有同无（由调用方保证）。

    `unit_cost` / `cost_amount` 是成本口径（0007 起，docs/14 7.1），与结算价
    `unit_price` / `amount` 分开存；业务层不得直接填这两个值，必须走 cost_service。
    """
    cur = conn.execute(
        """INSERT INTO stock_logs(client_msg_id, product_id, warehouse_id, operator_id, partner_id,
                                 type, quantity, before_qty, after_qty, product_name_snapshot,
                                 unit_price, amount, unit_cost, cost_amount, source, device_name,
                                 remark, ref_type, ref_id, trace_id, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fields["client_msg_id"], fields["product_id"], fields["warehouse_id"], fields["operator_id"],
         fields.get("partner_id"), fields["type"], fields["quantity"], fields["before_qty"],
         fields["after_qty"], fields["product_name_snapshot"], fields.get("unit_price"),
         fields.get("amount"), fields.get("unit_cost"), fields.get("cost_amount"),
         fields.get("source", "WEB"), fields.get("device_name"),
         fields.get("remark"), fields.get("ref_type"), fields.get("ref_id"),
         fields.get("trace_id"), fields.get("created_at") or timeutil.now_iso()))
    return int(cur.lastrowid)


def get_log(conn, log_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM stock_logs WHERE id = ?", (log_id,)).fetchone()
    return dict(row) if row else None


def mark_reversed(conn, log_id: int, reversal_log_id: int) -> int:
    """条件更新：只有尚未被冲销的行会被改写（并发双点「撤销」时第二次 0 行 → 409）。

    ★ 账本触发器只锁「账目语义」列，reversed_by 允许写 —— 冲销指针正是靠这个放行实现的。
    """
    return conn.execute(
        "UPDATE stock_logs SET reversed_by = ? WHERE id = ? AND reversed_by IS NULL",
        (reversal_log_id, log_id)).rowcount


def already_reversed(conn, log_id: int) -> int | None:
    row = conn.execute("SELECT reversed_by FROM stock_logs WHERE id = ?", (log_id,)).fetchone()
    return row["reversed_by"] if row else None


def find_log_by_ref(conn, ref_type: str, ref_id: int, product_id: int) -> dict | None:
    """按单据溯源查最近一条流水（调拨目标仓要取源仓出库时的成本，docs/14 7.6）。"""
    row = conn.execute(
        """SELECT id, warehouse_id, unit_cost FROM stock_logs
            WHERE ref_type = ? AND ref_id = ? AND product_id = ?
            ORDER BY id DESC LIMIT 1""",
        (ref_type, int(ref_id), int(product_id))).fetchone()
    return dict(row) if row else None


def recompute_from_logs(conn) -> list[dict]:
    """按流水全量重算，返回 diff（recalc-stock 的核心；docs/02 第 5 章不变式 1 + docs/14 7.4）。

    ★ 必须重算 `avg_cost`：`stock_current` 是派生表，只重算数量会让「派生表可重算」失效，
      届时账实一致但成本错乱且无法自愈。
    ★ 按 `created_at, id` 顺序重放；冲销行用 `reversed_by` 指针识别（唯一权威，
      不依赖 `remark='REVERSAL_OF:#...'` 文本），与实时写入共用 cost_service.next_state。
    ★ 0007 之前的历史流水 `unit_cost IS NULL`：按 0 计入（成本缺失，不臆造成本）。
    """
    from services import cost_service  # 延迟导入：cost_service 反过来要用本模块的读写原语

    logs = [dict(r) for r in conn.execute(
        """SELECT id, product_id, warehouse_id, type, quantity, unit_cost, reversed_by
             FROM stock_logs ORDER BY created_at, id""").fetchall()]
    origin_by_reversal = {int(r["reversed_by"]): r for r in logs if r["reversed_by"] is not None}

    state: dict = {}   # (product_id, warehouse_id) -> (qty, avg_cost)
    for log in logs:
        key = (int(log["product_id"]), int(log["warehouse_id"]))
        before_qty, avg_before = state.get(key, (0, 0.0))
        origin = origin_by_reversal.get(int(log["id"]))
        if log["unit_cost"] is None:
            unit_cost = avg_before if origin is not None else 0.0
        else:
            unit_cost = float(log["unit_cost"])
        after_qty, new_avg = cost_service.next_state(
            log_type=log["type"], quantity=int(log["quantity"]), before_qty=before_qty,
            avg_before=avg_before, unit_cost=unit_cost, origin=origin)
        state[key] = (after_qty, new_avg)

    diffs: list[dict] = []
    for row in conn.execute("SELECT product_id, warehouse_id, qty, avg_cost FROM stock_current"):
        key = (int(row["product_id"]), int(row["warehouse_id"]))
        computed_qty, computed_avg = state.get(key, (0, 0.0))
        recorded_avg = round(float(row["avg_cost"] or 0), 4)
        computed_avg = round(float(computed_avg), 4)
        if int(row["qty"]) == computed_qty and recorded_avg == computed_avg:
            continue
        diffs.append({"product_id": key[0], "warehouse_id": key[1],
                      "recorded": int(row["qty"]), "computed": computed_qty,
                      "avg_recorded": recorded_avg, "avg_computed": computed_avg})
    return diffs


def apply_recompute(conn) -> int:
    """把 diff 行按流水结果改写（--apply 时调用；调用前必须已备份）。数量与均价一起写回。"""
    diffs = recompute_from_logs(conn)
    now = timeutil.now_iso()
    for d in diffs:
        conn.execute(
            "UPDATE stock_current SET qty=?, avg_cost=?, updated_at=? "
            "WHERE product_id=? AND warehouse_id=?",
            (d["computed"], d["avg_computed"], now, d["product_id"], d["warehouse_id"]))
    return len(diffs)
