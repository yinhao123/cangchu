"""采购订单 / 采购入库单 业务规则与事务边界 —— docs/13 第 4.2 / 4.3 / 4.6 节 · 第 5 章状态机

★ 单据层**不写库存**：过账统一调 services/stock_service.post_bill_lines（铁律 1）。
★ 审批用同步状态机，不引流程引擎：SUBMITTED →(admin)→ APPROVED（入库单此时自动过账）/ REJECTED。
★ 采购入库**强制审批通过才过账**（docs/13 第 12 章决策 3）；领用/退库等 P4 再做可配免审。
★ 单据可改、流水不可变：作废走 cancel（status=CANCELED + is_active=0），已过账单据只走冲销。
"""
from __future__ import annotations

from core.errors import BizError
from models import database, partner_repo, product_repo, purchase_repo, warehouse_repo
from services import bill_no_service, settings_service, stock_service, timeutil, validators

# 可编辑/可提交的状态；其余一律 1014
EDITABLE = ("DRAFT", "REJECTED")
SUBMITTABLE = ("DRAFT", "REJECTED")


def _ensure_enabled() -> None:
    """单据总开关（docs/13 第 8.2 节回滚方案）：关闭后所有写操作 409，既有的出入库与库存不受影响。

    ★ 只拦写操作：历史单据仍然可查（否则关掉开关后连账都看不到了）。
    """
    if not settings_service.typed("wms_bills_enabled", True):
        raise BizError(1018, "WMS 单据功能已关闭，请在「设置 → 业务与提醒」中开启")


def _page(rows: list[dict], total: int, filters: dict) -> dict:
    page = int(filters.get("page") or 1)
    page_size = int(filters.get("page_size") or 20)
    return {"list": rows, "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}


def _bill_msg_id(bill_code: str, detail_id: int) -> str:
    """过账幂等键（实现见 services/bill_no_service.py:bill_msg_id，与操作单共用一套规则）。"""
    return bill_no_service.bill_msg_id(bill_code, detail_id)


def _require_product(conn, product_id) -> dict:
    pid = validators.require_id(product_id, "product_id")
    product = product_repo.get(conn, pid)
    if product is None:
        raise BizError(1006, "商品不存在", {"product_id": pid})
    if not product["is_active"]:
        raise BizError(1004, "该商品已停用，不能开单", {"product_id": pid})
    return product


def _clean_lines(conn, lines, kind: str) -> list[dict]:
    if not isinstance(lines, list) or not lines:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细不能为空"]}})
    if len(lines) > 200:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["单张单据最多 200 行"]}})
    out: list[dict] = []
    for index, raw in enumerate(lines):
        if not isinstance(raw, dict):
            raise BizError(1015, "单据明细非法", {"errors": {f"lines[{index}]": ["每行必须是对象"]}})
        product = _require_product(conn, raw.get("product_id"))
        qty = validators.require_positive_int(raw.get("qty"), "qty")
        price = validators.require_money(raw.get("price"), "price")
        line = {
            "product_id": product["id"],
            "product_name_snap": product["name"],
            "barcode_snap": product.get("barcode"),
            "unit_snap": product.get("unit") or "件",
            "price": price,
            "qty": qty,
            "amount": round(qty * price, 2),
            "remark": validators.require_str(raw.get("remark"), "remark", max_len=200, required=False),
        }
        if kind == "inbound":
            odid = raw.get("order_detail_id")
            line["order_detail_id"] = validators.require_id(odid, "order_detail_id") if odid else None
        out.append(line)
    return out


def _totals(lines: list[dict]) -> tuple[int, float]:
    return sum(int(l["qty"]) for l in lines), round(sum(float(l["amount"]) for l in lines), 2)


# ==================== 采购订单 ====================

def list_orders(filters: dict) -> dict:
    rows, total = purchase_repo.list_orders(database.get_connection(), filters or {})
    return _page(rows, total, filters or {})


def get_order(order_id: int) -> dict:
    conn = database.get_connection()
    order = purchase_repo.get_order(conn, validators.require_id(order_id, "order_id"))
    if order is None:
        raise BizError(1006, "采购订单不存在", {"order_id": order_id})
    order["details"] = purchase_repo.list_order_details(conn, order["id"])
    return order


def create_order(fields: dict, operator: dict) -> dict:
    _ensure_enabled()
    fields = fields or {}
    name = validators.require_str(fields.get("name"), "name", max_len=60)
    remark = validators.require_str(fields.get("remark"), "remark", max_len=200, required=False)
    applicant_date = fields.get("applicant_date") or timeutil.now_iso()
    if not timeutil.is_valid_iso(str(applicant_date)):
        raise BizError(1002, "参数校验失败", {"errors": {"applicant_date": ["日期格式无效"]}})
    conn = database.get_connection()
    partner = None
    if fields.get("partner_id") is not None:
        partner = partner_repo.get(conn, validators.require_id(fields["partner_id"], "partner_id"))
        if partner is None:
            raise BizError(1006, "往来单位不存在", {"partner_id": fields["partner_id"]})
        if partner["type"] != "SUPPLIER":
            raise BizError(1002, "参数校验失败",
                           {"errors": {"partner_id": ["采购订单的往来单位必须是供应商"]}})
    lines = _clean_lines(conn, fields.get("lines") or fields.get("details"), "order")
    total_qty, total_amount = _totals(lines)
    with database.transaction(conn):
        code = bill_no_service.next_code(conn, "PO")
        order_id = purchase_repo.insert_order(conn, {
            "code": code, "name": name, "applicant_user_id": operator["id"],
            "partner_id": partner["id"] if partner else None,
            "partner_name_snap": partner["name"] if partner else None,
            "applicant_date": applicant_date, "total_qty": total_qty,
            "total_amount": total_amount, "remark": remark,
        })
        purchase_repo.replace_order_details(conn, order_id, lines)
    return get_order(order_id)


def _load_order_for_change(conn, order_id: int, allowed: tuple[str, ...]) -> dict:
    _ensure_enabled()
    order = purchase_repo.get_order(conn, validators.require_id(order_id, "order_id"))
    if order is None:
        raise BizError(1006, "采购订单不存在", {"order_id": order_id})
    if order["status"] not in allowed:
        raise BizError(1014, f"单据当前状态为 {order['status']}，不允许该操作",
                       {"order_id": order_id, "status": order["status"]})
    return order


def update_order(order_id: int, fields: dict, operator: dict) -> dict:
    fields = fields or {}
    conn = database.get_connection()
    order = _load_order_for_change(conn, order_id, EDITABLE)
    clean: dict = {}
    if "name" in fields:
        clean["name"] = validators.require_str(fields["name"], "name", max_len=60)
    if "remark" in fields:
        clean["remark"] = validators.require_str(fields["remark"], "remark", max_len=200, required=False)
    if "applicant_date" in fields:
        day = str(fields["applicant_date"])
        if not timeutil.is_valid_iso(day):
            raise BizError(1002, "参数校验失败", {"errors": {"applicant_date": ["日期格式无效"]}})
        clean["applicant_date"] = day
    partner = None
    if "partner_id" in fields:
        if fields["partner_id"] is None:
            clean.update({"partner_id": None, "partner_name_snap": None})
        else:
            partner = partner_repo.get(conn, validators.require_id(fields["partner_id"], "partner_id"))
            if partner is None:
                raise BizError(1006, "往来单位不存在", {"partner_id": fields["partner_id"]})
            if partner["type"] != "SUPPLIER":
                raise BizError(1002, "参数校验失败",
                               {"errors": {"partner_id": ["采购订单的往来单位必须是供应商"]}})
            clean.update({"partner_id": partner["id"], "partner_name_snap": partner["name"]})
    with database.transaction(conn):
        if fields.get("lines") or fields.get("details"):
            lines = _clean_lines(conn, fields.get("lines") or fields.get("details"), "order")
            clean["total_qty"], clean["total_amount"] = _totals(lines)
            purchase_repo.replace_order_details(conn, order["id"], lines)
        if clean:
            purchase_repo.update_order(conn, order["id"], clean)
    return get_order(order["id"])


def submit_order(order_id: int, operator: dict) -> dict:
    conn = database.get_connection()
    order = _load_order_for_change(conn, order_id, SUBMITTABLE)
    if not purchase_repo.list_order_details(conn, order["id"]):
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细为空的单据不能提交"]}})
    now = timeutil.now_iso()
    purchase_repo.update_order(conn, order["id"], {"status": "SUBMITTED", "submit_at": now})
    return get_order(order["id"])


def review_order(order_id: int, fields: dict, operator: dict) -> dict:
    """审批（admin）：approve → APPROVED；reject → REJECTED（驳回原因必填）。"""
    fields = fields or {}
    action = str(fields.get("action") or "").upper()
    if action not in ("APPROVE", "REJECT"):
        raise BizError(1002, "参数校验失败", {"errors": {"action": ["只能是 APPROVE 或 REJECT"]}})
    conn = database.get_connection()
    order = _load_order_for_change(conn, order_id, ("SUBMITTED",))
    now = timeutil.now_iso()
    if action == "APPROVE":
        purchase_repo.update_order(conn, order["id"],
                                   {"status": "APPROVED", "approve_at": now,
                                    "approver_id": operator["id"], "reject_reason": None})
    else:
        reason = validators.require_str(fields.get("reject_reason"), "reject_reason", max_len=200)
        purchase_repo.update_order(conn, order["id"],
                                   {"status": "REJECTED", "approve_at": now,
                                    "approver_id": operator["id"], "reject_reason": reason})
    return get_order(order["id"])


def cancel_order(order_id: int, operator: dict) -> dict:
    conn = database.get_connection()
    order = _load_order_for_change(conn, order_id, ("DRAFT", "REJECTED", "SUBMITTED", "APPROVED"))
    purchase_repo.update_order(conn, order["id"], {"status": "CANCELED", "is_active": 0})
    return get_order(order["id"])


# ==================== 采购入库单 ====================

def list_inbounds(filters: dict) -> dict:
    rows, total = purchase_repo.list_inbounds(database.get_connection(), filters or {})
    return _page(rows, total, filters or {})


def get_inbound(inbound_id: int) -> dict:
    conn = database.get_connection()
    inbound = purchase_repo.get_inbound(conn, validators.require_id(inbound_id, "inbound_id"))
    if inbound is None:
        raise BizError(1006, "采购入库单不存在", {"inbound_id": inbound_id})
    inbound["details"] = purchase_repo.list_inbound_details(conn, inbound["id"])
    return inbound


def create_inbound(fields: dict, operator: dict) -> dict:
    _ensure_enabled()
    fields = fields or {}
    name = validators.require_str(fields.get("name"), "name", max_len=60)
    remark = validators.require_str(fields.get("remark"), "remark", max_len=200, required=False)
    in_date = fields.get("in_date") or timeutil.now_iso()
    if not timeutil.is_valid_iso(str(in_date)):
        raise BizError(1002, "参数校验失败", {"errors": {"in_date": ["日期格式无效"]}})
    conn = database.get_connection()
    warehouse = warehouse_repo.get(conn, validators.require_id(fields.get("warehouse_id"), "warehouse_id"))
    if warehouse is None:
        raise BizError(1006, "仓库不存在", {"warehouse_id": fields.get("warehouse_id")})
    if not warehouse["is_active"]:
        raise BizError(1008, "仓库已停用，请换仓库", {"warehouse_id": warehouse["id"]})

    order = None
    partner_id = fields.get("partner_id")
    if fields.get("order_id") is not None:
        order = purchase_repo.get_order(conn, validators.require_id(fields["order_id"], "order_id"))
        if order is None:
            raise BizError(1006, "采购订单不存在", {"order_id": fields["order_id"]})
        if order["status"] != "APPROVED":
            raise BizError(1014, "只有已审批通过的采购订单才能入库",
                           {"order_id": order["id"], "status": order["status"]})
        partner_id = partner_id or order["partner_id"]
    if partner_id is not None:
        partner = partner_repo.get(conn, validators.require_id(partner_id, "partner_id"))
        if partner is None:
            raise BizError(1006, "往来单位不存在", {"partner_id": partner_id})

    lines = _clean_lines(conn, fields.get("lines") or fields.get("details"), "inbound")
    _check_receivable(conn, order, lines)
    total_qty, total_amount = _totals(lines)
    with database.transaction(conn):
        code = bill_no_service.next_code(conn, "PI")
        inbound_id = purchase_repo.insert_inbound(conn, {
            "code": code, "name": name, "order_id": order["id"] if order else None,
            "order_code_snap": order["code"] if order else None, "warehouse_id": warehouse["id"],
            "partner_id": partner_id, "in_date": in_date, "total_qty": total_qty,
            "total_amount": total_amount, "remark": remark,
        })
        purchase_repo.replace_inbound_details(conn, inbound_id, lines)
    return get_inbound(inbound_id)


def _check_receivable(conn, order: dict | None, lines: list[dict]) -> None:
    """引用了订单明细时做「可收量」友好校验（真正的权威校验在过账时的 bump_received）。"""
    if order is None:
        return
    remaining = {d["id"]: int(d["qty"]) - int(d["received_qty"])
                 for d in purchase_repo.list_order_details(conn, order["id"])}
    for index, line in enumerate(lines):
        odid = line.get("order_detail_id")
        if odid is None:
            continue
        if odid not in remaining:
            raise BizError(1006, "采购订单明细不存在", {"order_detail_id": odid})
        if int(line["qty"]) > remaining[odid]:
            raise BizError(1016, "入库数量超出订单可收数量",
                           {"order_detail_id": odid, "requested": int(line["qty"]),
                            "receivable": remaining[odid]})


def _load_inbound_for_change(conn, inbound_id: int, allowed: tuple[str, ...]) -> dict:
    _ensure_enabled()
    inbound = purchase_repo.get_inbound(conn, validators.require_id(inbound_id, "inbound_id"))
    if inbound is None:
        raise BizError(1006, "采购入库单不存在", {"inbound_id": inbound_id})
    if inbound["posted_at"]:
        raise BizError(1014, "该入库单已过账，不能修改（要更正请走冲销）",
                       {"inbound_id": inbound_id})
    if inbound["status"] not in allowed:
        raise BizError(1014, f"单据当前状态为 {inbound['status']}，不允许该操作",
                       {"inbound_id": inbound_id, "status": inbound["status"]})
    return inbound


def update_inbound(inbound_id: int, fields: dict, operator: dict) -> dict:
    fields = fields or {}
    conn = database.get_connection()
    inbound = _load_inbound_for_change(conn, inbound_id, EDITABLE)
    clean: dict = {}
    if "name" in fields:
        clean["name"] = validators.require_str(fields["name"], "name", max_len=60)
    if "remark" in fields:
        clean["remark"] = validators.require_str(fields["remark"], "remark", max_len=200, required=False)
    if "in_date" in fields:
        day = str(fields["in_date"])
        if not timeutil.is_valid_iso(day):
            raise BizError(1002, "参数校验失败", {"errors": {"in_date": ["日期格式无效"]}})
        clean["in_date"] = day
    if "warehouse_id" in fields:
        warehouse = warehouse_repo.get(conn, validators.require_id(fields["warehouse_id"], "warehouse_id"))
        if warehouse is None:
            raise BizError(1006, "仓库不存在", {"warehouse_id": fields["warehouse_id"]})
        if not warehouse["is_active"]:
            raise BizError(1008, "仓库已停用，请换仓库", {"warehouse_id": warehouse["id"]})
        clean["warehouse_id"] = warehouse["id"]
    with database.transaction(conn):
        if fields.get("lines") or fields.get("details"):
            lines = _clean_lines(conn, fields.get("lines") or fields.get("details"), "inbound")
            order = purchase_repo.get_order(conn, inbound["order_id"]) if inbound["order_id"] else None
            _check_receivable(conn, order, lines)
            clean["total_qty"], clean["total_amount"] = _totals(lines)
            purchase_repo.replace_inbound_details(conn, inbound["id"], lines)
        if clean:
            purchase_repo.update_inbound(conn, inbound["id"], clean)
    return get_inbound(inbound["id"])


def submit_inbound(inbound_id: int, operator: dict) -> dict:
    conn = database.get_connection()
    inbound = _load_inbound_for_change(conn, inbound_id, SUBMITTABLE)
    if not purchase_repo.list_inbound_details(conn, inbound["id"]):
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细为空的单据不能提交"]}})
    purchase_repo.update_inbound(conn, inbound["id"],
                                 {"status": "SUBMITTED", "submit_at": timeutil.now_iso()})
    return get_inbound(inbound["id"])


def review_inbound(inbound_id: int, fields: dict, operator: dict,
                   trace_id: str | None = None) -> dict:
    """审批（admin）：APPROVE 时**同步过账**——写流水 + 回写订单可收量 + 订单收满自动关闭。

    ★ 改成异步/回调会引入「审批通过但库存没加」的中间态，这正是参照对象（yudao）的短板
      （它的审批回调只写资产、不写库存）。本项目必须整单原子（docs/13 第 2 章铁律 2）。
    """
    fields = fields or {}
    action = str(fields.get("action") or "").upper()
    if action not in ("APPROVE", "REJECT"):
        raise BizError(1002, "参数校验失败", {"errors": {"action": ["只能是 APPROVE 或 REJECT"]}})
    conn = database.get_connection()
    inbound = _load_inbound_for_change(conn, inbound_id, ("SUBMITTED",))
    now = timeutil.now_iso()
    if action == "REJECT":
        reason = validators.require_str(fields.get("reject_reason"), "reject_reason", max_len=200)
        purchase_repo.update_inbound(conn, inbound["id"],
                                     {"status": "REJECTED", "approve_at": now,
                                      "approver_id": operator["id"], "reject_reason": reason})
        return get_inbound(inbound["id"])

    details = purchase_repo.list_inbound_details(conn, inbound["id"])
    if not details:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细为空的单据不能过账"]}})
    lines = [{"client_msg_id": _bill_msg_id(inbound["code"], d["id"]),
              "product_id": d["product_id"], "warehouse_id": inbound["warehouse_id"],
              "quantity": int(d["qty"]), "direction": "IN", "unit_price": d["price"],
              "partner_id": inbound["partner_id"]}
             for d in details]
    with database.transaction(conn):
        stock_service.post_bill_lines(lines, operator, "PURCHASE_IN", inbound["id"],
                                     remark=f"采购入库 {inbound['code']}", trace_id=trace_id)
        for d in details:  # 权威校验：条件更新 0 行 = 超收（并发双点审批也挡得住）
            if d["order_detail_id"] and purchase_repo.bump_received(
                    conn, d["order_detail_id"], int(d["qty"])) == 0:
                raise BizError(1016, "入库数量超出订单可收数量，已整体回滚",
                               {"order_detail_id": d["order_detail_id"], "requested": int(d["qty"])})
        purchase_repo.update_inbound(conn, inbound["id"],
                                     {"status": "APPROVED", "approve_at": now,
                                      "approver_id": operator["id"], "posted_at": now,
                                      "posted_by": operator["id"], "reject_reason": None})
        if inbound["order_id"]:
            _maybe_close_order(conn, inbound["order_id"], now)
    return get_inbound(inbound["id"])


def _maybe_close_order(conn, order_id: int, now: str) -> None:
    """订单全部明细收满 → 自动 CLOSED（不做手工关闭入口，避免状态与收量不符）。"""
    details = purchase_repo.list_order_details(conn, order_id)
    if details and all(int(d["received_qty"]) >= int(d["qty"]) for d in details):
        purchase_repo.update_order(conn, order_id, {"status": "CLOSED", "approve_at": now})


def cancel_inbound(inbound_id: int, operator: dict) -> dict:
    conn = database.get_connection()
    inbound = _load_inbound_for_change(conn, inbound_id, ("DRAFT", "REJECTED", "SUBMITTED"))
    purchase_repo.update_inbound(conn, inbound["id"], {"status": "CANCELED", "is_active": 0})
    return get_inbound(inbound["id"])
