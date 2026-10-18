"""WMS 单据 CSV 导出 —— docs/13 第 6.2 节 · docs/03 第 12'.6 节 · 陷阱 T-10

★ 与 report_service 同一套口径：utf-8-sig（BOM）+ `\\r\\n`，否则 Excel 双击打开中文乱码。
★ 同步流式导出，上限 5 万行；超出直接拒绝并提示缩小日期范围（不引异步任务框架）。
★ 只导出「列表所见」的列；明细请走各自详情接口（避免一次导出几百列）。
"""
from __future__ import annotations

import csv
import io

from core.errors import BizError
from models import database, operation_repo, purchase_repo, sales_repo, stocktake_repo

EXPORT_MAX_ROWS = 50_000

STATUS_LABELS = {
    "DRAFT": "草稿", "SUBMITTED": "待审批", "APPROVED": "已审批", "REJECTED": "已驳回",
    "CLOSED": "已关闭", "CANCELED": "已作废", "DONE": "已完成", "REVERSED": "已冲销",
}
OP_LABELS = {
    "RECEIVE": "领用", "RETURN_STOCK": "退库", "RETURN_BORROW": "归还",
    "BORROW": "借用", "TRANSFER": "调拨",
}

BIZ = {
    "purchase-orders": "purchase_orders",
    "purchase-inbounds": "purchase_inbounds",
    "operation-orders": "operation_orders",
    "stocktakes": "stocktakes",
    "sales-orders": "sales_orders",
    "sales-returns": "sales_returns",
}


def _writer():
    buf = io.StringIO()
    return buf, csv.writer(buf, lineterminator="\r\n")


def _finish(buf, total: int, name: str):
    return ("\ufeff" + buf.getvalue()).encode("utf-8"), total, name


def _too_many(total: int):
    if total > EXPORT_MAX_ROWS:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"date": [f"结果 {total} 行超过上限 {EXPORT_MAX_ROWS}，请缩小日期范围分段导出"]}})


def export(biz: str, params: dict):
    """返回 (bytes, 行数, 文件名)。biz 走白名单，未知类型 404（不猜）。"""
    key = str(biz or "").strip().lower()
    if key not in BIZ:
        raise BizError(1006, "不支持的单据类型", {"biz": biz})
    filters = {**(params or {}), "page": 1, "page_size": EXPORT_MAX_ROWS + 1}
    conn = database.get_connection()

    if key == "purchase-orders":
        rows, total = purchase_repo.list_orders(conn, filters)
        _too_many(total)
        buf, writer = _writer()
        writer.writerow(["单号", "名称", "供应商", "申请日期", "数量合计", "金额合计",
                         "状态", "提交时间", "审批时间", "备注"])
        for r in rows:
            writer.writerow([r["code"], r["name"], r.get("partner_name_snap") or "",
                             r["applicant_date"], r["total_qty"], r["total_amount"],
                             STATUS_LABELS.get(r["status"], r["status"]),
                             r.get("submit_at") or "", r.get("approve_at") or "",
                             r.get("remark") or ""])
        return _finish(buf, total, "purchase_orders.csv")

    if key == "purchase-inbounds":
        rows, total = purchase_repo.list_inbounds(conn, filters)
        _too_many(total)
        buf, writer = _writer()
        writer.writerow(["单号", "名称", "源单号", "仓库", "入库日期", "数量合计", "金额合计",
                         "状态", "过账时间", "备注"])
        for r in rows:
            writer.writerow([r["code"], r["name"], r.get("order_code_snap") or "",
                             r.get("warehouse_name") or "", r["in_date"], r["total_qty"],
                             r["total_amount"], STATUS_LABELS.get(r["status"], r["status"]),
                             r.get("posted_at") or "", r.get("remark") or ""])
        return _finish(buf, total, "purchase_inbounds.csv")

    if key == "sales-orders":
        rows, total = sales_repo.list_orders(conn, filters)
        _too_many(total)
        buf, writer = _writer()
        writer.writerow(["单号", "名称", "客户", "出库仓库", "日期", "数量合计", "金额合计",
                         "状态", "提交时间", "审批时间", "备注"])
        for r in rows:
            writer.writerow([r["code"], r["name"], r.get("customer_name_snap") or "",
                             r.get("warehouse_name") or "", str(r["order_date"])[:10],
                             r["total_qty"], f"{float(r['total_amount']):.2f}",
                             STATUS_LABELS.get(r["status"], r["status"]),
                             (r.get("submit_at") or "")[:19].replace("T", " "),
                             (r.get("approve_at") or "")[:19].replace("T", " "),
                             r.get("remark") or ""])
        return _finish(buf, total, "sales-orders.csv")

    if key == "sales-returns":
        rows, total = sales_repo.list_returns(conn, filters)
        _too_many(total)
        buf, writer = _writer()
        writer.writerow(["单号", "名称", "客户", "退回仓库", "源销货单", "退货日期",
                         "数量合计", "金额合计", "退货原因", "状态", "过账时间", "备注"])
        for r in rows:
            writer.writerow([r["code"], r["name"], r.get("customer_name_snap") or "",
                             r.get("warehouse_name") or "", r.get("order_code_snap") or "无源单",
                             str(r["return_date"])[:10], r["total_qty"],
                             f"{float(r['total_amount']):.2f}", r.get("return_reason") or "",
                             STATUS_LABELS.get(r["status"], r["status"]),
                             (r.get("posted_at") or "")[:19].replace("T", " "),
                             r.get("remark") or ""])
        return _finish(buf, total, "sales-returns.csv")

    if key == "stocktakes":
        rows, total = stocktake_repo.list_all(conn, filters)
        _too_many(total)
        buf, writer = _writer()
        writer.writerow(["单号", "名称", "仓库", "快照时刻", "明细行数", "已盘行数",
                         "差异行数", "差异绝对值合计", "状态", "过账时间", "备注"])
        for r in rows:
            writer.writerow([r["code"], r["name"], r.get("warehouse_name") or "",
                             r["book_locked_at"], r["line_count"], r["counted_count"],
                             r["diff_count"], r["diff_abs_qty"],
                             STATUS_LABELS.get(r["status"], r["status"]),
                             r.get("posted_at") or "", r.get("remark") or ""])
        return _finish(buf, total, "stocktakes.csv")

    rows, total = operation_repo.list_orders(conn, filters)
    _too_many(total)
    buf, writer = _writer()
    writer.writerow(["单号", "名称", "类型", "源仓库", "目标仓库", "操作人", "日期",
                     "预计归还", "数量合计", "金额合计", "状态", "过账时间", "备注"])
    for r in rows:
        writer.writerow([r["code"], r["name"], OP_LABELS.get(r["op_type"], r["op_type"]),
                         r.get("warehouse_name") or "", r.get("to_warehouse_name") or "",
                         r.get("operator_name") or r.get("operator_username") or "",
                         r["op_date"], r.get("expect_return_date") or "", r["total_qty"],
                         r["total_amount"], STATUS_LABELS.get(r["status"], r["status"]),
                         r.get("posted_at") or "", r.get("remark") or ""])
    return _finish(buf, total, "operation_orders.csv")
