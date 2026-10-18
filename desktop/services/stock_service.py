"""库存业务核心 —— PRD 5.4 / 5.5 / 9.5 / 9.9 · 规则 10-inventory-rules.md

★ 全项目风险最高的文件，实现方式没有自由度：
  1. 扣减只能走 decrease_stock（条件更新 + rowcount），禁止先查再改（T-06）
  2. 写流水 + 更新库存必须同事务（PRD 5.4 规则 3）
  3. client_msg_id 必填；命中 UNIQUE 返回首次结果 + duplicated（T-09）
  4. 批量同步用「外层单事务 + 每条 SAVEPOINT」（T-08）
  5. 永不物理删除流水；错单走红字冲销，账实不符走 ADJUST
"""
from __future__ import annotations

import threading

from core.errors import BizError
from models import database, product_repo, stock_repo
from services import cost_service, timeutil, validators

# 批量同步与重算必须串行（PRD 9.5）：单进程内一把锁即可，跨进程锁是过度设计
sync_lock = threading.Lock()
recalc_lock = threading.Lock()

MAX_BATCH = 200
DIRECTIONS = ("IN", "OUT", "ADJUST")

# 单据溯源类型（docs/13 第 4.6 节映射表）：写进 stock_logs.ref_type
BILL_REF_TYPES = ("PURCHASE_IN", "RECEIVE", "RETURN_STOCK", "RETURN_BORROW",
                  "BORROW", "TRANSFER_OUT", "TRANSFER_IN",
                  "SALES_OUT", "SALES_RETURN")   # 销货出库 / 销售退货（P1-4）
# 非单据的账本来源（docs/14 3.6）：期初建账不是单据，因此不能走 post_bill_lines，
# 但它同样必须可溯源 —— 所有按 ref_type 聚合的采购/销售报表都要把 OPENING 单列。
OPENING_REF_TYPE = "OPENING"
# 盘点单（P0-2）：流水 type 是 ADJUST（可正可负），因此**不能**走 post_bill_lines（只收 IN/OUT 正数），
# 但它同样是单据，必须带 ref_type + ref_id（docs/02 不变式检查 5）。
STOCKTAKE_REF_TYPE = "STOCKTAKE"
LOG_REF_TYPES = BILL_REF_TYPES + (OPENING_REF_TYPE, STOCKTAKE_REF_TYPE)

# 退回类入库（退库 / 归还 / 销售退货）没有采购价，按目标仓当前均价入账；
# 均价为 0（该仓从未入过账）时才回落商品参考成本 —— 不能用结算价当成本，否则均价被售价污染。
COST_AT_AVG_REF_TYPES = ("RETURN_STOCK", "RETURN_BORROW", "SALES_RETURN")


def _adjust_cost_unit(diff: int, avg_before: float, cost_price) -> float:
    """盘点成本单价（docs/14 7.3）：盘盈且均价为 0 时取参考成本，其余用当前均价。"""
    if diff > 0 and avg_before <= 0:
        return float(cost_price or 0)
    return float(avg_before or 0)


def _post_cost_unit(conn, product: dict, ref_type: str, direction: str,
                    unit_price, avg_before: float, ref_id: int) -> float:
    """单据过账的成本单价（唯一口径，禁止各 service 各写一份，docs/14 7.6）。

    - 出库：出库前均价
    - 调拨入库：源仓出库流水的成本（目标仓按该成本重新加权）
    - 退回类入库：目标仓当前均价（为 0 取参考成本）
    - 其余入库（采购）：明细单价
    """
    if direction == "OUT":
        return float(avg_before or 0)
    if ref_type == "TRANSFER_IN":
        source = stock_repo.find_log_by_ref(conn, "TRANSFER_OUT", ref_id, product["id"])
        if source is not None:
            if source["unit_cost"] is not None:
                return float(source["unit_cost"])
            return cost_service.avg_cost(conn, product["id"], source["warehouse_id"])
        return float(avg_before or 0)  # 理论上不会发生；宁可不动均价也不臆造成本
    if ref_type in COST_AT_AVG_REF_TYPES:
        return float(avg_before) if avg_before > 0 else float(product["cost_price"] or 0)
    return float(unit_price or 0)


def _resolve_product(conn, payload: dict) -> dict:
    """product_id 与 barcode 二选一；同时给出时必须一致（docs/03 5.1）。"""
    pid = payload.get("product_id")
    barcode = validators.normalize_barcode(payload.get("barcode"))
    product = None
    if pid is not None:
        product = product_repo.get(conn, validators.require_id(pid, "product_id"))
        if product is None:
            raise BizError(1006, "商品不存在", {"product_id": pid})
    elif barcode:
        product = product_repo.find_by_barcode(conn, barcode)
        if product is None:
            raise BizError(1007, "条码未登记", {"barcode": barcode})
    else:
        raise BizError(1002, "参数校验失败", {"errors": {"product_id": ["product_id 与 barcode 至少给一个"]}})
    if barcode and product["barcode"] and product["barcode"] != barcode:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"barcode": [f"条码与商品不匹配（商品实际条码 {product['barcode']}）"]}})
    if not product["is_active"]:
        raise BizError(1004, "该商品已停用，不能出入库", {"product_id": product["id"]})
    return product


def _resolve_warehouse(conn, warehouse_id) -> dict:
    from models import warehouse_repo
    wid = validators.require_id(warehouse_id, "warehouse_id")
    row = warehouse_repo.get(conn, wid)
    if row is None:
        raise BizError(1006, "仓库不存在", {"warehouse_id": wid})
    if not row["is_active"]:
        raise BizError(1008, "仓库已停用，请换仓库", {"warehouse_id": wid, "name": row["name"]})
    return row


def _resolve_partner(conn, partner_id, expect_type: str) -> dict | None:
    """出库选客户 / 入库选供应商：类型不匹配直接 422（PRD 5.3）。"""
    if partner_id is None:
        return None
    from models import partner_repo
    row = partner_repo.get(conn, validators.require_id(partner_id, "partner_id"))
    if row is None:
        raise BizError(1006, "往来单位不存在")
    if row["type"] != expect_type:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"partner_id": [f"该单位类型是 {row['type']}，此处需要 {expect_type}"]}})
    return row


def stock_in(payload: dict, operator: dict, source: str = "WEB", device_name: str | None = None,
             trace_id: str | None = None) -> dict:
    return _write({**payload, "type": "IN"}, operator, source, device_name, trace_id)


def stock_out(payload: dict, operator: dict, source: str = "WEB", device_name: str | None = None,
              trace_id: str | None = None) -> dict:
    return _write({**payload, "type": "OUT"}, operator, source, device_name, trace_id)


def opening_stock(payload: dict, operator: dict) -> dict:
    """期初建账（P0-1 批量导入专用）—— docs/14 第 3.6 节。

    ★ 必须走本入口，不得直接 INSERT stock_current：账本只认这一条写入通道。
    ★ 幂等键由 `barcode + warehouse_code` 确定性派生：同一份期初文件重复提交
      返回 `duplicated=true`，不会翻倍加库存（T-40）。随机 UUID 做不到这一点。
    ★ `ref_type='OPENING'`、`ref_id=0`：期初不指向任何单据，用 0 占位以满足
      「ref_type 与 ref_id 同有同无」的不变式（docs/02 检查 5）。
    ★ `unit_price` 取商品参考成本（期初没有真实采购价），并且**不挂 partner_id**，
      否则期初会污染供应商对账（docs/14 3.6 第 4 条）。
    """
    barcode = validators.normalize_barcode(payload.get("barcode"))
    if not barcode:
        raise BizError(1002, "参数校验失败", {"errors": {"barcode": ["期初建账必须给条码"]}})
    warehouse_code = str(payload.get("warehouse_code") or "").strip().upper()
    if not warehouse_code:
        raise BizError(1002, "参数校验失败", {"errors": {"warehouse_code": ["仓库编码不能为空"]}})
    msg_id = opening_msg_id(barcode, warehouse_code)
    return _write({**payload, "type": "IN", "client_msg_id": msg_id, "partner_id": None},
                  operator, "WEB", None, None, ref_type=OPENING_REF_TYPE)


def opening_msg_id(barcode: str, warehouse_code: str) -> str:
    """期初流水的幂等键：确定性派生，且必须是 UUID v4（validators 只放行 v1/v4）。

    用 uuid5 取稳定位，再按 v4 重打包 —— 与 `bill_no_service.bill_msg_id` 同一套路。
    """
    import uuid
    seed = uuid.uuid5(uuid.NAMESPACE_URL,
                      f"scanable/opening/{barcode}/{str(warehouse_code).upper()}").int
    return str(uuid.UUID(int=seed, version=4))


def _write(payload: dict, operator: dict, source: str, device_name: str | None,
           trace_id: str | None, ref_type: str | None = None) -> dict:
    """入库/出库的统一实现（type 由调用方固定，不接受客户端传入方向）。"""
    msg_id = validators.require_client_msg_id(payload)
    direction = str(payload["type"]).upper()
    if direction not in ("IN", "OUT"):
        raise BizError(1002, "参数校验失败", {"errors": {"type": ["只能是 IN 或 OUT"]}})
    quantity = validators.require_positive_int(payload.get("quantity"), "quantity")
    conn = database.get_connection()

    dup = stock_repo.find_log_by_client_msg_id(conn, msg_id)
    if dup is not None:  # 幂等命中：返回首次结果，不报错、不重复扣减（T-09）
        return _log_view(dup, duplicated=True)

    product = _resolve_product(conn, payload)
    warehouse = _resolve_warehouse(conn, payload.get("warehouse_id"))
    partner = _resolve_partner(conn, payload.get("partner_id"),
                               "SUPPLIER" if direction == "IN" else "CUSTOMER")
    unit_price = product["cost_price"] if direction == "IN" else product["sale_price"]
    created_at = _client_time(payload.get("client_created_at"))

    with database.transaction(conn):
        avg_before = cost_service.avg_cost(conn, product["id"], warehouse["id"])
        if direction == "IN":
            before, after = stock_repo.increase_stock(conn, product["id"], warehouse["id"], quantity)
        else:
            pair = stock_repo.decrease_stock(conn, product["id"], warehouse["id"], quantity)
            if pair is None:
                current = stock_repo.get_qty(conn, product["id"], warehouse["id"]) or 0
                raise BizError(1009, "库存不足", {"product_id": product["id"], "warehouse_id": warehouse["id"],
                                                 "current": current, "requested": quantity,
                                                 "lack": quantity - current})
            before, after = pair
        # 成本口径（docs/14 7.3）：入库按结算价重新加权，出库用出库前均价（均价不变）
        cost = cost_service.apply(
            conn, product_id=product["id"], warehouse_id=warehouse["id"], log_type=direction,
            quantity=quantity, before_qty=before, avg_before=avg_before,
            unit_cost=float(unit_price or 0) if direction == "IN" else avg_before)
        log_id = stock_repo.insert_log(conn, {
            "client_msg_id": msg_id, "product_id": product["id"], "warehouse_id": warehouse["id"],
            "operator_id": operator["id"], "partner_id": partner["id"] if partner else None,
            "type": direction, "quantity": quantity, "before_qty": before, "after_qty": after,
            "product_name_snapshot": product["name"], "unit_price": unit_price,
            "amount": round(quantity * float(unit_price or 0), 2),
            "unit_cost": cost["unit_cost"], "cost_amount": cost["cost_amount"], "source": source,
            "device_name": device_name, "remark": _remark(payload), "trace_id": trace_id,
            # 期初建账带 ref_type（非单据），普通扫码出入库保持 NULL
            "ref_type": ref_type, "ref_id": 0 if ref_type else None,
            "created_at": created_at,
        })
    return _log_view(stock_repo.get_log(conn, log_id), duplicated=False)


def _client_time(raw):
    """离线补传的业务发生时刻；非法或缺省 → 用服务器时间（PRD 5.5 / docs/03 5.1）。"""
    if raw and timeutil.is_valid_iso(str(raw)):
        return str(raw)
    return timeutil.now_iso()


def _remark(payload: dict) -> str | None:
    return validators.require_str(payload.get("remark"), "remark", max_len=200, required=False)


def _log_view(log: dict, duplicated: bool) -> dict:
    return {"log_id": log["id"], "type": log["type"], "product_id": log["product_id"],
            "product_name": log["product_name_snapshot"], "warehouse_id": log["warehouse_id"],
            "quantity": log["quantity"], "before_qty": log["before_qty"], "after_qty": log["after_qty"],
            "duplicated": duplicated, "created_at": log["created_at"]}


def stock_adjust(payload: dict, operator: dict, source: str = "WEB", device_name: str | None = None,
                 trace_id: str | None = None) -> dict:
    """盘点调整（仅 admin，remark 必填，PRD 4.3.6）：quantity = target - 当前，可正可负。"""
    msg_id = validators.require_client_msg_id(payload)
    target = validators.require_int(payload.get("target_qty"), "target_qty", minimum=0, maximum=10 ** 6)
    remark = payload.get("remark")
    if not (remark and str(remark).strip()):
        raise BizError(1002, "参数校验失败", {"errors": {"remark": ["盘点必须说明差异原因"]}})
    conn = database.get_connection()
    dup = stock_repo.find_log_by_client_msg_id(conn, msg_id)
    if dup is not None:
        return _log_view(dup, duplicated=True)
    product = _resolve_product(conn, payload)
    warehouse = _resolve_warehouse(conn, payload.get("warehouse_id"))
    created_at = _client_time(payload.get("client_created_at"))
    with database.transaction(conn):
        avg_before = cost_service.avg_cost(conn, product["id"], warehouse["id"])
        before, after = stock_repo.set_stock(conn, product["id"], warehouse["id"], target)
        diff = after - before
        if diff == 0:  # CHECK (quantity != 0) 会拒 0，必须在业务层先挡并给出人话
            raise BizError(1002, "参数校验失败", {"errors": {"target_qty": ["目标值与当前库存一致，无需调整"]}})
        cost = cost_service.apply(
            conn, product_id=product["id"], warehouse_id=warehouse["id"], log_type="ADJUST",
            quantity=diff, before_qty=before, avg_before=avg_before,
            unit_cost=_adjust_cost_unit(diff, avg_before, product["cost_price"]))
        log_id = stock_repo.insert_log(conn, {
            "client_msg_id": msg_id, "product_id": product["id"], "warehouse_id": warehouse["id"],
            "operator_id": operator["id"], "partner_id": None, "type": "ADJUST", "quantity": diff,
            "before_qty": before, "after_qty": after, "product_name_snapshot": product["name"],
            "unit_price": product["cost_price"], "amount": round(abs(diff) * float(product["cost_price"] or 0), 2),
            "unit_cost": cost["unit_cost"], "cost_amount": cost["cost_amount"],
            "source": source, "device_name": device_name, "remark": str(remark).strip()[:200],
            "trace_id": trace_id, "created_at": created_at,
        })
    view = _log_view(stock_repo.get_log(conn, log_id), duplicated=False)
    view["diff"] = diff
    return view


def reverse_log(log_id: int, payload: dict, operator: dict, trace_id: str | None = None) -> dict:
    """红字冲销：生成等额反向流水，永不删改原行（PRD 5.4 规则 7）。"""
    msg_id = validators.require_client_msg_id(payload)
    reason = payload.get("reason")
    if not (reason and str(reason).strip()):
        raise BizError(1002, "参数校验失败", {"errors": {"reason": ["冲销必须填写原因"]}})
    conn = database.get_connection()
    dup = stock_repo.find_log_by_client_msg_id(conn, msg_id)
    if dup is not None:
        return {"reversal_log_id": dup["id"], "reverses": dup["remark"], "duplicated": True}
    origin = stock_repo.get_log(conn, log_id)
    if origin is None:
        raise BizError(1006, "单据不存在", {"log_id": log_id})
    if origin["type"] == "ADJUST":
        raise BizError(1002, "盘点调整单不可冲销，请再盘点一次修正")
    if stock_repo.already_reversed(conn, log_id) is not None:
        raise BizError(1010, "该单据已被冲销", {"reversed_by": origin["reversed_by"]})
    opposite = "OUT" if origin["type"] == "IN" else "IN"
    product = product_repo.get(conn, origin["product_id"])
    if product is None:
        raise BizError(1006, "原单据的商品已不存在，无法冲销")
    qty = int(origin["quantity"])
    with database.transaction(conn):
        avg_before = cost_service.avg_cost(conn, origin["product_id"], origin["warehouse_id"])
        if opposite == "IN":
            before, after = stock_repo.increase_stock(conn, origin["product_id"], origin["warehouse_id"], qty)
        else:
            pair = stock_repo.decrease_stock(conn, origin["product_id"], origin["warehouse_id"], qty)
            if pair is None:
                current = stock_repo.get_qty(conn, origin["product_id"], origin["warehouse_id"]) or 0
                raise BizError(1009, "库存不足，无法冲销该入库单；请改用盘点调整",
                               {"current": current, "requested": qty, "lack": qty - current})
            before, after = pair
        # 冲销成本（docs/14 7.3）：取原流水成本；冲销入库要把当初计入的金额减回去，
        # 历史流水 unit_cost 为空时按当前均价兜底（等价于不改变均价）。
        origin_cost = origin["unit_cost"] if origin["unit_cost"] is not None else avg_before
        cost = cost_service.apply(
            conn, product_id=origin["product_id"], warehouse_id=origin["warehouse_id"],
            log_type=opposite, quantity=qty, before_qty=before, avg_before=avg_before,
            unit_cost=float(origin_cost), origin=origin)
        remark = f"REVERSAL_OF:#{log_id} {str(reason).strip()[:160]}"
        if cost["avg_zeroed"]:  # docs/14 7.6：除零归零必须留痕，不能只在代码里悄悄做
            remark += "（冲销后库存为 0，均价归零）"
        reversal_id = stock_repo.insert_log(conn, {
            "client_msg_id": msg_id, "product_id": origin["product_id"], "warehouse_id": origin["warehouse_id"],
            "operator_id": operator["id"], "partner_id": None, "type": opposite, "quantity": qty,
            "before_qty": before, "after_qty": after, "product_name_snapshot": product["name"],
            "unit_price": origin["unit_price"], "amount": origin["amount"],
            "unit_cost": cost["unit_cost"], "cost_amount": cost["cost_amount"], "source": "WEB",
            "remark": remark, "trace_id": trace_id,
            "created_at": timeutil.now_iso(),
        })
        # 条件更新做并发保护：0 行说明已被别人冲销过
        if stock_repo.mark_reversed(conn, log_id, reversal_id) == 0:
            raise BizError(1010, "该单据已被冲销")
    return {"reversal_log_id": reversal_id, "reverses": log_id, "type": opposite,
            "after_qty": after, "duplicated": False}


def post_bill_lines(lines: list[dict], operator: dict, ref_type: str, ref_id: int,
                    source: str = "WEB", device_name: str | None = None,
                    trace_id: str | None = None, remark: str | None = None,
                    partner_type: str | None = None) -> list[dict]:
    """单据过账的唯一出口 —— docs/13 第 2 章铁律 1~4 / 第 4.6 节映射表。

    ★ 单据层（采购入库、领用/退库/归还/借用/调拨）**禁止**自己写 stock_current，只能走本函数。
    ★ 整单原子：任一行失败（库存不足 / 商品停用 / 仓库停用）整个单据回滚，绝不留下半截过账。
    ★ 幂等：client_msg_id 命中既有流水 → 返回首次结果（`duplicated=True`），不重复扣减（T-09）。
    ★ 溯源：每行流水都写入 ref_type + ref_id，二者同有同无（docs/02 不变式检查 5）。
    ★ `partner_type`（可选）：默认按方向推导（入库要供应商、出库要客户），
      但「销售退货」是 IN 却必须挂客户，因此允许单据层显式指定，避免为此放宽类型校验。
    """
    if not isinstance(lines, list) or not lines:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细不能为空"]}})
    if ref_type not in BILL_REF_TYPES:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"ref_type": [f"只能是 {'/'.join(BILL_REF_TYPES)}"]}})
    bill_id = validators.require_id(ref_id, "ref_id")
    conn = database.get_connection()
    views: list[dict] = []
    with database.transaction(conn):  # 调用方已在事务中时自动降级为 SAVEPOINT
        for index, line in enumerate(lines):
            views.append(_post_one(conn, line, operator, ref_type, bill_id, source,
                                   device_name, trace_id, remark, index, partner_type))
    return views


def _post_one(conn, line: dict, operator: dict, ref_type: str, ref_id: int, source: str,
              device_name: str | None, trace_id: str | None, fallback_remark: str | None,
              index: int, expect_partner_type: str | None = None) -> dict:
    if not isinstance(line, dict):
        raise BizError(1015, "单据明细非法", {"errors": {f"lines[{index}]": ["每行必须是对象"]}})
    msg_id = validators.require_client_msg_id(line)
    dup = stock_repo.find_log_by_client_msg_id(conn, msg_id)
    if dup is not None:  # 重复过账：返回首次结果，不新增流水
        return _log_view(dup, duplicated=True)
    direction = str(line.get("direction") or "").upper()
    if direction not in ("IN", "OUT"):
        raise BizError(1015, "单据明细非法",
                       {"errors": {"direction": [f"第 {index + 1} 行的 direction 只能是 IN 或 OUT"]}})
    quantity = validators.require_positive_int(line.get("quantity"), "quantity")
    product = _resolve_product(conn, line)
    warehouse = _resolve_warehouse(conn, line.get("warehouse_id"))
    partner = _resolve_partner(conn, line.get("partner_id"),
                               expect_partner_type or ("SUPPLIER" if direction == "IN" else "CUSTOMER"))
    raw_price = line.get("unit_price")
    if raw_price is None:  # 未指定则沿用既有语义：入库取成本价、出库取售价
        unit_price = product["cost_price"] if direction == "IN" else product["sale_price"]
    else:
        unit_price = validators.require_money(raw_price, "unit_price")

    avg_before = cost_service.avg_cost(conn, product["id"], warehouse["id"])
    if direction == "IN":
        before, after = stock_repo.increase_stock(conn, product["id"], warehouse["id"], quantity)
    else:
        pair = stock_repo.decrease_stock(conn, product["id"], warehouse["id"], quantity)
        if pair is None:
            current = stock_repo.get_qty(conn, product["id"], warehouse["id"]) or 0
            raise BizError(1009, "库存不足，单据无法过账",
                           {"product_id": product["id"], "product_name": product["name"],
                            "warehouse_id": warehouse["id"], "current": current,
                            "requested": quantity, "lack": quantity - current})
        before, after = pair
    cost = cost_service.apply(
        conn, product_id=product["id"], warehouse_id=warehouse["id"], log_type=direction,
        quantity=quantity, before_qty=before, avg_before=avg_before,
        unit_cost=_post_cost_unit(conn, product, ref_type, direction, unit_price, avg_before, ref_id))
    log_id = stock_repo.insert_log(conn, {
        "client_msg_id": msg_id, "product_id": product["id"], "warehouse_id": warehouse["id"],
        "operator_id": operator["id"], "partner_id": partner["id"] if partner else None,
        "type": direction, "quantity": quantity, "before_qty": before, "after_qty": after,
        "product_name_snapshot": product["name"], "unit_price": unit_price,
        "amount": round(quantity * float(unit_price or 0), 2),
        "unit_cost": cost["unit_cost"], "cost_amount": cost["cost_amount"], "source": source,
        "device_name": device_name,
        "remark": _remark(line) or fallback_remark,
        "ref_type": ref_type, "ref_id": ref_id, "trace_id": trace_id,
        "created_at": timeutil.now_iso(),
    })
    view = _log_view(stock_repo.get_log(conn, log_id), duplicated=False)
    view["client_msg_id"] = msg_id
    return view


def post_adjust_lines(lines: list[dict], operator: dict, ref_id: int, source: str = "WEB",
                      trace_id: str | None = None) -> list[dict]:
    """盘点单过账的唯一出口 —— docs/14 第 4.2 / 4.5 节。

    ★ 为什么不复用 `post_bill_lines`：它只收 `direction ∈ IN/OUT` 且 `quantity > 0`，
      而盘点的 type 是 `ADJUST`、数量可正可负（`stock_logs` 的 CHECK 也这么要求）。
      强行复用会导致「盘亏写不出来」，所以单开一个入口，但**规则一条不少**：
      整单一个事务（任一行失败全部回滚）、幂等键由单据派生、溯源写 ref_type/ref_id。
    ★ 数量口径 = **实时当前值**（docs/14 4.3 B 口径）：`quantity = target_qty - 当前 qty`，
      由 `stock_repo.set_stock()` 在同一事务内取 before/after，禁止先查再算（T-06 同源风险）。
    ★ 无差异行不落账（CHECK quantity != 0 也不允许 0），返回 `skipped=True` 让上层统计。
    """
    if not isinstance(lines, list) or not lines:
        raise BizError(1015, "盘点明细非法", {"errors": {"lines": ["明细不能为空"]}})
    bill_id = validators.require_id(ref_id, "ref_id")
    conn = database.get_connection()
    views: list[dict] = []
    with database.transaction(conn):  # 调用方已在事务中时自动降级为 SAVEPOINT（T-23）
        for index, line in enumerate(lines):
            views.append(_post_adjust_one(conn, line, operator, bill_id, source, trace_id, index))
    return views


def _post_adjust_one(conn, line: dict, operator: dict, ref_id: int, source: str,
                     trace_id: str | None, index: int) -> dict:
    if not isinstance(line, dict):
        raise BizError(1015, "盘点明细非法", {"errors": {f"lines[{index}]": ["每行必须是对象"]}})
    msg_id = validators.require_client_msg_id(line)
    dup = stock_repo.find_log_by_client_msg_id(conn, msg_id)
    if dup is not None:  # 重复过账：返回首次结果，不新增流水（T-09）
        view = _log_view(dup, duplicated=True)
        view["client_msg_id"] = msg_id
        return view
    target = validators.require_int(line.get("target_qty"), "target_qty",
                                    minimum=0, maximum=10 ** 6)
    product = _resolve_product(conn, line)
    warehouse = _resolve_warehouse(conn, line.get("warehouse_id"))
    avg_before = cost_service.avg_cost(conn, product["id"], warehouse["id"])
    before, after = stock_repo.set_stock(conn, product["id"], warehouse["id"], target)
    diff = after - before
    if diff == 0:  # 账实相符：不落账，避免账本被无意义行灌满
        return {"client_msg_id": msg_id, "log_id": None, "skipped": True, "diff": 0,
                "before_qty": before, "after_qty": after, "duplicated": False}
    cost = cost_service.apply(
        conn, product_id=product["id"], warehouse_id=warehouse["id"], log_type="ADJUST",
        quantity=diff, before_qty=before, avg_before=avg_before,
        unit_cost=_adjust_cost_unit(diff, avg_before, product["cost_price"]))
    remark = _adjust_remark(line, before, after)
    log_id = stock_repo.insert_log(conn, {
        "client_msg_id": msg_id, "product_id": product["id"], "warehouse_id": warehouse["id"],
        "operator_id": operator["id"], "partner_id": None, "type": "ADJUST", "quantity": diff,
        "before_qty": before, "after_qty": after, "product_name_snapshot": product["name"],
        "unit_price": product["cost_price"],
        "amount": round(abs(diff) * float(product["cost_price"] or 0), 2),
        "unit_cost": cost["unit_cost"], "cost_amount": cost["cost_amount"],
        "source": source, "remark": remark, "trace_id": trace_id,
        "ref_type": STOCKTAKE_REF_TYPE, "ref_id": ref_id,
        "created_at": timeutil.now_iso(),
    })
    view = _log_view(stock_repo.get_log(conn, log_id), duplicated=False)
    view["client_msg_id"] = msg_id
    view["skipped"] = False
    view["diff"] = diff
    return view


def _adjust_remark(line: dict, before: int, after: int) -> str:
    """差异说明必须让人看懂「这个数是怎么来的」：实盘 / 盘面（快照）/ 期间变动（docs/14 4.3 第 1 条）。"""
    book = line.get("book_qty")
    book = int(book) if book is not None else before
    extra = validators.require_str(line.get("remark"), "remark", max_len=160, required=False)
    text = f"盘点实盘 {after}（盘面 {book}，期间变动 {before - book:+d}）"
    return f"{text} {extra}".strip()[:200]


def sync_batch(logs: list[dict], operator: dict, device_name: str | None = None,
               trace_id: str | None = None) -> dict:
    """离线批量同步（docs/03 5.4）：单事务 + 每条 SAVEPOINT；全局串行；逐条按序返回。"""
    if not isinstance(logs, list) or not logs:
        raise BizError(1002, "参数校验失败", {"errors": {"logs": ["logs 必须是非空数组"]}})
    if len(logs) > MAX_BATCH:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"logs": [f"单次最多 {MAX_BATCH} 条，请分页提交"]}})
    if not sync_lock.acquire(timeout=10):
        raise BizError(1012, "同步通道繁忙，请稍后重试", {"retry_after": 5}, http_status=503)
    try:
        conn = database.get_connection()
        results = database.batch_with_savepoints(
            conn, logs, lambda item: _sync_one(conn, item, operator, device_name, trace_id))
        ok_count = sum(1 for r in results if r.get("ok"))
        dup_count = sum(1 for r in results if r.get("ok") and r.get("duplicated"))
        failed = [r for r in results if not r.get("ok")]
        return {"received": len(logs), "ok_count": ok_count, "duplicated_count": dup_count,
                "failed_count": len(failed), "results": results}
    finally:
        sync_lock.release()


def recalc_preview() -> list[dict]:
    """库存一致性自检：返回「账面 vs 流水聚合」不一致的行（docs/02 第 5 章检查 1）。"""
    with recalc_lock:
        return stock_repo.recompute_from_logs(database.get_connection())


def recalc_apply() -> dict:
    """按流水全量重写派生表 stock_current（PRD 4.5 / 9.2）：执行期持互斥锁，写前自动备份。

    这是**唯一**允许改 stock_current 的路径 —— 手工 UPDATE 正是「库存对不上」的源头。
    不一致为 0 时不备份也不写库：无意义的覆盖只会让备份目录越长越乱。
    """
    from services import backup_service
    conn = database.get_connection()
    with recalc_lock:
        diffs = stock_repo.recompute_from_logs(conn)
        if not diffs:
            return {"changed": 0, "backup": None}
        info = backup_service.create_backup(trigger="pre_migration")
        with database.transaction(conn):
            changed = stock_repo.apply_recompute(conn)
    return {"changed": changed, "backup": info["filename"]}


def _sync_one(conn, item: dict, operator: dict, device_name: str | None, trace_id: str | None) -> dict:
    """单条同步：成功结果；业务错抛 BizError 由 SAVEPOINT 回滚该条并记录 code。"""
    if not isinstance(item, dict):
        raise BizError(1002, "参数校验失败", {"errors": {"logs": ["每条必须是对象"]}})
    direction = str(item.get("type") or "").upper()
    try:
        if direction == "IN":
            view = stock_in(item, operator, source="APP", device_name=device_name, trace_id=trace_id)
        elif direction == "OUT":
            view = stock_out(item, operator, source="APP", device_name=device_name, trace_id=trace_id)
        elif direction == "ADJUST":
            view = stock_adjust(item, operator, source="APP", device_name=device_name, trace_id=trace_id)
        else:
            raise BizError(1002, "参数校验失败", {"errors": {"type": ["只能是 IN/OUT/ADJUST"]}})
    except BizError as e:
        return {"client_msg_id": item.get("client_msg_id"), "ok": False, "code": e.code, "msg": e.msg}
    except Exception as e:  # 非业务错：交给上层事务回滚整批（数据异常不该被静默吞掉）
        raise
    return {"client_msg_id": view.get("client_msg_id") or item.get("client_msg_id"),
            "ok": True, "duplicated": view["duplicated"], "log_id": view["log_id"],
            "after_qty": view["after_qty"]}
