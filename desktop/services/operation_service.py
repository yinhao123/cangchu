"""通用操作单（领用 / 退库 / 归还 / 借用 / 调拨）—— docs/13 第 4.4 / 4.6 节 · 第 5 章状态机

过账映射（唯一权威，见 docs/13 4.6）：

    op_type        | ref_type      | 账本动作
    ---------------|---------------|--------------------------------
    RECEIVE 领用    | RECEIVE       | OUT 源仓
    BORROW  借用    | BORROW        | OUT 源仓（expect_return_date 必填）
    RETURN_STOCK 退库 | RETURN_STOCK | IN 源仓
    RETURN_BORROW 归还 | RETURN_BORROW | IN 源仓
    TRANSFER 调拨   | TRANSFER_OUT + TRANSFER_IN | 源仓 OUT + 目标仓 IN（同一事务）

★ 单据层不写库存：全部走 stock_service.post_bill_lines（铁律 1）。
★ 冲销不是「删单」：对已过账流水逐条红字冲销（账本不可变，铁律 5）。
"""
from __future__ import annotations

from core.errors import BizError
from models import (database, operation_repo, partner_repo, product_repo,
                    purchase_repo, user_repo, warehouse_repo)
from services import bill_no_service, settings_service, stock_service, timeutil, validators

OUT_TYPES = ("RECEIVE", "BORROW")
IN_TYPES = ("RETURN_STOCK", "RETURN_BORROW")
EDITABLE = ("DRAFT", "REJECTED")
SUBMITTABLE = ("DRAFT", "REJECTED")

DEFAULT_PRICE = {  # 明细未填单价时的取值口径（与 /api/stock/in|out 保持一致）
    "OUT": "sale_price",
    "IN": "cost_price",
}


def _ensure_enabled() -> None:
    """单据总开关（docs/13 第 8.2 节）：关闭后所有写操作 409，历史单据仍可查。"""
    if not settings_service.typed("wms_bills_enabled", True):
        raise BizError(1018, "WMS 单据功能已关闭，请在「设置 → 业务与提醒」中开启")


def _approval_required() -> bool:
    """领用/退库/归还/借用/调拨是否需要审批（决策 3 的可配项；采购入库始终强制审批）。"""
    return bool(settings_service.typed("wms_operation_approve_required", True))


def _page(rows: list[dict], total: int, filters: dict) -> dict:
    page = int(filters.get("page") or 1)
    page_size = int(filters.get("page_size") or 20)
    return {"list": rows, "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}


def _require_warehouse(conn, warehouse_id, field: str = "warehouse_id") -> dict:
    row = warehouse_repo.get(conn, validators.require_id(warehouse_id, field))
    if row is None:
        raise BizError(1006, "仓库不存在", {field: warehouse_id})
    if not row["is_active"]:
        raise BizError(1008, "仓库已停用，请换仓库", {field: row["id"], "name": row["name"]})
    return row


def _clean_lines(conn, lines) -> list[dict]:
    if not isinstance(lines, list) or not lines:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细不能为空"]}})
    if len(lines) > 200:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["单张单据最多 200 行"]}})
    out: list[dict] = []
    for index, raw in enumerate(lines):
        if not isinstance(raw, dict):
            raise BizError(1015, "单据明细非法", {"errors": {f"lines[{index}]": ["每行必须是对象"]}})
        pid = validators.require_id(raw.get("product_id"), "product_id")
        product = product_repo.get(conn, pid)
        if product is None:
            raise BizError(1006, "商品不存在", {"product_id": pid})
        if not product["is_active"]:
            raise BizError(1004, "该商品已停用，不能开单", {"product_id": pid})
        qty = validators.require_positive_int(raw.get("qty"), "qty")
        price = raw.get("price")
        if price is not None:
            price = validators.require_money(price, "price")
        serial = validators.require_str(raw.get("serial_number"), "serial_number",
                                        max_len=64, required=False)
        flag = raw.get("is_join_asset")
        out.append({
            "product_id": product["id"], "product_name_snap": product["name"],
            "barcode_snap": product.get("barcode"), "unit_snap": product.get("unit") or "件",
            "qty": qty, "price": price,
            "amount": None if price is None else round(qty * float(price), 2),
            "serial_number": serial,
            "is_join_asset": 1 if (flag is True or str(flag).lower() in ("1", "true")) else 0,
            "remark": validators.require_str(raw.get("remark"), "remark", max_len=200, required=False),
        })
    return out


def _totals(lines: list[dict]) -> tuple[int, float]:
    return (sum(int(l["qty"]) for l in lines),
            round(sum(float(l["amount"] or 0) for l in lines), 2))


def _validate_shape(conn, op_type: str, warehouse_id, to_warehouse_id,
                    expect_return_date) -> tuple[dict, int | None, str | None]:
    """单头形态校验：调拨必须有目标仓且不得同仓；其余类型不得有目标仓；借用必须有归还日期。"""
    warehouse = _require_warehouse(conn, warehouse_id)
    to_id: int | None = None
    expect: str | None = None
    if op_type == "TRANSFER":
        if to_warehouse_id is None:
            raise BizError(1002, "参数校验失败",
                           {"errors": {"to_warehouse_id": ["调拨单必须指定调入仓库"]}})
        target = _require_warehouse(conn, to_warehouse_id, "to_warehouse_id")
        if target["id"] == warehouse["id"]:
            raise BizError(1002, "参数校验失败",
                           {"errors": {"to_warehouse_id": ["调入仓库不能与调出仓库相同"]}})
        to_id = target["id"]
    elif to_warehouse_id is not None:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"to_warehouse_id": ["只有调拨单才能指定调入仓库"]}})
    if op_type == "BORROW":
        raw = expect_return_date
        if not raw or not timeutil.is_valid_iso(str(raw)):
            raise BizError(1002, "参数校验失败",
                           {"errors": {"expect_return_date": ["借用单必须填写预计归还日期"]}})
        expect = str(raw)
    return warehouse, to_id, expect


# ==================== 查询 ====================

def list_orders(filters: dict) -> dict:
    rows, total = operation_repo.list_orders(database.get_connection(), filters or {})
    return _page(rows, total, filters or {})


def get_order(order_id: int) -> dict:
    conn = database.get_connection()
    order = operation_repo.get_order(conn, validators.require_id(order_id, "order_id"))
    if order is None:
        raise BizError(1006, "操作单不存在", {"order_id": order_id})
    order["details"] = operation_repo.list_details(conn, order["id"])
    return order


# ==================== 新增 / 编辑 ====================

def create_order(fields: dict, operator: dict) -> dict:
    _ensure_enabled()
    fields = fields or {}
    op_type = validators.require_choice(fields.get("op_type"), "op_type", operation_repo.OP_TYPES)
    name = validators.require_str(fields.get("name"), "name", max_len=60)
    remark = validators.require_str(fields.get("remark"), "remark", max_len=200, required=False)
    op_date = fields.get("op_date") or timeutil.now_iso()
    if not timeutil.is_valid_iso(str(op_date)):
        raise BizError(1002, "参数校验失败", {"errors": {"op_date": ["日期格式无效"]}})
    operator_user_id = validators.require_id(fields.get("operator_user_id") or operator["id"],
                                             "operator_user_id")
    conn = database.get_connection()
    if user_repo.find_by_id(conn, operator_user_id) is None:
        raise BizError(1006, "操作人不存在", {"operator_user_id": operator_user_id})
    warehouse, to_id, expect = _validate_shape(conn, op_type, fields.get("warehouse_id"),
                                               fields.get("to_warehouse_id"),
                                               fields.get("expect_return_date"))
    partner_id = None
    if fields.get("partner_id") is not None:
        partner = partner_repo.get(conn, validators.require_id(fields["partner_id"], "partner_id"))
        if partner is None:
            raise BizError(1006, "往来单位不存在", {"partner_id": fields["partner_id"]})
        partner_id = partner["id"]
    _check_sources(conn, fields)
    lines = _clean_lines(conn, fields.get("lines") or fields.get("details"))
    total_qty, total_amount = _totals(lines)
    with database.transaction(conn):
        code = bill_no_service.next_code(conn, "GO")
        order_id = operation_repo.insert_order(conn, {
            "code": code, "name": name, "op_type": op_type,
            "order_id": fields.get("order_id"),
            "in_warehousing_id": fields.get("in_warehousing_id"),
            "warehouse_id": warehouse["id"], "to_warehouse_id": to_id,
            "operator_user_id": operator_user_id, "partner_id": partner_id,
            "op_date": op_date, "expect_return_date": expect,
            "total_qty": total_qty, "total_amount": total_amount, "remark": remark,
        })
        operation_repo.replace_details(conn, order_id, lines)
    return get_order(order_id)


def _check_sources(conn, fields: dict) -> None:
    """可选来源单据校验：引用了就必须存在（不校验状态，领用可能引用历史入库单）。"""
    if fields.get("order_id") is not None:
        if purchase_repo.get_order(conn, validators.require_id(fields["order_id"], "order_id")) is None:
            raise BizError(1006, "采购订单不存在", {"order_id": fields["order_id"]})
    if fields.get("in_warehousing_id") is not None:
        iid = validators.require_id(fields["in_warehousing_id"], "in_warehousing_id")
        if purchase_repo.get_inbound(conn, iid) is None:
            raise BizError(1006, "采购入库单不存在", {"in_warehousing_id": iid})


def _load_for_change(conn, order_id: int, allowed: tuple[str, ...]) -> dict:
    _ensure_enabled()
    order = operation_repo.get_order(conn, validators.require_id(order_id, "order_id"))
    if order is None:
        raise BizError(1006, "操作单不存在", {"order_id": order_id})
    if order["posted_at"]:
        raise BizError(1014, "该单据已过账，不能修改（要更正请走冲销）", {"order_id": order_id})
    if order["status"] not in allowed:
        raise BizError(1014, f"单据当前状态为 {order['status']}，不允许该操作",
                       {"order_id": order_id, "status": order["status"]})
    return order


def update_order(order_id: int, fields: dict, operator: dict) -> dict:
    fields = fields or {}
    conn = database.get_connection()
    order = _load_for_change(conn, order_id, EDITABLE)
    clean: dict = {}
    if "name" in fields:
        clean["name"] = validators.require_str(fields["name"], "name", max_len=60)
    if "remark" in fields:
        clean["remark"] = validators.require_str(fields["remark"], "remark", max_len=200, required=False)
    if "op_date" in fields:
        if not timeutil.is_valid_iso(str(fields["op_date"])):
            raise BizError(1002, "参数校验失败", {"errors": {"op_date": ["日期格式无效"]}})
        clean["op_date"] = str(fields["op_date"])
    if fields.get("op_type") and str(fields["op_type"]).upper() != order["op_type"]:
        raise BizError(1014, "单据类型不能修改（要换类型请新建）",
                       {"order_id": order_id, "op_type": order["op_type"]})
    if "warehouse_id" in fields or "to_warehouse_id" in fields or "expect_return_date" in fields:
        warehouse, to_id, expect = _validate_shape(
            conn, order["op_type"],
            fields.get("warehouse_id", order["warehouse_id"]),
            fields.get("to_warehouse_id", order["to_warehouse_id"]),
            fields.get("expect_return_date", order["expect_return_date"]))
        clean.update({"warehouse_id": warehouse["id"], "to_warehouse_id": to_id,
                      "expect_return_date": expect})
    with database.transaction(conn):
        if fields.get("lines") or fields.get("details"):
            lines = _clean_lines(conn, fields.get("lines") or fields.get("details"))
            clean["total_qty"], clean["total_amount"] = _totals(lines)
            operation_repo.replace_details(conn, order["id"], lines)
        if clean:
            operation_repo.update_order(conn, order["id"], clean)
    return get_order(order["id"])


def submit_order(order_id: int, operator: dict, trace_id: str | None = None) -> dict:
    """提交。`wms_operation_approve_required=false` 时提交即过账（决策 3 的可配免审）。"""
    conn = database.get_connection()
    order = _load_for_change(conn, order_id, SUBMITTABLE)
    details = operation_repo.list_details(conn, order["id"])
    if not details:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细为空的单据不能提交"]}})
    now = timeutil.now_iso()
    if _approval_required():
        operation_repo.update_order(conn, order["id"], {"status": "SUBMITTED", "submit_at": now})
        return get_order(order["id"])
    # 免审：走与「审批通过」完全相同的过账出口，不允许出现第二套扣减逻辑
    _post_and_done(conn, order, details, operator, trace_id, {"submit_at": now})
    return get_order(order["id"])


# ==================== 审批与过账 ====================

def review_order(order_id: int, fields: dict, operator: dict, trace_id: str | None = None) -> dict:
    """审批（admin）：APPROVE 时同步过账；REJECT 需填原因。"""
    fields = fields or {}
    action = str(fields.get("action") or "").upper()
    if action not in ("APPROVE", "REJECT"):
        raise BizError(1002, "参数校验失败", {"errors": {"action": ["只能是 APPROVE 或 REJECT"]}})
    conn = database.get_connection()
    order = _load_for_change(conn, order_id, ("SUBMITTED",))
    now = timeutil.now_iso()
    if action == "REJECT":
        reason = validators.require_str(fields.get("reject_reason"), "reject_reason", max_len=200)
        operation_repo.update_order(conn, order["id"],
                                    {"status": "REJECTED", "approve_at": now,
                                     "approver_id": operator["id"], "reject_reason": reason})
        return get_order(order["id"])

    details = operation_repo.list_details(conn, order["id"])
    if not details:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细为空的单据不能过账"]}})
    _post_and_done(conn, order, details, operator, trace_id,
                   {"approve_at": now, "approver_id": operator["id"], "reject_reason": None})
    return get_order(order["id"])


def _post_and_done(conn, order: dict, details: list[dict], operator: dict,
                   trace_id: str | None, extra: dict) -> None:
    """过账 + 置 DONE，整体一个事务（审批通过与免审提交共用这一条路径）。"""
    now = timeutil.now_iso()
    payload = {"status": "DONE", "posted_at": now, "posted_by": operator["id"]}
    payload.update(extra)
    with database.transaction(conn):
        _post(conn, order, details, operator, trace_id)
        operation_repo.update_order(conn, order["id"], payload)


def _post(conn, order: dict, details: list[dict], operator: dict,
          trace_id: str | None) -> None:
    """按 4.6 映射表过账。调拨拆成两批（TRANSFER_OUT / TRANSFER_IN），同事务内原子完成。"""
    code = order["code"]
    remark = f"{order['op_type']} {code}"
    if order["op_type"] == "TRANSFER":
        out_lines = [{"client_msg_id": bill_no_service.bill_msg_id(code, d["id"], ":OUT"),
                      "product_id": d["product_id"], "warehouse_id": order["warehouse_id"],
                      "quantity": int(d["qty"]), "direction": "OUT",
                      "unit_price": _price_of(d, order["op_type"])} for d in details]
        in_lines = [{"client_msg_id": bill_no_service.bill_msg_id(code, d["id"], ":IN"),
                     "product_id": d["product_id"], "warehouse_id": order["to_warehouse_id"],
                     "quantity": int(d["qty"]), "direction": "IN",
                     "unit_price": _price_of(d, order["op_type"])} for d in details]
        stock_service.post_bill_lines(out_lines, operator, "TRANSFER_OUT", order["id"],
                                      remark=remark, trace_id=trace_id)
        stock_service.post_bill_lines(in_lines, operator, "TRANSFER_IN", order["id"],
                                      remark=remark, trace_id=trace_id)
        return
    direction = "OUT" if order["op_type"] in OUT_TYPES else "IN"
    lines = [{"client_msg_id": bill_no_service.bill_msg_id(code, d["id"]),
              "product_id": d["product_id"], "warehouse_id": order["warehouse_id"],
              "quantity": int(d["qty"]), "direction": direction,
              "unit_price": _price_of(d, order["op_type"]),
              "partner_id": order["partner_id"]} for d in details]
    stock_service.post_bill_lines(lines, operator, order["op_type"], order["id"],
                                  remark=remark, trace_id=trace_id)


def _price_of(detail: dict, op_type: str):
    """明细价优先；未填则由 stock_service 按成本价/售价兜底（direction=OUT 取售价）。"""
    if detail.get("price") is not None:
        return detail["price"]
    return None


def reverse_order(order_id: int, fields: dict, operator: dict,
                  trace_id: str | None = None) -> dict:
    """红字冲销：对已过账流水逐条反向，原单据置 REVERSED（账本不可变，docs/13 铁律 5）。"""
    _ensure_enabled()
    fields = fields or {}
    reason = validators.require_str(fields.get("reason"), "reason", max_len=200)
    conn = database.get_connection()
    order = operation_repo.get_order(conn, validators.require_id(order_id, "order_id"))
    if order is None:
        raise BizError(1006, "操作单不存在", {"order_id": order_id})
    if order["status"] != "DONE" or not order["posted_at"]:
        raise BizError(1014, "只有已过账的单据才能冲销",
                       {"order_id": order_id, "status": order["status"]})
    if order["reversed_at"]:
        raise BizError(1010, "该单据已被冲销", {"reversed_at": order["reversed_at"]})

    ref_types = ("TRANSFER_OUT", "TRANSFER_IN") if order["op_type"] == "TRANSFER" else (order["op_type"],)
    placeholders = ",".join("?" for _ in ref_types)
    logs = conn.execute(
        f"""SELECT id, type, reversed_by FROM stock_logs
             WHERE ref_type IN ({placeholders}) AND ref_id = ?
             ORDER BY id""", tuple(list(ref_types) + [order["id"]])).fetchall()
    if not logs:
        raise BizError(1014, "该单据没有可冲销的流水（可能已被冲销）", {"order_id": order_id})

    with database.transaction(conn):
        for log in logs:
            if log["reversed_by"] is not None:
                continue
            stock_service.reverse_log(
                int(log["id"]),
                {"client_msg_id": bill_no_service.bill_msg_id(order["code"], int(log["id"]), ":R"),
                 "reason": f"{reason}（单据 {order['code']} 冲销）"},
                operator, trace_id=trace_id)
        operation_repo.update_order(conn, order["id"],
                                    {"status": "REVERSED", "reversed_at": timeutil.now_iso(),
                                     "reversed_by": operator["id"]})
    return get_order(order["id"])


def cancel_order(order_id: int, operator: dict) -> dict:
    conn = database.get_connection()
    order = _load_for_change(conn, order_id, ("DRAFT", "REJECTED", "SUBMITTED"))
    operation_repo.update_order(conn, order["id"], {"status": "CANCELED", "is_active": 0})
    return get_order(order["id"])
