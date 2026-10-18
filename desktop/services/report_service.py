"""流水 / 库存 / 汇总 / CSV 导出 —— PRD 5.6 · docs/03 第 6 章 · 陷阱 T-05 / T-10 / T-19

★ 动态 SQL 片段（排序列）只能来自服务端白名单，值一律参数化。
★ 列表展示商品名一律用 product_name_snapshot（历史快照），仅当它为空才回落 products.name。
★ is_low_stock / total_qty 等派生值只在服务端算一次，两端不得各算一套（口径必须唯一）。
★ CSV 必须 utf-8-sig（带 BOM）+ \r\n，否则 Excel 打开中文乱码；上限 5 万行，超出拒绝并提示分段。
"""
from __future__ import annotations

import csv
import io

from core.errors import BizError
from models import database
from services import timeutil, validators

LOG_SORT = {"created_at": "l.created_at", "id": "l.id", "quantity": "l.quantity",
            "product_id": "l.product_id", "type": "l.type"}
SUMMARY_DIMENSIONS = {"DAY": "substr(l.created_at,1,10)", "PRODUCT": "l.product_name_snapshot",
                      "CATEGORY": "p.category", "WAREHOUSE": "w.name", "OPERATOR": "u.username"}
EXPORT_MAX_ROWS = 50_000


def _range(params: dict) -> tuple[str, str]:
    day = timeutil.today_prefix()
    return timeutil.day_range(params.get("date_from") or day, params.get("date_to") or day)


def _log_filters(params: dict) -> tuple[str, list]:
    conn = database.get_connection()
    where, args = ["substr(l.created_at,1,10) BETWEEN ? AND ?"], list(_range(params))
    if params.get("warehouse_id"):
        where.append("l.warehouse_id = ?"); args.append(validators.require_id(params["warehouse_id"], "warehouse_id"))
    if params.get("product_id"):
        where.append("l.product_id = ?"); args.append(validators.require_id(params["product_id"], "product_id"))
    if params.get("barcode"):
        where.append("l.product_id = (SELECT id FROM products WHERE barcode = ?)")
        args.append(validators.normalize_barcode(params["barcode"]))
    if params.get("keyword"):
        where.append("(l.product_name_snapshot LIKE ? OR p.barcode LIKE ?)")
        args += [f"%{params['keyword']}%", f"%{params['keyword']}%"]
    types = [t.strip().upper() for t in str(params.get("type") or "").split(",") if t.strip()]
    if types:
        bad = [t for t in types if t not in ("IN", "OUT", "ADJUST")]
        if bad:
            raise BizError(1002, "参数校验失败", {"errors": {"type": [f"非法类型 {bad}"]}})
        where.append(f"l.type IN ({','.join('?' * len(types))})"); args += types
    if params.get("operator_id"):
        where.append("l.operator_id = ?"); args.append(validators.require_id(params["operator_id"], "operator_id"))
    if params.get("source"):
        src = str(params["source"]).upper()
        if src not in ("WEB", "APP", "PDA"):
            raise BizError(1002, "参数校验失败", {"errors": {"source": ["只能是 WEB/APP/PDA"]}})
        where.append("l.source = ?"); args.append(src)
    if str(params.get("only_reversed", "")).lower() == "true":
        where.append("l.reversed_by IS NOT NULL")
    return " AND ".join(where), args


# 列清单与 JOIN 只写一份：流水列表与单条详情必须完全同形，否则模板字段会随调用点漂移
# ★ 显式列（T-19）：0007 新增的 unit_cost / cost_amount 属于成本口径，默认**不对前端下发**
#   （docs/14 7.5 要求仅 admin 可见，权限开关在 P1-6 落地，不能靠 l.* 提前漏给 operator）
_LOG_SELECT = """SELECT l.id, l.client_msg_id, l.product_id, l.warehouse_id, l.operator_id,
                        l.partner_id, l.type, l.quantity, l.before_qty, l.after_qty,
                        l.product_name_snapshot, l.unit_price, l.amount, l.source, l.device_name,
                        l.remark, l.reversed_by, l.ref_type, l.ref_id, l.trace_id, l.created_at,
                        p.name AS product_name_current, p.barcode, p.unit,
                        w.name AS warehouse_name, u.username AS operator_name, pa.name AS partner_name
                   FROM stock_logs l
                   LEFT JOIN products p ON p.id = l.product_id
                   LEFT JOIN warehouses w ON w.id = l.warehouse_id
                   LEFT JOIN users u ON u.id = l.operator_id
                   LEFT JOIN partners pa ON pa.id = l.partner_id"""


def query_logs(params: dict) -> dict:
    conn = database.get_connection()
    clause, args = _log_filters(params)
    page, page_size = validators.clamp_page(params.get("page"), params.get("page_size"))
    col = LOG_SORT.get(str(params.get("sort") or "created_at"))
    if col is None:  # 非法排序列静默回落，不报错也不进 SQL（T-05）
        col = LOG_SORT["created_at"]
    direction = "ASC" if str(params.get("order") or "desc").lower() == "asc" else "DESC"
    total = int(conn.execute(f"SELECT COUNT(*) FROM stock_logs l LEFT JOIN products p ON p.id=l.product_id "
                             f"WHERE {clause}", tuple(args)).fetchone()[0])
    rows = conn.execute(
        f"""{_LOG_SELECT}
             WHERE {clause}
             ORDER BY {col} {direction}, l.id {direction}
             LIMIT ? OFFSET ?""", tuple(args) + (page_size, (page - 1) * page_size)).fetchall()
    items = [_log_view(dict(r)) for r in rows]
    return {"list": items, "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}


def get_log(log_id) -> dict:
    """单条流水详情（冲销确认页）——复用列表页的列清单，保证字段名一致。"""
    conn = database.get_connection()
    row = conn.execute(f"{_LOG_SELECT} WHERE l.id = ?",
                       (validators.require_id(log_id, "id"),)).fetchone()
    if row is None:
        raise BizError(1006, "流水不存在")
    return _log_view(dict(row))


def list_operators() -> list[dict]:
    """筛选下拉用：只取展示所需列，password_hash 等敏感列绝不出库（T-19）。"""
    conn = database.get_connection()
    rows = conn.execute("SELECT id, username, display_name FROM users WHERE is_active = 1 "
                        "ORDER BY username").fetchall()
    return [dict(r) for r in rows]


def _log_view(row: dict) -> dict:
    """商品名优先用写入时的快照，仅当快照为空才回落当前名（PRD 5.3 改名不回溯）。"""
    row["product_name"] = row.get("product_name_snapshot") or row.get("product_name_current")
    row.pop("product_name_current", None)
    return row


def query_current(params: dict) -> dict:
    conn = database.get_connection()
    negative_only = str(params.get("negative_only", "")).lower() == "true"
    low_only = str(params.get("low_only", "")).lower() == "true"
    where, args = ["1=1"], []
    if negative_only:
        where.append("sc.qty < 0")
    if low_only:
        where.append("sc.qty < p.min_stock")
    if params.get("warehouse_id"):
        where.append("sc.warehouse_id = ?"); args.append(validators.require_id(params["warehouse_id"], "warehouse_id"))
    if params.get("product_id"):
        where.append("sc.product_id = ?"); args.append(validators.require_id(params["product_id"], "product_id"))
    if params.get("keyword"):
        where.append("(p.name LIKE ? OR p.barcode = ?)")
        args += [f"%{params['keyword']}%", params["keyword"]]
    clause = " AND ".join(where)
    page, page_size = validators.clamp_page(params.get("page"), params.get("page_size"))
    total = int(conn.execute(f"""SELECT COUNT(*) FROM stock_current sc JOIN products p ON p.id=sc.product_id
                                 WHERE {clause}""", tuple(args)).fetchone()[0])
    rows = conn.execute(
        f"""SELECT sc.product_id, sc.warehouse_id, sc.qty, sc.updated_at,
                   p.name AS product_name, p.barcode, p.unit, p.min_stock, p.category,
                   w.name AS warehouse_name,
                   (SELECT MAX(l.created_at) FROM stock_logs l WHERE l.product_id = sc.product_id
                          AND l.warehouse_id = sc.warehouse_id AND l.type='IN')  AS last_in_at,
                   (SELECT MAX(l.created_at) FROM stock_logs l WHERE l.product_id = sc.product_id
                          AND l.warehouse_id = sc.warehouse_id AND l.type='OUT') AS last_out_at
              FROM stock_current sc
              JOIN products p ON p.id = sc.product_id
              JOIN warehouses w ON w.id = sc.warehouse_id
             WHERE {clause}
             ORDER BY p.name, w.name LIMIT ? OFFSET ?""", tuple(args) + (page_size, (page - 1) * page_size)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["is_low_stock"] = bool(d["qty"] < (d["min_stock"] or 0))  # 口径只在这里定义一次
        items.append(d)
    return {"list": items, "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}


def summarize(params: dict) -> dict:
    conn = database.get_connection()
    dimension = str(params.get("dimension") or "DAY").upper()
    if dimension not in SUMMARY_DIMENSIONS:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"dimension": [f"只支持 {'/'.join(sorted(SUMMARY_DIMENSIONS))}"]}})
    day_from, day_to = _range(params)
    expr = SUMMARY_DIMENSIONS[dimension]
    rows = conn.execute(
        f"""SELECT {expr} AS key, {expr} AS label,
                   COALESCE(SUM(CASE WHEN l.type='IN' THEN l.quantity ELSE 0 END),0)  AS in_qty,
                   COALESCE(SUM(CASE WHEN l.type='OUT' THEN l.quantity ELSE 0 END),0) AS out_qty,
                   COALESCE(SUM(CASE WHEN l.type='IN' THEN l.amount ELSE 0 END),0)   AS in_amount,
                   COALESCE(SUM(CASE WHEN l.type='OUT' THEN l.amount ELSE 0 END),0)  AS out_amount,
                   COUNT(*) AS bill_count
              FROM stock_logs l
              LEFT JOIN products p ON p.id = l.product_id
              LEFT JOIN warehouses w ON w.id = l.warehouse_id
              LEFT JOIN users u ON u.id = l.operator_id
             WHERE substr(l.created_at,1,10) BETWEEN ? AND ?
             GROUP BY key ORDER BY key DESC""", (day_from, day_to)).fetchall()
    items = [dict(r) for r in rows]
    total = {"in_qty": sum(i["in_qty"] for i in items), "out_qty": sum(i["out_qty"] for i in items),
             "in_amount": round(sum(i["in_amount"] or 0 for i in items), 2),
             "out_amount": round(sum(i["out_amount"] or 0 for i in items), 2),
             "bill_count": sum(i["bill_count"] for i in items)}
    return {"dimension": dimension, "date_from": day_from, "date_to": day_to,
            "rows": items, "total": total}


def export_csv(params: dict):
    """同步流式导出（自用项目不引入异步任务框架，PRD 第 0 章）。上限 5 万行。"""
    conn = database.get_connection()
    clause, args = _log_filters(params)
    total = int(conn.execute(f"SELECT COUNT(*) FROM stock_logs l LEFT JOIN products p ON p.id=l.product_id "
                             f"WHERE {clause}", tuple(args)).fetchone()[0])
    if total > EXPORT_MAX_ROWS:
        raise BizError(1002, "参数校验失败",
                       {"errors": {"date": [f"结果 {total} 行超过上限 {EXPORT_MAX_ROWS}，请缩小日期范围分段导出"]}})
    rows = conn.execute(
        f"""SELECT l.* FROM stock_logs l LEFT JOIN products p ON p.id = l.product_id
             WHERE {clause} ORDER BY l.created_at DESC, l.id DESC""", tuple(args)).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["时间", "类型", "商品", "条码", "仓库", "数量", "结存", "单价", "金额",
                     "来源", "操作员", "往来单位", "备注", "单据号"])
    labels = {"IN": "入库", "OUT": "出库", "ADJUST": "盘点调整"}
    for r in rows:
        d = dict(r)
        writer.writerow([d["created_at"], labels.get(d["type"], d["type"]), d["product_name_snapshot"],
                         d.get("barcode") or "", d.get("warehouse_name") or "", d["quantity"], d["after_qty"],
                         d.get("unit_price") or "", d.get("amount") or "", d["source"],
                         d.get("operator_name") or "", d.get("partner_name") or "", d.get("remark") or "",
                         d["id"]])
    # ★ utf-8-sig：Excel 双击打开 CSV 时靠 BOM 识别 UTF-8，缺了就中文乱码（T-10）
    return ("\ufeff" + buf.getvalue()).encode("utf-8"), total
