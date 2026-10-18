"""盘点单业务规则与事务边界 —— docs/14 第 4 章（P0-2）

状态机（docs/14 4.2）：
    DRAFT ──submit──▶ SUBMITTED ──post(admin)──▶ POSTED
      │                    └──reject(admin, 必填原因)──▶ REJECTED ──改──▶ SUBMITTED
      └──cancel──▶ CANCELED（未过账才可作废）

★ 盘点单自己不算账：过账只调 `stock_service.post_adjust_lines()`（写 stock_logs + stock_current），
  绝不 `UPDATE stock_current` / `UPDATE stock_logs`（铁律 1/2）。
★ 过账数量用**实时当前值**（`quantity = 实盘 - 实时当前`），不是「实盘 - 建单快照」：
  盘点期间有人出货时，用快照差会把已出的货加回来（docs/14 4.3 表 A/B，采用 B）。
  差异报表仍按 `book_qty` 快照口径统计，两个数字各有用途。
★ 整单原子 + 幂等：`client_msg_id = bill_msg_id(code, detail_id)`；状态更新用条件更新，
  重复点过账第二次 0 行 → 409/1014，流水不增加（docs/14 4.7）。
"""
from __future__ import annotations

from core.errors import BizError
from models import database, product_repo, stocktake_repo, warehouse_repo
from services import bill_no_service, settings_service, stock_service, timeutil, validators

EDITABLE = ("DRAFT", "REJECTED")
SUBMITTABLE = ("DRAFT", "REJECTED")
#: 单次 post_adjust_lines 的行数上限（与单据过账的 200 行同源）；超出分批，外层事务仍是整单原子
POST_CHUNK = 200
PRINT_MODES = ("blank", "diff")


def _ensure_enabled() -> None:
    """单据总开关（docs/03 12'.7）：关闭后单据写操作一律 409/1018，历史单据仍可查。

    ★ 盘点单也是单据（第四类），一并受开关约束；临时纠错仍可用「出入库 → 单条调整」，
      那条路径不经过单据层（docs/14 4.9）。
    """
    if not settings_service.typed("wms_bills_enabled", True):
        raise BizError(1018, "WMS 单据功能已关闭，请在「设置 → 业务与提醒」中开启")


def _page(rows: list[dict], total: int, filters: dict) -> dict:
    page = int(filters.get("page") or 1)
    page_size = int(filters.get("page_size") or 20)
    return {"list": rows, "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}


def _load(conn, stocktake_id: int) -> dict:
    doc = stocktake_repo.get(conn, validators.require_id(stocktake_id, "stocktake_id"))
    if doc is None:
        raise BizError(1006, "盘点单不存在", {"stocktake_id": stocktake_id})
    return doc


def _load_for_change(conn, stocktake_id: int, allowed: tuple[str, ...]) -> dict:
    doc = _load(conn, stocktake_id)
    if doc["status"] not in allowed:
        raise BizError(1014, f"盘点单当前状态为 {doc['status']}，不允许该操作",
                       {"stocktake_id": doc["id"], "status": doc["status"]})
    return doc


# ==================== 查询 ====================

def list_stocktakes(filters: dict) -> dict:
    rows, total = stocktake_repo.list_all(database.get_connection(), filters or {})
    return _page(rows, total, filters or {})


def get_stocktake(stocktake_id: int) -> dict:
    conn = database.get_connection()
    doc = _load(conn, stocktake_id)
    doc["details"] = stocktake_repo.list_details(conn, doc["id"])
    # 期间变动提示（docs/14 4.3 第 3 条）：>0 说明快照已过时，过账按实盘数调整，不阻断
    doc["period_changes"] = stocktake_repo.count_logs_since(conn, doc["warehouse_id"],
                                                            doc["book_locked_at"])
    doc["has_diff"] = bool(doc["diff_count"])
    return doc


# ==================== 建单 / 编辑 ====================

def create_stocktake(fields: dict, operator: dict) -> dict:
    _ensure_enabled()
    fields = fields or {}
    name = validators.require_str(fields.get("name"), "name", max_len=50)
    remark = validators.require_str(fields.get("remark"), "remark", max_len=200, required=False)
    category = validators.require_str(fields.get("category"), "category", max_len=50, required=False)
    conn = database.get_connection()
    warehouse_id = validators.require_id(fields.get("warehouse_id"), "warehouse_id")
    warehouse = warehouse_repo.get(conn, warehouse_id)
    if warehouse is None:
        raise BizError(1006, "仓库不存在", {"warehouse_id": warehouse_id})
    if not warehouse["is_active"]:
        raise BizError(1008, "仓库已停用，请换仓库", {"warehouse_id": warehouse_id,
                                                     "name": warehouse["name"]})
    if stocktake_repo.count_snapshot_rows(conn, warehouse_id, category) == 0:
        raise BizError(1014, "该仓库当前无库存可盘",
                       {"warehouse_id": warehouse_id, "warehouse_name": warehouse["name"],
                        "category": category,
                        "errors": {"warehouse_id": ["零库存商品与已停用商品不参与盘点；"
                                                    "临时纠错请用「出入库 → 单条调整」"]}})
    with database.transaction(conn):
        code = bill_no_service.next_code(conn, "ST")
        stocktake_id = stocktake_repo.insert(conn, {
            "code": code, "name": name, "warehouse_id": warehouse_id,
            "scope_category": category, "operator_id": operator["id"],
            "book_locked_at": timeutil.now_iso(), "remark": remark,
        })
        stocktake_repo.snapshot(conn, stocktake_id, warehouse_id, category)
    return get_stocktake(stocktake_id)


def update_stocktake(stocktake_id: int, fields: dict, operator: dict) -> dict:
    """仅 DRAFT/REJECTED 可改：改 name/remark + 批量录实盘数 + 追加行（docs/14 4.6）。"""
    _ensure_enabled()
    fields = fields or {}
    conn = database.get_connection()
    doc = _load_for_change(conn, stocktake_id, EDITABLE)
    clean: dict = {}
    if "name" in fields:
        clean["name"] = validators.require_str(fields["name"], "name", max_len=50)
    if "remark" in fields:
        clean["remark"] = validators.require_str(fields["remark"], "remark", max_len=200, required=False)
    with database.transaction(conn):
        _apply_lines(conn, doc, fields.get("lines"))
        _append_lines(conn, doc, fields.get("append"))
        if clean:
            stocktake_repo.update(conn, doc["id"], clean)
    return get_stocktake(doc["id"])


def _apply_lines(conn, doc: dict, lines) -> None:
    if not lines:
        return
    if not isinstance(lines, list):
        raise BizError(1015, "盘点明细非法", {"errors": {"lines": ["lines 必须是数组"]}})
    for index, raw in enumerate(lines):
        if not isinstance(raw, dict):
            raise BizError(1015, "盘点明细非法", {"errors": {f"lines[{index}]": ["每行必须是对象"]}})
        detail_id = validators.require_id(raw.get("detail_id"), "detail_id")
        # 归属校验：明细必须属于本单，防止跨单改数
        if stocktake_repo.get_detail(conn, doc["id"], detail_id) is None:
            raise BizError(1006, "盘点明细不存在",
                           {"detail_id": detail_id, "errors": {f"lines[{index}]": ["该明细不属于本单"]}})
        real_qty = _optional_qty(raw.get("real_qty"), f"lines[{index}].real_qty")
        stocktake_repo.set_real_qty(conn, detail_id, real_qty)


def _append_lines(conn, doc: dict, append) -> None:
    """追加行：不在原快照的商品按盘盈处理，`book_qty` 记 0（docs/14 4.7）。"""
    if not append:
        return
    if not isinstance(append, list):
        raise BizError(1015, "盘点明细非法", {"errors": {"append": ["append 必须是数组"]}})
    for index, raw in enumerate(append):
        if not isinstance(raw, dict):
            raise BizError(1015, "盘点明细非法", {"errors": {f"append[{index}]": ["每行必须是对象"]}})
        barcode = validators.normalize_barcode(raw.get("barcode"))
        if not barcode:
            raise BizError(1002, "参数校验失败",
                           {"errors": {f"append[{index}].barcode": ["追加行必须给条码"]}})
        product = product_repo.find_by_barcode(conn, barcode)
        if product is None:
            raise BizError(1007, "条码未登记", {"barcode": barcode})
        if not product["is_active"]:
            raise BizError(1004, "该商品已停用，不参与盘点",
                           {"product_id": product["id"], "barcode": barcode})
        if stocktake_repo.find_detail_by_product(conn, doc["id"], product["id"]) is not None:
            raise BizError(1002, "参数校验失败",
                           {"errors": {f"append[{index}].barcode": ["该商品已在本单明细中，请直接录实盘数"]}})
        real_qty = _optional_qty(raw.get("real_qty"), f"append[{index}].real_qty")
        remark = validators.require_str(raw.get("remark"), "remark", max_len=160, required=False)
        stocktake_repo.add_detail(conn, doc["id"], {
            "product_id": product["id"], "book_qty": 0, "real_qty": real_qty,
            "product_name_snapshot": product["name"], "remark": remark,
        })


def _optional_qty(value, field: str):
    """实盘数：None = 未盘（允许清空）；否则 0~1000000 整数（盘亏可以是 0）。"""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return validators.require_int(value, field, minimum=0, maximum=10 ** 6)


# ==================== 状态流转 ====================

def submit_stocktake(stocktake_id: int, operator: dict) -> dict:
    _ensure_enabled()
    conn = database.get_connection()
    doc = _load_for_change(conn, stocktake_id, SUBMITTABLE)
    if stocktake_repo.count_recorded(conn, doc["id"]) == 0:
        raise BizError(1015, "还没有录入任何实盘数",
                       {"errors": {"real_qty": ["至少录入一行实盘数才能提交"]}})
    changed = stocktake_repo.set_status(conn, doc["id"], SUBMITTABLE,
                                        {"status": "SUBMITTED", "submitter_id": operator["id"]})
    if changed == 0:  # 并发：别人刚提交过
        raise BizError(1014, "盘点单状态已变化，请刷新后重试", {"stocktake_id": doc["id"]})
    return get_stocktake(doc["id"])


def reject_stocktake(stocktake_id: int, fields: dict, operator: dict) -> dict:
    _ensure_enabled()
    fields = fields or {}
    reason = validators.require_str(fields.get("reason") or fields.get("reject_reason"),
                                    "reason", max_len=200)
    conn = database.get_connection()
    doc = _load_for_change(conn, stocktake_id, ("SUBMITTED",))
    changed = stocktake_repo.set_status(conn, doc["id"], "SUBMITTED",
                                        {"status": "REJECTED", "approver_id": operator["id"],
                                         "remark": _merge_remark(doc.get("remark"), f"驳回：{reason}")})
    if changed == 0:
        raise BizError(1014, "盘点单状态已变化，请刷新后重试", {"stocktake_id": doc["id"]})
    return get_stocktake(doc["id"])


def cancel_stocktake(stocktake_id: int, operator: dict) -> dict:
    """作废：只改状态，绝不删明细（单据可追溯）。已过账不可作废。"""
    _ensure_enabled()
    conn = database.get_connection()
    doc = _load_for_change(conn, stocktake_id, EDITABLE)
    changed = stocktake_repo.set_status(conn, doc["id"], EDITABLE, {"status": "CANCELED"})
    if changed == 0:
        raise BizError(1014, "盘点单状态已变化，请刷新后重试", {"stocktake_id": doc["id"]})
    return get_stocktake(doc["id"])


def post_stocktake(stocktake_id: int, operator: dict, trace_id: str | None = None) -> dict:
    """过账（admin）：把「实盘 - 实时当前」写成 ADJUST 流水，整单一个事务。

    ★ 无差异行不落账；全单无差异 → 409/1014（不是 200，避免 UI 误判成功）。
    ★ 分批调用 `post_adjust_lines`（每批 200 行），但外层事务保证整单原子：
      任一批失败，整个单据回滚，`stock_current` 一行不变（docs/14 4.7 注）。
    """
    _ensure_enabled()
    conn = database.get_connection()
    doc = _load(conn, stocktake_id)
    if doc["status"] != "SUBMITTED":
        raise BizError(1014, f"盘点单当前状态为 {doc['status']}，不能过账"
                             + ("（重复过账）" if doc["status"] == "POSTED" else ""),
                       {"stocktake_id": doc["id"], "status": doc["status"]})
    details = stocktake_repo.list_details(conn, doc["id"])
    lines = []
    for d in details:
        if d["real_qty"] is None:  # 未盘行不参与过账
            continue
        current = int(d["current_qty"] or 0)
        if int(d["real_qty"]) == current:  # 账实相符：不产生流水
            continue
        lines.append({
            "client_msg_id": bill_no_service.bill_msg_id(doc["code"], d["id"]),
            "product_id": d["product_id"], "warehouse_id": doc["warehouse_id"],
            "target_qty": int(d["real_qty"]), "book_qty": int(d["book_qty"]),
            "remark": d["remark"],
        })
    if not lines:
        raise BizError(1014, "全部商品账实相符，无需过账",
                       {"stocktake_id": doc["id"], "counted": len(details),
                        "errors": {"real_qty": ["所有已盘行的实盘数都等于当前库存"]}})
    now = timeutil.now_iso()
    with database.transaction(conn):
        for start in range(0, len(lines), POST_CHUNK):
            stock_service.post_adjust_lines(lines[start:start + POST_CHUNK], operator, doc["id"],
                                            trace_id=trace_id)
        changed = stocktake_repo.set_status(conn, doc["id"], "SUBMITTED",
                                           {"status": "POSTED", "posted_at": now,
                                            "approver_id": operator["id"]})
        if changed == 0:  # 并发过账：另一请求已抢先，本事务整体回滚
            raise BizError(1014, "该盘点单已被过账，请刷新", {"stocktake_id": doc["id"]})
    return get_stocktake(doc["id"])


# ==================== 打印数据（P0-3 消费） ====================

def print_data(stocktake_id: int, mode: str = "blank") -> dict:
    """返回打印所需数据：`blank` 空白盘点表（实盘列留空）/ `diff` 差异表（快照口径）。"""
    key = str(mode or "blank").strip().lower()
    if key not in PRINT_MODES:
        raise BizError(1006, "不支持的打印类型", {"mode": mode, "supported": list(PRINT_MODES)})
    conn = database.get_connection()
    doc = _load(conn, stocktake_id)
    details = stocktake_repo.list_details(conn, doc["id"])
    if key == "diff":
        details = [d for d in details if d["real_qty"] is not None and int(d["diff_qty"]) != 0]
    lines = [{
        "line_no": index + 1,
        "barcode": d.get("barcode"), "product_name": d["product_name_snapshot"],
        "unit": d.get("unit") or "件", "book_qty": int(d["book_qty"]),
        "real_qty": None if key == "blank" else d["real_qty"],
        "diff_qty": None if key == "blank" else int(d["diff_qty"]),
        "remark": d.get("remark"),
    } for index, d in enumerate(details)]
    return {"mode": key, "stocktake": doc, "lines": lines, "total": len(lines)}


def _merge_remark(old, extra: str) -> str:
    text = f"{old} | {extra}" if old else extra
    return text[:200]
