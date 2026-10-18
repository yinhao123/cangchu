"""成本核算（移动加权平均）—— docs/14 第 7 章 · P1-5

口径分离（docs/14 7.1，两个口径**不得复用同一列**）：
  `unit_price` / `amount`        = 与往来单位的结算价（采购价 / 销售价），由单据带过来
  `unit_cost`  / `cost_amount`   = 成本口径，只由本模块计算，业务层不得填

规则（docs/14 7.3，唯一权威）：
  IN         : `unit_cost` = 入库单价（采购明细价 / 期初取参考成本），均价重新加权
  OUT        : `unit_cost` = 出库前均价，均价**不变**
  ADJUST 正  : `unit_cost` = 当前均价（为 0 时取参考成本），均价**不变**
  ADJUST 负  : `unit_cost` = 当前均价，均价**不变**
  调拨       : 目标仓 IN 的 `unit_cost` 取源仓 OUT 的成本，按该成本重新加权（在 stock_service 组装）
  冲销       : `unit_cost` = **原流水**的 `unit_cost`；冲销入库要把金额减回去

★ 关于「先读后写」：T-06 禁止的是「先 SELECT 判断够不够再 UPDATE」这类**可用性判定**。
  成本重算是纯数值换算，不涉及「是否允许」，调用方已在 `BEGIN IMMEDIATE` 事务内串行化，
  因此本模块可以读-算-写。这一区分必须保留，否则 review 时会误判为违规。
"""
from __future__ import annotations

# 均价保留 4 位：逐笔 round 到 2 位会让长链条反复截断，账面金额偏离手算结果
AVG_NDIGITS = 4
MONEY_NDIGITS = 2


def _avg(value) -> float:
    return round(float(value or 0), AVG_NDIGITS)


def _money(value) -> float:
    return round(float(value or 0), MONEY_NDIGITS)


def inbound_avg(before_qty: int, avg_before: float, qty: int, unit_cost: float) -> float:
    """入库后的移动加权均价：`(qty*avg + q*price) / (qty+q)`；总量为 0 时直接取本次单价。"""
    total = int(before_qty) + int(qty)
    if total <= 0:
        return _avg(unit_cost)
    return _avg((int(before_qty) * float(avg_before) + int(qty) * float(unit_cost)) / total)


def reversal_avg(before_qty: int, avg_before: float, after_qty: int, qty: int,
                 origin_type: str, origin_unit_cost: float) -> float:
    """冲销后的均价。

    - 冲销入库（本笔是 OUT）：把当初计入的金额减回去 → `(qty*avg - q*cost) / after`；
      `after == 0` 时归零（docs/14 7.6：除零必须显式处理，不能靠异常兜底）。
    - 冲销出库（本笔是 IN）：按原成本加回来 → `(qty*avg + q*cost) / after`。
    """
    if int(after_qty) <= 0:
        return 0.0
    if origin_type == "IN":
        return _avg(max(0.0, (int(before_qty) * float(avg_before)
                              - int(qty) * float(origin_unit_cost)) / int(after_qty)))
    return _avg((int(before_qty) * float(avg_before)
                 + int(qty) * float(origin_unit_cost)) / int(after_qty))


def next_state(*, log_type: str, quantity: int, before_qty: int, avg_before: float,
               unit_cost: float, origin: dict | None = None) -> tuple[int, float]:
    """纯函数：返回 (after_qty, 新均价)。

    实时写入与 `recalc-stock` 全量重放**共用这一个函数**，否则「重算无 diff」不可能成立。
    `origin` 非空表示这是冲销行（原流水的 type / unit_cost 以它为准）。
    """
    qty = int(quantity)
    if log_type == "OUT":
        after_qty = int(before_qty) - qty
    else:  # IN 恒正；ADJUST 本身带符号
        after_qty = int(before_qty) + qty
    if origin is not None:
        return after_qty, reversal_avg(before_qty, avg_before, after_qty, qty,
                                       origin["type"], float(unit_cost or 0))
    if log_type == "IN":
        return after_qty, inbound_avg(before_qty, avg_before, qty, unit_cost)
    return after_qty, _avg(avg_before)  # OUT / ADJUST：不动均价


def avg_cost(conn, product_id: int, warehouse_id: int) -> float:
    """当前均价（无库存行或历史行为 NULL 时按 0 处理，UI 会标记「成本缺失」）。"""
    from models import stock_repo
    return _avg(stock_repo.get_avg_cost(conn, product_id, warehouse_id))


def apply(conn, *, product_id: int, warehouse_id: int, log_type: str, quantity: int,
          before_qty: int, avg_before: float, unit_cost: float,
          origin: dict | None = None) -> dict:
    """算成本并落 `stock_current.avg_cost`，返回要写进流水与响应的成本字段。

    必须在 `BEGIN IMMEDIATE` 事务内调用（库存行已由 increase/decrease/set 建好）。
    """
    from models import stock_repo
    after_qty, new_avg = next_state(log_type=log_type, quantity=quantity,
                                    before_qty=before_qty, avg_before=avg_before,
                                    unit_cost=unit_cost, origin=origin)
    if _avg(new_avg) != _avg(avg_before):
        stock_repo.set_avg_cost(conn, product_id, warehouse_id, _avg(new_avg))
    return {
        "unit_cost": _avg(unit_cost),
        "cost_amount": _money(abs(int(quantity)) * float(unit_cost or 0)),
        "avg_cost": _avg(new_avg),
        # 冲销入库导致库存归零 → 调用方必须把这个事实写进 remark（docs/14 7.6）
        "avg_zeroed": bool(origin is not None and after_qty == 0),
    }
