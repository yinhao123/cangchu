"""审批待办 —— docs/13 第 6 章 · docs/03 第 12'.5 节

把三类单据里 `status='SUBMITTED'` 的合并成一个列表，按提交时间倒序。
★ 只读汇总，不做状态变更：审批动作仍走各自的 /approve 接口（状态机只有一处实现）。
★ 不做分页：待办天然是小集合（几十条），分页反而让人漏看。
"""
from __future__ import annotations

from models import database

BIZ_PURCHASE_ORDER = "PURCHASE_ORDER"
BIZ_PURCHASE_INBOUND = "PURCHASE_INBOUND"
BIZ_OPERATION_ORDER = "OPERATION_ORDER"
BIZ_STOCKTAKE = "STOCKTAKE"
BIZ_SALES_ORDER = "SALES_ORDER"
BIZ_SALES_RETURN = "SALES_RETURN"

BIZ_LABELS = {
    BIZ_PURCHASE_ORDER: "采购订单",
    BIZ_PURCHASE_INBOUND: "采购入库单",
    BIZ_OPERATION_ORDER: "操作单",
    BIZ_STOCKTAKE: "盘点单",
    BIZ_SALES_ORDER: "销货单",
    BIZ_SALES_RETURN: "销售退货单",
}

OP_LABELS = {
    "RECEIVE": "领用", "RETURN_STOCK": "退库", "RETURN_BORROW": "归还",
    "BORROW": "借用", "TRANSFER": "调拨",
}


def _mk(row, biz: str, **extra) -> dict:
    """统一待办条目结构：前端只按 biz 决定跳哪一页，字段名保持一致。"""
    item = dict(row)
    item.update({"biz": biz, "biz_label": BIZ_LABELS[biz], "op_type": None, "op_label": None})
    item.update(extra)
    return item


def list_pending(filters: dict) -> dict:
    conn = database.get_connection()
    wanted = str((filters or {}).get("type") or "").strip().upper()
    items: list[dict] = []

    if wanted in ("", BIZ_PURCHASE_ORDER):
        rows = conn.execute(
            """SELECT o.id, o.code, o.name, o.total_qty, o.total_amount, o.submit_at,
                      o.partner_name_snap AS counterparty,
                      COALESCE(u.display_name, u.username) AS submitter
                 FROM wms_purchase_order o
                 LEFT JOIN users u ON u.id = o.applicant_user_id
                WHERE o.status = 'SUBMITTED' AND o.is_active = 1""").fetchall()
        items += [_mk(r, BIZ_PURCHASE_ORDER) for r in rows]

    if wanted in ("", BIZ_PURCHASE_INBOUND):
        rows = conn.execute(
            """SELECT i.id, i.code, i.name, i.total_qty, i.total_amount, i.submit_at,
                      i.order_code_snap AS counterparty, w.name AS warehouse_name,
                      COALESCE(u.display_name, u.username) AS submitter
                 FROM wms_purchase_in_warehousing i
                 LEFT JOIN warehouses w ON w.id = i.warehouse_id
                 LEFT JOIN users      u ON u.id = i.posted_by
                WHERE i.status = 'SUBMITTED' AND i.is_active = 1""").fetchall()
        items += [_mk(r, BIZ_PURCHASE_INBOUND) for r in rows]

    if wanted in ("", BIZ_OPERATION_ORDER):
        rows = conn.execute(
            """SELECT o.id, o.code, o.name, o.total_qty, o.total_amount, o.submit_at,
                      o.op_type, w.name AS warehouse_name, t.name AS to_warehouse_name,
                      COALESCE(u.display_name, u.username) AS submitter,
                      COALESCE(u.display_name, u.username) AS operator_name
                 FROM wms_goods_operation_order o
                 LEFT JOIN warehouses w ON w.id = o.warehouse_id
                 LEFT JOIN warehouses t ON t.id = o.to_warehouse_id
                 LEFT JOIN users      u ON u.id = o.operator_user_id
                WHERE o.status = 'SUBMITTED' AND o.is_active = 1""").fetchall()
        items += [_mk(r, BIZ_OPERATION_ORDER, op_type=r["op_type"],
                      op_label=OP_LABELS.get(r["op_type"], r["op_type"])) for r in rows]

    if wanted in ("", BIZ_STOCKTAKE):
        # 盘点单没有独立 submit_at 列（docs/14 4.4 的 DDL 如此），
        # 但 SUBMITTED 之后不允许再改，因此 updated_at 就是提交时刻。
        rows = conn.execute(
            """SELECT s.id, s.code, s.name, s.updated_at AS submit_at,
                      w.name AS warehouse_name,
                      COALESCE(u.display_name, u.username) AS submitter
                 FROM wms_stocktake s
                 LEFT JOIN warehouses w ON w.id = s.warehouse_id
                 LEFT JOIN users      u ON u.id = s.submitter_id
                WHERE s.status = 'SUBMITTED'""").fetchall()
        # 盘点单没有金额/数量合计（盘的是实盘差异），统一给 0，保证前端字段形状一致
        items += [_mk(r, BIZ_STOCKTAKE, total_qty=0, total_amount=0) for r in rows]

    if wanted in ("", BIZ_SALES_ORDER):
        rows = conn.execute(
            """SELECT o.id, o.code, o.name, o.total_qty, o.total_amount, o.submit_at,
                      o.customer_name_snap AS counterparty, w.name AS warehouse_name,
                      COALESCE(u.display_name, u.username) AS submitter
                 FROM wms_sales_order o
                 LEFT JOIN warehouses w ON w.id = o.warehouse_id
                 LEFT JOIN users      u ON u.id = o.applicant_user_id
                WHERE o.status = 'SUBMITTED' AND o.is_active = 1""").fetchall()
        items += [_mk(r, BIZ_SALES_ORDER) for r in rows]

    if wanted in ("", BIZ_SALES_RETURN):
        rows = conn.execute(
            """SELECT r.id, r.code, r.name, r.total_qty, r.total_amount, r.submit_at,
                      r.customer_name_snap AS counterparty, w.name AS warehouse_name,
                      r.return_reason,
                      COALESCE(u.display_name, u.username) AS submitter
                 FROM wms_sales_return r
                 LEFT JOIN warehouses w ON w.id = r.warehouse_id
                 LEFT JOIN users      u ON u.id = r.approver_id
                WHERE r.status = 'SUBMITTED' AND r.is_active = 1""").fetchall()
        items += [_mk(r, BIZ_SALES_RETURN) for r in rows]

    items.sort(key=lambda x: (x.get("submit_at") or ""), reverse=True)
    return {"list": items, "total": len(items)}
