"""业务单据打印数据 —— docs/14 第 5 章（P0-3）

★ 打印**不由服务端渲染**（红线 8：views/ 只做静态服务与回退）：这里只产出「只读、稳定、含中文枚举与合计」
  的数据包，版式在前端 `components/PrintDocument.tsx` 用原生 table 画。
★ 前端不重算业务口径：`total_qty` / `total_amount` 一律取单据表上的服务端值（docs/04 前端不做业务计算）。
★ 签字栏是本文件里的 `SIGN_OFF` 单一真相，接口原样下发；前端不许再各写一份。
★ 为什么不用现有详情接口：详情为屏幕设计（含操作态、英文枚举值、无签字栏），
  打印需要一个与屏幕解耦的形状，否则每加一个按钮都要回来改打印页。
"""
from __future__ import annotations

from core.errors import BizError
from services import (
    operation_service, purchase_service, settings_service, stocktake_service, timeutil,
)
from services.wms_export_service import OP_LABELS, STATUS_LABELS

#: 单次最多打印多少张（批量打印 `?ids=1,2,3`）；再多请分次，避免一次渲染几百页卡死浏览器
MAX_DOCS = 20

#: 签字栏（唯一真相）：biz → 从左到右的签字位
SIGN_OFF: dict[str, tuple[str, ...]] = {
    "purchase-orders": ("采购经办", "主管审批", "供应商确认"),
    "purchase-inbounds": ("仓管", "送货人", "复核"),
    "stocktakes": ("盘点人", "复核人"),
    # 销货单是给客户签字的那张纸：客户签收栏必须有，否则打出来不能当交接凭据
    "sales-orders": ("销售经办", "仓管", "客户签收"),
}

#: 操作单按 op_type 各自的签字位：领用与调拨的责任人完全不同，不能共用一张
OPERATION_SIGN_OFF: dict[str, tuple[str, ...]] = {
    "RECEIVE": ("领用人", "仓管"),
    "RETURN_STOCK": ("退库人", "仓管"),
    "RETURN_BORROW": ("归还人", "仓管"),
    "BORROW": ("借用人", "仓管", "主管"),
    "TRANSFER": ("调出仓管", "调入仓管"),
}

BIZ_LABELS = {
    "purchase-orders": "采购订单",
    "purchase-inbounds": "采购入库单",
    "operation-orders": "领用 / 借还 / 调拨单",
    "stocktakes": "盘点单",
    "sales-orders": "销货单",
}

PAPERS = (
    {"key": "a4", "label": "A4 纵向（激光 / 喷墨）", "page": "A4 portrait", "margin": "12mm",
     "rows_per_page": 0},
    {"key": "dot", "label": "针式连续纸 241×140mm", "page": "241mm 140mm", "margin": "4mm",
     "rows_per_page": 6},
)


def sign_off(biz: str, op_type: str | None = None) -> list[str]:
    if biz == "operation-orders":
        return list(OPERATION_SIGN_OFF.get(op_type or "", ("领用人", "仓管")))
    return list(SIGN_OFF.get(biz, ("制单", "复核")))


# ==================== 元信息 ====================

def meta() -> dict:
    """打印元信息：抬头、联系方式、各单据签字栏、纸张预设（settings 驱动，前端不再硬编码）。"""
    name = str(settings_service.typed("company_name", "") or "").strip()
    phone = str(settings_service.typed("company_phone", "") or "").strip()
    address = str(settings_service.typed("company_address", "") or "").strip()
    return {
        "company": {
            "name": name or str(settings_service.typed("server_name", "ScanableSelf") or ""),
            "phone": phone,
            "address": address,
            "title_from_setting": bool(name),
        },
        "paper": str(settings_service.typed("print_paper", "a4") or "a4").lower(),
        "papers": [dict(item) for item in PAPERS],
        "biz": [
            {"key": key, "label": label,
             "sign_off": sign_off(key),
             "operation_sign_off": {op: list(v) for op, v in OPERATION_SIGN_OFF.items()}
             if key == "operation-orders" else None}
            for key, label in BIZ_LABELS.items()
        ],
        "printed_by": _user_name(),
        "server_time": timeutil.now_iso(),
    }


def _user_name() -> str:
    """当前操作者显示名（打印页脚「打印人」）。无请求上下文时返回空串。"""
    from core.acl import current_user   # 延迟引用：本模块也被脚本/测试直接调用

    try:
        user = current_user()
    except RuntimeError:  # 无请求上下文（脚本 / 测试直调）
        return ""
    return str(user.get("display_name") or user.get("username") or "") if user else ""


# ==================== 打印数据包 ====================

def documents(biz: str, ids, mode: str | None = None) -> dict:
    """`ids` 支持 `12` 或 `1,2,3`（批量）。返回 `{biz, biz_label, mode, docs:[...], total}`。"""
    key = str(biz or "").strip().lower()
    if key not in BIZ_LABELS:
        raise BizError(1006, "不支持的单据类型", {"biz": biz, "supported": list(BIZ_LABELS)})
    wanted = _parse_ids(ids)
    docs = [_document(key, one, mode) for one in wanted]
    info = meta()
    return {"biz": key, "biz_label": BIZ_LABELS[key], "mode": mode, "docs": docs,
            "total": len(docs), "company": info["company"], "printed_by": info["printed_by"],
            "server_time": info["server_time"]}


def _parse_ids(ids) -> list[int]:
    if isinstance(ids, int):
        raw = [str(ids)]
    else:
        raw = str(ids or "").replace(" ", "").split(",")
    out: list[int] = []
    for item in raw:
        if not item:
            continue
        try:
            value = int(item)
        except ValueError:
            raise BizError(1002, "参数校验失败", {"errors": {"id": [f"id 必须是整数：{item}"]}})
        if value <= 0:
            raise BizError(1002, "参数校验失败", {"errors": {"id": ["id 必须大于 0"]}})
        if value not in out:
            out.append(value)
    if not out:
        raise BizError(1002, "参数校验失败", {"errors": {"id": ["至少给一个单据 id"]}})
    if len(out) > MAX_DOCS:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"id": [f"一次最多打印 {MAX_DOCS} 张，请分批打印"]}})
    return out


def _document(biz: str, doc_id: int, mode: str | None) -> dict:
    if biz == "purchase-orders":
        return _purchase_order(doc_id)
    if biz == "purchase-inbounds":
        return _purchase_inbound(doc_id)
    if biz == "operation-orders":
        return _operation_order(doc_id)
    if biz == "sales-orders":
        return _sales_order(doc_id)
    return _stocktake(doc_id, mode)


def _sales_order(doc_id: int) -> dict:
    from services import sales_service

    order = sales_service.get_order(doc_id)
    doc = _base("sales-orders", order["code"], order["name"], order["status"])
    doc.update({
        "id": order["id"],
        "date": str(order.get("order_date") or "")[:10],
        "date_label": "销货日期",
        "warehouse": order.get("warehouse_name") or "",
        "counterparty_label": "客户",
        "counterparty": order.get("customer_name_snap") or "",
        "person_label": "经办人",
        "person": order.get("applicant_name") or "",
        "remark": order.get("remark") or "",
        "lines": [_line(index + 1, d.get("product_name_snap"), d.get("barcode_snap"),
                        d.get("unit_snap"), d.get("qty"), d.get("price"), d.get("amount"),
                        d.get("remark"))
                  for index, d in enumerate(order.get("details") or [])],
        "totals": {"total_qty": int(order.get("total_qty") or 0),
                   "total_amount": _money(order.get("total_amount"))},
    })
    doc["summary"] = [{"label": "数量合计", "value": doc["totals"]["total_qty"]},
                      {"label": "金额合计", "value": doc["totals"]["total_amount"]}]
    return doc


def _base(biz: str, code: str, name: str, status: str, op_type: str | None = None) -> dict:
    return {
        "biz": biz, "biz_label": BIZ_LABELS[biz], "kind": "bill",
        "code": code, "name": name,
        "status": status, "status_label": STATUS_LABELS.get(status, status),
        "op_type": op_type, "op_label": OP_LABELS.get(op_type or "", ""),
        "sign_off": sign_off(biz, op_type),
    }


def _money(value) -> float:
    return round(float(value or 0), 2)


def _line(no: int, product: str, barcode, unit, qty, price, amount, remark) -> dict:
    return {"no": no, "product_name": product or "", "barcode": barcode or "",
            "unit": unit or "件", "qty": int(qty or 0), "price": _money(price),
            "amount": _money(amount), "remark": remark or ""}


def _purchase_order(doc_id: int) -> dict:
    order = purchase_service.get_order(doc_id)
    doc = _base("purchase-orders", order["code"], order["name"], order["status"])
    doc.update({
        "id": order["id"],
        "date": str(order.get("applicant_date") or "")[:10],
        "date_label": "申请日期",
        "counterparty_label": "供应商",
        "counterparty": order.get("partner_name_snap") or "",
        "person_label": "申请人",
        "person": order.get("applicant_name") or "",
        "remark": order.get("remark") or "",
        "lines": [_line(index + 1, d.get("product_name_snap"), d.get("barcode_snap"),
                        d.get("unit_snap"), d.get("qty"), d.get("price"), d.get("amount"),
                        d.get("remark"))
                  for index, d in enumerate(order.get("details") or [])],
        "totals": {"total_qty": int(order.get("total_qty") or 0),
                   "total_amount": _money(order.get("total_amount"))},
    })
    doc["summary"] = [{"label": "数量合计", "value": doc["totals"]["total_qty"]},
                      {"label": "金额合计", "value": doc["totals"]["total_amount"]}]
    return doc


def _purchase_inbound(doc_id: int) -> dict:
    inbound = purchase_service.get_inbound(doc_id)
    doc = _base("purchase-inbounds", inbound["code"], inbound["name"], inbound["status"])
    doc.update({
        "id": inbound["id"],
        "date": str(inbound.get("in_date") or "")[:10],
        "date_label": "入库日期",
        "warehouse": inbound.get("warehouse_name") or "",
        "counterparty_label": "供应商",
        "counterparty": inbound.get("partner_name") or "",
        "person_label": "源订单",
        "person": inbound.get("order_code_snap") or "无源单",
        "remark": inbound.get("remark") or "",
        "lines": [_line(index + 1, d.get("product_name_snap"), d.get("barcode_snap"),
                        d.get("unit_snap"), d.get("qty"), d.get("price"), d.get("amount"),
                        d.get("remark"))
                  for index, d in enumerate(inbound.get("details") or [])],
        "totals": {"total_qty": int(inbound.get("total_qty") or 0),
                   "total_amount": _money(inbound.get("total_amount"))},
    })
    doc["summary"] = [{"label": "数量合计", "value": doc["totals"]["total_qty"]},
                      {"label": "金额合计", "value": doc["totals"]["total_amount"]}]
    return doc


def _operation_order(doc_id: int) -> dict:
    order = operation_service.get_order(doc_id)
    doc = _base("operation-orders", order["code"], order["name"], order["status"],
                op_type=order.get("op_type"))
    doc.update({
        "id": order["id"],
        "date": str(order.get("op_date") or "")[:10],
        "date_label": "业务日期",
        "warehouse": order.get("warehouse_name") or "",
        "to_warehouse": order.get("to_warehouse_name") or "",
        "counterparty_label": "往来单位",
        "counterparty": order.get("partner_name_snap") or order.get("partner_name") or "",
        "person_label": "经办人",
        "person": order.get("operator_name") or "",
        "remark": order.get("remark") or "",
        "lines": [_line(index + 1, d.get("product_name_snap"), d.get("barcode_snap"),
                        d.get("unit_snap"), d.get("qty"), d.get("price"), d.get("amount"),
                        d.get("remark"))
                  for index, d in enumerate(order.get("details") or [])],
        "totals": {"total_qty": int(order.get("total_qty") or 0),
                   "total_amount": _money(order.get("total_amount"))},
    })
    doc["summary"] = [{"label": "数量合计", "value": doc["totals"]["total_qty"]},
                      {"label": "金额合计", "value": doc["totals"]["total_amount"]}]
    return doc


def _stocktake(doc_id: int, mode: str | None) -> dict:
    data = stocktake_service.print_data(doc_id, mode or "blank")
    head = data["stocktake"]
    doc = _base("stocktakes", head["code"], head["name"], head["status"])
    doc.update({
        "id": head["id"], "kind": "stocktake",
        "date": str(head.get("book_locked_at") or "")[:10],
        "date_label": "快照日期",
        "warehouse": head.get("warehouse_name") or "",
        "counterparty_label": "",
        "counterparty": "",
        "person_label": "创建人",
        "person": head.get("operator_name") or "",
        "remark": head.get("remark") or "",
        "mode": data["mode"],
        "lines": [{"no": line["line_no"], "product_name": line["product_name"],
                   "barcode": line["barcode"] or "", "unit": line["unit"],
                   "qty": int(line["book_qty"]), "price": None, "amount": None,
                   "real_qty": line["real_qty"], "diff_qty": line["diff_qty"],
                   "remark": line["remark"] or ""}
                  for line in data["lines"]],
        "totals": {"line_count": int(head.get("line_count") or 0),
                   "counted_count": int(head.get("counted_count") or 0),
                   "diff_count": int(head.get("diff_count") or 0)},
    })
    doc["summary"] = [{"label": "明细行数", "value": doc["totals"]["line_count"]},
                      {"label": "已盘行数", "value": doc["totals"]["counted_count"]},
                      {"label": "差异行数", "value": doc["totals"]["diff_count"]}]
    return doc
