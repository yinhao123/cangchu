"""销售订单 / 发货 / 销售退货 / 客户对账 —— docs/14 第 6 章（P1-4）

状态机（与采购同构，只多一步「发货」）：

    销售订单 SO：DRAFT →(submit)→ SUBMITTED →(admin approve)→ APPROVED
                     └→(admin reject)→ REJECTED             │
                                                            ├─ /ship 可多次（部分发货）
                                                            │    每次 OUT 流水 ref_type='SALES_OUT'
                                                            └─ 全部发满 → CLOSED（自动，无手工关闭入口）

    销售退货 SR：DRAFT → SUBMITTED →(admin approve，同步过账 IN，ref_type='SALES_RETURN')→ APPROVED

★ 单据不写库存：出库/退货都走 `stock_service.post_bill_lines`（铁律 1）。
★ 超发用条件更新拦：`bump_shipped` 返回 0 行 → 409/1016，整单回滚（铁律 8.6）。
★ 发货幂等靠 `shipment_id`（UUID v4，前端每次点“发货”生成一次）：同一 shipment_id 重放
  命中既有 `client_msg_id`，返回首次结果且**不重复回写** shipped_qty（铁律 3 的延伸）。
"""
from __future__ import annotations

from core.errors import BizError
from models import database, partner_repo, product_repo, sales_repo, stock_repo, warehouse_repo
from services import bill_no_service, settings_service, stock_service, timeutil, validators

EDITABLE = ("DRAFT", "REJECTED")
SUBMITTABLE = ("DRAFT", "REJECTED")
MAX_LINES = 200


def _ensure_enabled() -> None:
    """单据总开关：关闭后销售单据的写操作一律 409/1018，历史仍可查。"""
    if not settings_service.typed("wms_bills_enabled", True):
        raise BizError(1018, "WMS 单据功能已关闭，请在「设置 → 业务与提醒」中开启")


def _page(rows: list[dict], total: int, filters: dict) -> dict:
    page = int(filters.get("page") or 1)
    page_size = int(filters.get("page_size") or 20)
    return {"list": rows, "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}


def _bill_msg_id(bill_code: str, detail_id: int) -> str:
    return bill_no_service.bill_msg_id(bill_code, detail_id)


def _require_customer(conn, customer_id) -> dict:
    cid = validators.require_id(customer_id, "customer_id")
    partner = partner_repo.get(conn, cid)
    if partner is None:
        raise BizError(1006, "客户不存在", {"customer_id": cid})
    if partner["type"] != "CUSTOMER":
        raise BizError(1002, "参数校验失败",
                       {"errors": {"customer_id": ["销货单的往来单位必须是客户"]}})
    if not partner["is_active"]:
        raise BizError(1004, "该客户已停用，不能开单", {"customer_id": cid, "name": partner["name"]})
    return partner


def _require_warehouse(conn, warehouse_id) -> dict:
    wid = validators.require_id(warehouse_id, "warehouse_id")
    warehouse = warehouse_repo.get(conn, wid)
    if warehouse is None:
        raise BizError(1006, "仓库不存在", {"warehouse_id": wid})
    if not warehouse["is_active"]:
        raise BizError(1008, "仓库已停用，请换仓库", {"warehouse_id": wid})
    return warehouse


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
    if len(lines) > MAX_LINES:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": [f"单张单据最多 {MAX_LINES} 行"]}})
    out: list[dict] = []
    for index, raw in enumerate(lines):
        if not isinstance(raw, dict):
            raise BizError(1015, "单据明细非法", {"errors": {f"lines[{index}]": ["每行必须是对象"]}})
        product = _require_product(conn, raw.get("product_id"))
        qty = validators.require_positive_int(raw.get("qty"), "qty")
        price = raw.get("price")
        # 销售价默认取商品售价：现场开单时九成情况就是这个价，省一次手输
        price = product["sale_price"] if price in (None, "") else validators.require_money(price, "price")
        line = {
            "product_id": product["id"], "product_name_snap": product["name"],
            "barcode_snap": product.get("barcode"), "unit_snap": product.get("unit") or "件",
            "price": float(price), "qty": qty, "amount": round(qty * float(price), 2),
            "remark": validators.require_str(raw.get("remark"), "remark", max_len=200, required=False),
        }
        if kind == "return":
            odid = raw.get("order_detail_id")
            line["order_detail_id"] = validators.require_id(odid, "order_detail_id") if odid else None
        out.append(line)
    return out


def _totals(lines: list[dict]) -> tuple[int, float]:
    return sum(int(l["qty"]) for l in lines), round(sum(float(l["amount"]) for l in lines), 2)


# ==================== 销售订单 ====================

def list_orders(filters: dict) -> dict:
    rows, total = sales_repo.list_orders(database.get_connection(), filters or {})
    return _page(rows, total, filters or {})


def get_order(order_id: int) -> dict:
    conn = database.get_connection()
    order = sales_repo.get_order(conn, validators.require_id(order_id, "order_id"))
    if order is None:
        raise BizError(1006, "销货单不存在", {"order_id": order_id})
    details = sales_repo.list_order_details(conn, order["id"])
    for detail in details:
        detail["remaining_qty"] = max(0, int(detail["qty"]) - int(detail["shipped_qty"]))
    order["details"] = details
    order["shipped_qty"] = sum(int(d["shipped_qty"]) for d in details)
    order["remaining_qty"] = sum(int(d["remaining_qty"]) for d in details)
    return order


def create_order(fields: dict, operator: dict) -> dict:
    _ensure_enabled()
    fields = fields or {}
    name = validators.require_str(fields.get("name"), "name", max_len=60)
    remark = validators.require_str(fields.get("remark"), "remark", max_len=200, required=False)
    order_date = fields.get("order_date") or timeutil.now_iso()
    if not timeutil.is_valid_iso(str(order_date)):
        raise BizError(1002, "参数校验失败", {"errors": {"order_date": ["日期格式无效"]}})
    conn = database.get_connection()
    customer = _require_customer(conn, fields.get("customer_id"))
    warehouse = _require_warehouse(conn, fields.get("warehouse_id"))
    lines = _clean_lines(conn, fields.get("lines") or fields.get("details"), "order")
    total_qty, total_amount = _totals(lines)
    with database.transaction(conn):
        code = bill_no_service.next_code(conn, "SO")
        order_id = sales_repo.insert_order(conn, {
            "code": code, "name": name, "applicant_user_id": operator["id"],
            "customer_id": customer["id"], "customer_name_snap": customer["name"],
            "warehouse_id": warehouse["id"], "order_date": order_date,
            "total_qty": total_qty, "total_amount": total_amount, "remark": remark,
        })
        sales_repo.replace_order_details(conn, order_id, lines)
    return get_order(order_id)


def _load_order_for_change(conn, order_id: int, allowed: tuple[str, ...]) -> dict:
    _ensure_enabled()
    order = sales_repo.get_order(conn, validators.require_id(order_id, "order_id"))
    if order is None:
        raise BizError(1006, "销货单不存在", {"order_id": order_id})
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
    if "order_date" in fields:
        day = str(fields["order_date"])
        if not timeutil.is_valid_iso(day):
            raise BizError(1002, "参数校验失败", {"errors": {"order_date": ["日期格式无效"]}})
        clean["order_date"] = day
    if "customer_id" in fields:
        customer = _require_customer(conn, fields["customer_id"])
        clean.update({"customer_id": customer["id"], "customer_name_snap": customer["name"]})
    if "warehouse_id" in fields:
        warehouse = _require_warehouse(conn, fields["warehouse_id"])
        clean["warehouse_id"] = warehouse["id"]
    with database.transaction(conn):
        if fields.get("lines") or fields.get("details"):
            lines = _clean_lines(conn, fields.get("lines") or fields.get("details"), "order")
            clean["total_qty"], clean["total_amount"] = _totals(lines)
            sales_repo.replace_order_details(conn, order["id"], lines)
        if clean:
            sales_repo.update_order(conn, order["id"], clean)
    return get_order(order["id"])


def submit_order(order_id: int, operator: dict) -> dict:
    conn = database.get_connection()
    order = _load_order_for_change(conn, order_id, SUBMITTABLE)
    if not sales_repo.list_order_details(conn, order["id"]):
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细为空的单据不能提交"]}})
    sales_repo.update_order(conn, order["id"],
                            {"status": "SUBMITTED", "submit_at": timeutil.now_iso()})
    return get_order(order["id"])


def review_order(order_id: int, fields: dict, operator: dict) -> dict:
    """审批（admin）：APPROVE 只改状态，**不出库**；出库必须再点「发货」。"""
    fields = fields or {}
    action = str(fields.get("action") or "").upper()
    if action not in ("APPROVE", "REJECT"):
        raise BizError(1002, "参数校验失败", {"errors": {"action": ["只能是 APPROVE 或 REJECT"]}})
    conn = database.get_connection()
    order = _load_order_for_change(conn, order_id, ("SUBMITTED",))
    now = timeutil.now_iso()
    if action == "APPROVE":
        sales_repo.update_order(conn, order["id"],
                                {"status": "APPROVED", "approve_at": now,
                                 "approver_id": operator["id"], "reject_reason": None})
    else:
        reason = validators.require_str(fields.get("reject_reason"), "reject_reason", max_len=200)
        sales_repo.update_order(conn, order["id"],
                                {"status": "REJECTED", "approve_at": now,
                                 "approver_id": operator["id"], "reject_reason": reason})
    return get_order(order["id"])


def cancel_order(order_id: int, operator: dict) -> dict:
    conn = database.get_connection()
    order = _load_order_for_change(conn, order_id, ("DRAFT", "REJECTED", "SUBMITTED"))
    sales_repo.update_order(conn, order["id"], {"status": "CANCELED", "is_active": 0})
    return get_order(order["id"])


# ==================== 发货（可多次，部分发货） ====================

def ship_order(order_id: int, fields: dict, operator: dict, trace_id: str | None = None) -> dict:
    """发货出库：一次调用 = 一批 `SALES_OUT` 流水；可反复调用直到发满自动 CLOSED。

    ★`shipment_id` 必填（UUID v4）：它把「同一批发货」绑定成一个幂等单元，
      重放同一个 shipment_id 不会二次扣库存、也不会二次回写 shipped_qty。
    ★超发以 `bump_shipped` 的 rowcount 为准（1016），不靠「先查余量再决定」。
    """
    fields = fields or {}
    shipment_id = validators.require_client_msg_id({"client_msg_id": fields.get("shipment_id")})
    raw_lines = fields.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise BizError(1015, "发货明细非法", {"errors": {"lines": ["至少要发一行"]}})
    conn = database.get_connection()
    # 允许 CLOSED：发满后重放同一个 shipment_id 必须仍能命中幂等分支（返回 duplicated=true），
    # 否则「网络超时后前端重试」会拿到 409，用户不知道到底发没发。
    order = _load_order_for_change(conn, order_id, ("APPROVED", "CLOSED"))
    details = {d["id"]: d for d in sales_repo.list_order_details(conn, order["id"])}
    ship_lines: list[dict] = []
    for index, raw in enumerate(raw_lines):
        if not isinstance(raw, dict):
            raise BizError(1015, "发货明细非法", {"errors": {f"lines[{index}]": ["每行必须是对象"]}})
        detail_id = validators.require_id(raw.get("detail_id"), "detail_id")
        detail = details.get(detail_id)
        if detail is None:
            raise BizError(1006, "销货单明细不存在",
                           {"detail_id": detail_id, "errors": {f"lines[{index}]": ["该明细不属于本单"]}})
        qty = validators.require_positive_int(raw.get("qty"), "qty")
        msg_id = _bill_msg_id(f"{order['code']}#{shipment_id}", detail_id)
        replay = stock_repo.find_log_by_client_msg_id(conn, msg_id) is not None
        remaining = int(detail["qty"]) - int(detail["shipped_qty"])
        if not replay and qty > remaining:  # 友好提示（权威判定仍在 bump_shipped）
            raise BizError(1016, "发货数量超出可发数量",
                           {"detail_id": detail_id, "requested": qty, "receivable": remaining,
                            "errors": {f"lines[{index}].qty": [f"最多还能发 {remaining}"]}})
        ship_lines.append({
            "client_msg_id": msg_id,
            "product_id": detail["product_id"], "warehouse_id": order["warehouse_id"],
            "quantity": qty, "direction": "OUT", "unit_price": detail["price"],
            "partner_id": order["customer_id"], "detail_id": detail_id, "qty": qty,
        })
    now = timeutil.now_iso()
    with database.transaction(conn):
        views = stock_service.post_bill_lines(
            ship_lines, operator, "SALES_OUT", order["id"],
            remark=f"销货出库 {order['code']}", trace_id=trace_id)
        duplicated = all(bool(v.get("duplicated")) for v in views)
        if not duplicated and any(bool(v.get("duplicated")) for v in views):
            # 一半命中、一半没命中：说明这一批被别的请求拆着入账了，继续回写会把已入账的行再加一次
            raise BizError(1014, "该发货批次已部分入账，请刷新后按剩余数量重发",
                           {"order_id": order["id"]})
        if not duplicated:
            for line in ship_lines:
                if sales_repo.bump_shipped(conn, line["detail_id"], line["qty"]) == 0:
                    raise BizError(1016, "发货数量超出可发数量，已整体回滚",
                                   {"detail_id": line["detail_id"], "requested": line["qty"]})
            _maybe_close_order(conn, order["id"])
    result = get_order(order["id"])
    result["shipment"] = {
        "shipment_id": shipment_id, "duplicated": duplicated,
        "lines": [{"detail_id": line["detail_id"], "qty": line["qty"]} for line in ship_lines],
    }
    return result


def _maybe_close_order(conn, order_id: int) -> None:
    """全部明细发满 → 自动 CLOSED。不改 `approve_at`（那是审批时刻，不能被发货覆盖）。"""
    details = sales_repo.list_order_details(conn, order_id)
    if details and all(int(d["shipped_qty"]) >= int(d["qty"]) for d in details):
        sales_repo.update_order(conn, order_id, {"status": "CLOSED"})


# ==================== 销售退货 ====================

def list_returns(filters: dict) -> dict:
    rows, total = sales_repo.list_returns(database.get_connection(), filters or {})
    return _page(rows, total, filters or {})


def get_return(return_id: int) -> dict:
    conn = database.get_connection()
    doc = sales_repo.get_return(conn, validators.require_id(return_id, "return_id"))
    if doc is None:
        raise BizError(1006, "销售退货单不存在", {"return_id": return_id})
    doc["details"] = sales_repo.list_return_details(conn, doc["id"])
    return doc


def create_return(fields: dict, operator: dict) -> dict:
    _ensure_enabled()
    fields = fields or {}
    name = validators.require_str(fields.get("name"), "name", max_len=60)
    reason = validators.require_str(fields.get("return_reason"), "return_reason", max_len=200)
    remark = validators.require_str(fields.get("remark"), "remark", max_len=200, required=False)
    return_date = fields.get("return_date") or timeutil.now_iso()
    if not timeutil.is_valid_iso(str(return_date)):
        raise BizError(1002, "参数校验失败", {"errors": {"return_date": ["日期格式无效"]}})
    conn = database.get_connection()
    customer = _require_customer(conn, fields.get("customer_id"))
    warehouse = _require_warehouse(conn, fields.get("warehouse_id"))
    source_order = None
    if fields.get("source_order_id") is not None:
        sid = validators.require_id(fields["source_order_id"], "source_order_id")
        source_order = sales_repo.get_order(conn, sid)
        if source_order is None:
            raise BizError(1006, "源销货单不存在", {"source_order_id": sid})
        if source_order["customer_id"] != customer["id"]:
            raise BizError(1002, "参数校验失败",
                           {"errors": {"source_order_id": ["源销货单不属于该客户"]}})
    lines = _clean_lines(conn, fields.get("lines") or fields.get("details"), "return")
    total_qty, total_amount = _totals(lines)
    with database.transaction(conn):
        code = bill_no_service.next_code(conn, "SR")
        return_id = sales_repo.insert_return(conn, {
            "code": code, "name": name,
            "source_order_id": source_order["id"] if source_order else None,
            "order_code_snap": source_order["code"] if source_order else None,
            "customer_id": customer["id"], "customer_name_snap": customer["name"],
            "warehouse_id": warehouse["id"], "return_date": return_date,
            "total_qty": total_qty, "total_amount": total_amount,
            "return_reason": reason, "remark": remark,
        })
        sales_repo.replace_return_details(conn, return_id, lines)
    return get_return(return_id)


def _load_return_for_change(conn, return_id: int, allowed: tuple[str, ...]) -> dict:
    _ensure_enabled()
    doc = sales_repo.get_return(conn, validators.require_id(return_id, "return_id"))
    if doc is None:
        raise BizError(1006, "销售退货单不存在", {"return_id": return_id})
    if doc["posted_at"]:
        raise BizError(1014, "该退货单已过账，不能修改（要更正请走冲销）", {"return_id": return_id})
    if doc["status"] not in allowed:
        raise BizError(1014, f"单据当前状态为 {doc['status']}，不允许该操作",
                       {"return_id": return_id, "status": doc["status"]})
    return doc


def update_return(return_id: int, fields: dict, operator: dict) -> dict:
    fields = fields or {}
    conn = database.get_connection()
    doc = _load_return_for_change(conn, return_id, EDITABLE)
    clean: dict = {}
    if "name" in fields:
        clean["name"] = validators.require_str(fields["name"], "name", max_len=60)
    if "remark" in fields:
        clean["remark"] = validators.require_str(fields["remark"], "remark", max_len=200, required=False)
    if "return_reason" in fields:
        clean["return_reason"] = validators.require_str(fields["return_reason"], "return_reason",
                                                        max_len=200)
    if "return_date" in fields:
        day = str(fields["return_date"])
        if not timeutil.is_valid_iso(day):
            raise BizError(1002, "参数校验失败", {"errors": {"return_date": ["日期格式无效"]}})
        clean["return_date"] = day
    if "customer_id" in fields:
        customer = _require_customer(conn, fields["customer_id"])
        clean.update({"customer_id": customer["id"], "customer_name_snap": customer["name"]})
    if "warehouse_id" in fields:
        warehouse = _require_warehouse(conn, fields["warehouse_id"])
        clean["warehouse_id"] = warehouse["id"]
    with database.transaction(conn):
        if fields.get("lines") or fields.get("details"):
            lines = _clean_lines(conn, fields.get("lines") or fields.get("details"), "return")
            clean["total_qty"], clean["total_amount"] = _totals(lines)
            sales_repo.replace_return_details(conn, doc["id"], lines)
        if clean:
            sales_repo.update_return(conn, doc["id"], clean)
    return get_return(doc["id"])


def submit_return(return_id: int, operator: dict) -> dict:
    conn = database.get_connection()
    doc = _load_return_for_change(conn, return_id, SUBMITTABLE)
    if not sales_repo.list_return_details(conn, doc["id"]):
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细为空的单据不能提交"]}})
    sales_repo.update_return(conn, doc["id"],
                             {"status": "SUBMITTED", "submit_at": timeutil.now_iso()})
    return get_return(doc["id"])


def review_return(return_id: int, fields: dict, operator: dict,
                  trace_id: str | None = None) -> dict:
    """审批（admin）：APPROVE 时同步过账（IN 流水，ref_type='SALES_RETURN'）。"""
    fields = fields or {}
    action = str(fields.get("action") or "").upper()
    if action not in ("APPROVE", "REJECT"):
        raise BizError(1002, "参数校验失败", {"errors": {"action": ["只能是 APPROVE 或 REJECT"]}})
    conn = database.get_connection()
    doc = _load_return_for_change(conn, return_id, ("SUBMITTED",))
    now = timeutil.now_iso()
    if action == "REJECT":
        reason = validators.require_str(fields.get("reject_reason"), "reject_reason", max_len=200)
        sales_repo.update_return(conn, doc["id"],
                                 {"status": "REJECTED", "approve_at": now,
                                  "approver_id": operator["id"], "reject_reason": reason})
        return get_return(doc["id"])

    details = sales_repo.list_return_details(conn, doc["id"])
    if not details:
        raise BizError(1015, "单据明细非法", {"errors": {"lines": ["明细为空的单据不能过账"]}})
    lines = [{"client_msg_id": _bill_msg_id(doc["code"], d["id"]),
              "product_id": d["product_id"], "warehouse_id": doc["warehouse_id"],
              "quantity": int(d["qty"]), "direction": "IN", "unit_price": d["price"],
              "partner_id": doc["customer_id"]} for d in details]
    with database.transaction(conn):
        # 退货方向是 IN，但往来单位仍是客户 —— 显式指定 partner_type，别让方向推导误判成供应商
        stock_service.post_bill_lines(lines, operator, "SALES_RETURN", doc["id"],
                                      remark=f"销售退货 {doc['code']}", trace_id=trace_id,
                                      partner_type="CUSTOMER")
        sales_repo.update_return(conn, doc["id"],
                                 {"status": "APPROVED", "approve_at": now,
                                  "approver_id": operator["id"], "posted_at": now,
                                  "posted_by": operator["id"], "reject_reason": None})
    return get_return(doc["id"])


def cancel_return(return_id: int, operator: dict) -> dict:
    conn = database.get_connection()
    doc = _load_return_for_change(conn, return_id, ("DRAFT", "REJECTED", "SUBMITTED"))
    sales_repo.update_return(conn, doc["id"], {"status": "CANCELED", "is_active": 0})
    return get_return(doc["id"])


# ==================== 客户对账 ====================

def customer_statement(filters: dict) -> dict:
    """客户对账单：按客户 + 期间汇总「出库 / 退货 / 净额」，明细来自账本。

    ★只到「金额明细 + 合计」为止：不出应收凭证、不做账龄、不连开票（docs/14 2.3）。
    """
    filters = filters or {}
    conn = database.get_connection()
    # query string 里全是字符串，require_id 只认 int —— 这里先窄化，缺参/非法都归 1002
    try:
        partner_id = int(str(filters.get("partner_id")).strip())
    except (TypeError, ValueError):
        raise BizError(1002, "参数校验失败",
                       {"errors": {"partner_id": ["请选择客户"]}})
    customer = _require_customer(conn, partner_id)
    today = timeutil.now_iso()[:10]
    date_from = str(filters.get("from") or today[:8] + "01")[:10]
    date_to = str(filters.get("to") or today)[:10]
    if date_from > date_to:
        raise BizError(1002, "参数校验失败", {"errors": {"from": ["开始日期不能晚于结束日期"]}})
    rows = sales_repo.statement_rows(conn, customer["id"], date_from, date_to)
    lines = [{
        "date": str(row["created_at"])[:10],
        "code": row["doc_code"] or "",
        "biz": "销货出库" if row["ref_type"] == "SALES_OUT" else "销售退货",
        "direction": "OUT" if row["ref_type"] == "SALES_OUT" else "IN",
        "product_name": row["product_name_snapshot"],
        "qty": int(row["quantity"]),
        "price": round(float(row["unit_price"] or 0), 2),
        "amount": round(float(row["amount"] or 0), 2),
        "log_id": row["log_id"],
    } for row in rows]
    out_qty = sum(line["qty"] for line in lines if line["direction"] == "OUT")
    out_amount = round(sum(line["amount"] for line in lines if line["direction"] == "OUT"), 2)
    return_qty = sum(line["qty"] for line in lines if line["direction"] == "IN")
    return_amount = round(sum(line["amount"] for line in lines if line["direction"] == "IN"), 2)
    return {
        "customer": {"id": customer["id"], "name": customer["name"],
                     "contact": customer.get("contact"), "phone": customer.get("phone"),
                     "address": customer.get("address")},
        "from": date_from, "to": date_to,
        "lines": lines,
        "totals": {
            "out_qty": out_qty, "out_amount": out_amount,
            "return_qty": return_qty, "return_amount": return_amount,
            "net_qty": out_qty - return_qty,
            "net_amount": round(out_amount - return_amount, 2),
            "line_count": len(lines),
        },
    }


def statement_csv(data: dict) -> bytes:
    """对账单 CSV：`utf-8-sig` + `\\r\\n`（T-10，Excel 打开中文不乱码）。"""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    customer = data["customer"]
    writer.writerow([f"客户对账单：{customer['name']}",
                     f"{data['from']} ~ {data['to']}"])
    writer.writerow(["日期", "单据号", "业务", "商品", "数量", "单价", "金额"])
    for line in data["lines"]:
        writer.writerow([line["date"], line["code"], line["biz"], line["product_name"],
                         line["qty"], f"{line['price']:.2f}",
                         f"{line['amount']:.2f}"])
    totals = data["totals"]
    writer.writerow(["合计-出库", "", "", "", totals["out_qty"], "",
                     f"{totals['out_amount']:.2f}"])
    writer.writerow(["合计-退货", "", "", "", totals["return_qty"], "",
                     f"{totals['return_amount']:.2f}"])
    writer.writerow(["净额", "", "", "", totals["net_qty"], "",
                     f"{totals['net_amount']:.2f}"])
    return buf.getvalue().encode("utf-8-sig")
