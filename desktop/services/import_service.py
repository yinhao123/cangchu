"""基础数据批量导入 / 导出 —— docs/14 第 3 章（P0-1）

商用基础版的「客户第一周就会撞上」的第一件事：把现有商品档案与期初库存搬进来。
没有这条通路，800 个 SKU 只能手输；而用「入库单」刷期初会污染采购统计与供应商对账

设计要点（与 docs/14 §3 一致，不得简化）：
1. **两段式无状态**：preview（dry_run）与 commit 共用同一套解析/校验函数，
   避免「预检说通过、提交却失败」这种最伤信任的分歧。
2. **期初库存只能走 `stock_service.opening_stock()`**：账本唯一入口，
   `ref_type='OPENING'`，幂等键由 `barcode + warehouse_code` 确定性派生（T-40）。
3. **账本非空拒绝期初**（1021），但**先判幂等**：同一份文件重复提交应返回 duplicated，
   而不是被「账本已有流水」误拦 —— 否则验收里的「提交两次」永远过不了。
4. **编码**：`utf-8-sig` → `gbk` 回退（T-39）。Excel「另存为 CSV」在国内默认 GBK，
   只按 UTF-8 解析会把中文读成乱码而**不报错**，那是最难查的一类事故。
"""
from __future__ import annotations

import csv
import io
import sqlite3

from core.errors import BizError
from models import database, import_repo, partner_repo, product_repo, stock_repo
from services import stock_service, timeutil, validators

MAX_ROWS = 5000
MAX_BYTES = 5 * 1024 * 1024

KINDS = ("products", "partners", "opening-stock")

#: 字段 → 中文（错误提示与导出表头共用，避免两处各写一份文案）
LABELS = {
    "name": "名称", "barcode": "条码", "category": "分类", "unit": "单位",
    "cost_price": "成本价", "sale_price": "售价", "min_stock": "预警库存", "remark": "备注",
    "type": "类型", "contact": "联系人", "phone": "电话", "address": "地址",
    "warehouse_code": "仓库", "qty": "数量",
}

TEMPLATES = {
    "products": {
        "filename": "products_template.csv",
        "columns": ("name", "barcode", "category", "unit", "cost_price", "sale_price",
                    "min_stock", "remark"),
        "required": ("name",),
        "samples": (
            ("矿泉水 550ml", "6900000000001", "饮料", "瓶", "1.20", "2.00", "50",
             "示例行，导入前请删除"),
            ("无条码散装商品", "", "其他", "件", "3.50", "6.00", "10", ""),
        ),
        "notes": (
            "商品导入：name 必填；barcode 可空，填了就必须是 3~48 位字母/数字/连字符",
            "cost_price、sale_price 是参考价，留空按 0；min_stock 是预警线，留空按 0",
            "以 # 开头的行与空行会被忽略",
        ),
    },
    "partners": {
        "filename": "partners_template.csv",
        "columns": ("name", "type", "contact", "phone", "address", "remark"),
        "required": ("name", "type"),
        "samples": (
            ("示例客户 张三超市", "CUSTOMER", "张三", "13800000000", "城东区人民路 1 号",
             "示例行，导入前请删除"),
            ("示例供应商 华信包装", "SUPPLIER", "李四", "0755-1234567", "宝安区福永街道", ""),
        ),
        "notes": (
            "往来单位导入：type 只能是 CUSTOMER（客户）或 SUPPLIER（供应商），大小写不敏感",
            "同名同类型不能重复登记；以 # 开头的行与空行会被忽略",
        ),
    },
    "opening-stock": {
        "filename": "opening_stock_template.csv",
        "columns": ("barcode", "warehouse_code", "qty", "remark"),
        "required": ("barcode", "warehouse_code", "qty"),
        "samples": (
            ("6900000000001", "主仓库", "100", "示例行，导入前请删除"),
            ("6900000000002", "WH-FG", "50", ""),
        ),
        "notes": (
            "期初建账只能在新账本（还没有任何流水）上执行；已有流水请改用盘点单修正",
            "warehouse_code 可填仓库编码或仓库名称；qty 必须是正整数",
            "同一份文件重复导入不会重复加库存（幂等键由 条码+仓库 派生）",
        ),
    },
}


# ==================== 模板 ====================

def template_csv(kind) -> tuple[bytes, str]:
    key, tpl = _template(kind)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(tpl["columns"])
    for row in tpl["samples"]:
        writer.writerow(row)
    for note in tpl["notes"]:
        writer.writerow(["# " + note])
    return _bom(buf.getvalue()), tpl["filename"]


def _bom(text: str) -> bytes:
    """UTF-8 BOM + CRLF：Excel 双击打开中文不乱码（T-10）。"""
    return ("\ufeff" + text).encode("utf-8")


# ==================== 预览 / 提交 ====================

def preview(kind, raw: bytes) -> dict:
    key, items, unknown = _parse(kind, raw)
    errors, clean = _validate(key, items)
    return {
        "kind": key,
        "total": len(items),
        "valid": len(clean),
        "invalid": len(errors),
        "errors": errors[:100],
        "errors_truncated": len(errors) > 100,
        "preview": [{k: v for k, v in item.items() if not k.startswith("__")}
                    for item in clean[:20]],
        "unknown_columns": unknown,
    }


def commit(kind, raw: bytes, skip_invalid: bool, operator: dict) -> dict:
    key, items, unknown = _parse(kind, raw)
    errors, clean = _validate(key, items)
    if errors and not skip_invalid:
        raise BizError(1019, f"文件中有 {len(errors)} 处不合法，已取消导入",
                       {"errors": {"file": ["请修正后重试，或勾选「跳过不合法行」"]},
                        "rows": errors[:200], "invalid": len(errors)})
    # 只有表头（或全是空行 / # 注释行）时才叫「没有数据行」；
    # 有数据行但全部不合法是另一回事，必须如实回「0 成功 + N 不合法」，不能误报成空文件。
    if not items:
        raise BizError(1019, "文件没有数据行", {"errors": {"file": ["请下载模板填写后再上传"]}})
    if key == "opening-stock":
        result = _commit_opening(clean, operator)
    else:
        result = _commit_master(key, clean)
    # ★ total / valid / invalid 的口径必须与 preview 完全一致（同一份文件同一组数字）：
    #   total = 文件数据行数（不含表头、空行与 # 注释行），valid = 可导入行数。
    #   历史上 commit.total 只统计合法行数，和 preview.total 对不上，前端一对比就误判。
    result.update({"kind": key, "total": len(items), "valid": len(clean),
                   "invalid": len(errors), "errors": errors[:100],
                   "unknown_columns": unknown})
    return result


def _commit_master(key: str, clean: list[dict]) -> dict:
    conn = database.get_connection()
    ok, failed = 0, []
    with database.transaction(conn):
        for index, item in enumerate(clean):
            point = f"imp_{index}"
            conn.execute(f"SAVEPOINT {point}")
            try:
                if key == "products":
                    import_repo.insert_product(conn, item)
                else:
                    import_repo.insert_partner(conn, item)
                conn.execute(f"RELEASE {point}")
                ok += 1
            except sqlite3.IntegrityError as exc:
                # 预检已经查过冲突；这里兜住并发窗口（别人刚好插了同一条）
                conn.execute(f"ROLLBACK TO {point}")
                conn.execute(f"RELEASE {point}")
                failed.append({"row": item["row"], "msg": _integrity_msg(exc)})
    # 行数统计统一由 commit() 填（total/valid/invalid），这里只回执行结果
    return {"ok_count": ok, "failed_count": len(failed), "duplicated_count": 0,
            "failed_rows": failed}


def _commit_opening(clean: list[dict], operator: dict) -> dict:
    conn = database.get_connection()
    has_logs = import_repo.count_stock_logs(conn) > 0
    pending, duplicated = [], 0
    # ★ 先判幂等再判「账本非空」：重复提交同一份文件时要返回 duplicated=true，
    #   而不是被 1021 误拦（docs/14 3.9 的验收项就卡在这个顺序上）。
    for item in clean:
        msg_id = stock_service.opening_msg_id(item["barcode"], item["warehouse_code"])
        if stock_repo.find_log_by_client_msg_id(conn, msg_id) is not None:
            duplicated += 1
            continue
        pending.append(item)
    if pending and has_logs:
        raise BizError(1021, "本账本已有流水，不能导入期初库存",
                       {"errors": {"file": ["期初建账只能在新库执行；如需修正请用盘点单"]},
                        "pending": len(pending)})
    ok, failed = 0, []
    with database.transaction(conn):
        for index, item in enumerate(pending):
            point = f"op_{index}"
            conn.execute(f"SAVEPOINT {point}")
            try:
                stock_service.opening_stock({
                    "barcode": item["barcode"],
                    "warehouse_id": item["warehouse_id"],
                    "warehouse_code": item["warehouse_code"],
                    "quantity": item["qty"],
                    "remark": item["remark"] or "期初建账",
                }, operator)
                conn.execute(f"RELEASE {point}")
                ok += 1
            except BizError as exc:
                conn.execute(f"ROLLBACK TO {point}")
                conn.execute(f"RELEASE {point}")
                failed.append({"row": item["row"], "msg": exc.msg})
    return {"ok_count": ok, "failed_count": len(failed),
            "duplicated_count": duplicated, "failed_rows": failed,
            "duplicated": bool(clean) and duplicated == len(clean)}


# ==================== 解析 ====================

def _template(kind):
    key = str(kind or "").strip().lower()
    tpl = TEMPLATES.get(key)
    if tpl is None:
        raise BizError(1006, "不支持的导入类型", {"kind": kind, "supported": list(KINDS)})
    return key, tpl


def _decode(raw: bytes) -> str:
    if not raw:
        raise BizError(1019, "导入文件为空", {"errors": {"file": ["请下载模板填写后再上传"]}})
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BizError(1019, "导入文件编码无法识别",
                   {"errors": {"file": ["请在 Excel 里另存为「CSV UTF-8」或「CSV（逗号分隔）」后重试"]}})


def _parse(kind, raw: bytes):
    key, tpl = _template(kind)
    if raw is None or len(raw) == 0:
        raise BizError(1019, "导入文件为空", {"errors": {"file": ["请下载模板填写后再上传"]}})
    if len(raw) > MAX_BYTES:
        raise BizError(1020, "导入文件过大",
                       {"errors": {"file": [f"单个文件不得超过 {MAX_BYTES // 1024 // 1024} MB"]}})
    text = _decode(raw)
    header, rows = None, []
    try:
        reader = csv.reader(io.StringIO(text))
        for cells in reader:
            cells = [str(cell).strip().lstrip("\ufeff") for cell in cells]
            if not any(cells) or cells[0].startswith("#"):
                continue
            if header is None:
                header = cells
                continue
            rows.append((reader.line_num, cells))
    except csv.Error as exc:
        raise BizError(1019, "CSV 格式错误", {"errors": {"file": [str(exc)[:120]]}})
    if header is None:
        raise BizError(1019, "文件没有表头", {"errors": {"file": ["第一行必须是模板表头"]}})
    if len(rows) > MAX_ROWS:
        raise BizError(1020, "导入行数超限",
                       {"errors": {"file": [f"单次最多 {MAX_ROWS} 行（当前 {len(rows)} 行），请拆分文件"]}})
    index = {name: pos for pos, name in enumerate(header)}
    missing = [column for column in tpl["required"] if column not in index]
    if missing:
        raise BizError(1019, "表头缺列",
                       {"errors": {"file": [f"缺少列：{'、'.join(LABELS.get(c, c) for c in missing)}"]},
                        "missing_columns": missing})
    unknown = [name for name in header if name and name not in tpl["columns"]]
    items = []
    for line_no, cells in rows:
        # 行号用 CSV 物理行号（Excel 里看到的就是这个），便于用户直接定位
        record = {"__row": line_no}
        for column in tpl["columns"]:
            pos = index.get(column)
            record[column] = cells[pos] if pos is not None and pos < len(cells) else ""
        items.append(record)
    return key, items, unknown


# ==================== 校验 ====================

def _validate(key: str, items: list[dict]):
    errors: list[dict] = []
    clean: list[dict] = []
    if key == "products":
        _validate_products(items, errors, clean)
    elif key == "partners":
        _validate_partners(items, errors, clean)
    else:
        _validate_opening(items, errors, clean)
    return errors, clean


def _err(errors, row, field, value, msg):
    errors.append({"row": row, "field": field, "value": _clip(value), "msg": msg})


def _clip(value, limit: int = 40) -> str:
    text = str(value if value is not None else "")
    return text if len(text) <= limit else text[:limit] + "…"


def _label(field: str) -> str:
    return LABELS.get(field, field)


def _text(errors, row, field, raw, max_len: int, required: bool = False, default=None):
    try:
        value = validators.require_str(raw, field, max_len=max_len, required=required)
        return value if value is not None else default
    except BizError as exc:
        _err(errors, row, field, raw,
             exc.data["errors"][field][0].replace(field, _label(field), 1))
        return default


def _number(errors, row, field, raw):
    """空 → None；支持 `¥1,234.50` 这种从 Excel 粘出来的写法。"""
    text = str(raw or "").strip()
    for token in ("¥", "￥", ",", " ", "　"):
        text = text.replace(token, "")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        _err(errors, row, field, raw, f"{_label(field)}必须是数字")
        return None


def _money(errors, row, field, raw):
    value = _number(errors, row, field, raw)
    if value is None:
        return 0.0
    if value < 0:
        _err(errors, row, field, raw, f"{_label(field)}不能为负")
        return 0.0
    return round(value, 2)


def _int_field(errors, row, field, raw, minimum: int = 0, maximum: int = 10 ** 6,
               default: int = 0, required: bool = False):
    value = _number(errors, row, field, raw)
    if value is None:
        if required:
            _err(errors, row, field, raw, f"{_label(field)}不能为空")
        return default
    if value != int(value):
        _err(errors, row, field, raw, f"{_label(field)}必须是整数")
        return default
    number = int(value)
    if not (minimum <= number <= maximum):
        _err(errors, row, field, raw, f"{_label(field)}需在 {minimum}~{maximum} 之间")
        return default
    return number


def _validate_products(items, errors, clean):
    conn = database.get_connection()
    seen_barcode: dict[str, int] = {}
    for item in items:
        row = item["__row"]
        before = len(errors)
        name = _text(errors, row, "name", item["name"], 100, required=True)
        barcode = None
        if str(item["barcode"] or "").strip():
            try:
                barcode = validators.normalize_barcode(item["barcode"])
            except BizError as exc:
                _err(errors, row, "barcode", item["barcode"],
                     exc.data["errors"]["barcode"][0].replace("barcode", "条码", 1))
            if barcode:
                if barcode in seen_barcode:
                    _err(errors, row, "barcode", barcode,
                         f"文件内第 {seen_barcode[barcode]} 行已用过该条码")
                else:
                    seen_barcode[barcode] = row
                    exist = product_repo.exists_barcode(conn, barcode)
                    if exist is not None:
                        _err(errors, row, "barcode", barcode,
                             f"条码已被「{exist['name']}」占用")
        category = _text(errors, row, "category", item["category"], 30, default="未分类") or "未分类"
        unit = _text(errors, row, "unit", item["unit"], 10, default="件") or "件"
        cost_price = _money(errors, row, "cost_price", item["cost_price"])
        sale_price = _money(errors, row, "sale_price", item["sale_price"])
        min_stock = _int_field(errors, row, "min_stock", item["min_stock"], 0, 10 ** 6)
        remark = _text(errors, row, "remark", item["remark"], 200)
        if len(errors) == before:
            clean.append({"row": row, "name": name, "barcode": barcode, "category": category,
                          "unit": unit, "cost_price": cost_price, "sale_price": sale_price,
                          "min_stock": min_stock, "remark": remark})


def _validate_partners(items, errors, clean):
    conn = database.get_connection()
    seen: dict[tuple, int] = {}
    for item in items:
        row = item["__row"]
        before = len(errors)
        name = _text(errors, row, "name", item["name"], 50, required=True)
        partner_type = None
        raw_type = str(item["type"] or "").strip().upper()
        if raw_type not in ("CUSTOMER", "SUPPLIER"):
            _err(errors, row, "type", item["type"], "类型只能是 CUSTOMER（客户）或 SUPPLIER（供应商）")
        else:
            partner_type = raw_type
        contact = _text(errors, row, "contact", item["contact"], 30)
        phone = _text(errors, row, "phone", item["phone"], 30)
        address = _text(errors, row, "address", item["address"], 100)
        remark = _text(errors, row, "remark", item["remark"], 200)
        if name and partner_type:
            key = (name, partner_type)
            if key in seen:
                _err(errors, row, "name", name, f"文件内第 {seen[key]} 行已登记过同名同类型")
            else:
                seen[key] = row
                if partner_repo.find_by_name_type(conn, name, partner_type) is not None:
                    _err(errors, row, "name", name, "该名称与类型在系统里已存在")
        if len(errors) == before:
            clean.append({"row": row, "name": name, "type": partner_type, "contact": contact,
                          "phone": phone, "address": address, "remark": remark})


def _validate_opening(items, errors, clean):
    conn = database.get_connection()
    seen: dict[tuple, int] = {}
    for item in items:
        row = item["__row"]
        before = len(errors)
        barcode = None
        try:
            barcode = validators.normalize_barcode(item["barcode"])
        except BizError as exc:
            _err(errors, row, "barcode", item["barcode"],
                 exc.data["errors"]["barcode"][0].replace("barcode", "条码", 1))
        if not barcode:
            if len(errors) == before:
                _err(errors, row, "barcode", item["barcode"], "期初建账必须给条码")
        else:
            product = product_repo.find_by_barcode(conn, barcode)
            if product is None:
                _err(errors, row, "barcode", barcode, "条码未登记，请先导入商品档案")
            elif not product["is_active"]:
                _err(errors, row, "barcode", barcode, "该商品已停用，不能建期初")

        warehouse = import_repo.find_warehouse(conn, item["warehouse_code"])
        warehouse_id = None
        if warehouse is None:
            _err(errors, row, "warehouse_code", item["warehouse_code"],
                 "仓库不存在，请填仓库编码或名称")
        elif not warehouse["is_active"]:
            _err(errors, row, "warehouse_code", item["warehouse_code"], "该仓库已停用")
        else:
            warehouse_id = warehouse["id"]

        qty = _int_field(errors, row, "qty", item["qty"], 1, validators.QUANTITY_MAX, required=True)
        remark = _text(errors, row, "remark", item["remark"], 200)

        if barcode and warehouse_id:
            key = (barcode, warehouse_id)
            if key in seen:
                _err(errors, row, "barcode", barcode,
                     f"文件内第 {seen[key]} 行已是同一商品同一仓库，请合并为一行")
            else:
                seen[key] = row
        if len(errors) == before:
            clean.append({"row": row, "barcode": barcode, "warehouse_id": warehouse_id,
                          "warehouse_code": str(item["warehouse_code"]).strip().upper(),
                          "qty": qty, "remark": remark})


def _integrity_msg(exc: sqlite3.IntegrityError) -> str:
    text = str(exc)
    if "products.barcode" in text:
        return "条码与已有商品冲突"
    if "partners.name" in text:
        return "同名同类型已存在"
    return "与已有记录冲突"


# ==================== 导出 ====================

EXPORT_KINDS = ("products", "partners", "stock-current")


def export_csv(kind) -> tuple[bytes, str, int]:
    """导出 CSV（utf-8-sig + CRLF）。

    `stock-current` 是当前库存快照，客户可拿它当「期初底稿」改完再导回
    （注意：导回时走的是 `opening-stock`，字段名不同 —— 期初模板用 barcode/warehouse_code）。
    """
    key = str(kind or "").strip().lower()
    if key not in EXPORT_KINDS:
        raise BizError(1006, "不支持的导出类型", {"kind": kind, "supported": list(EXPORT_KINDS)})
    conn = database.get_connection()
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")

    if key == "products":
        rows = import_repo.list_products_for_export(conn)
        writer.writerow(["名称", "条码", "分类", "单位", "成本价", "售价", "预警库存",
                         "备注", "状态", "创建时间"])
        for item in rows:
            writer.writerow([item["name"], item["barcode"] or "", item["category"] or "",
                             item["unit"], item["cost_price"], item["sale_price"],
                             item["min_stock"], item["remark"] or "",
                             "启用" if item["is_active"] else "停用", item["created_at"]])
        return _bom(buf.getvalue()), "products.csv", len(rows)

    if key == "partners":
        rows = import_repo.list_partners_for_export(conn)
        writer.writerow(["名称", "类型", "联系人", "电话", "地址", "备注", "状态", "创建时间"])
        for item in rows:
            writer.writerow([item["name"], item["type"], item["contact"] or "",
                             item["phone"] or "", item["address"] or "", item["remark"] or "",
                             "启用" if item["is_active"] else "停用", item["created_at"]])
        return _bom(buf.getvalue()), "partners.csv", len(rows)

    rows = import_repo.list_stock_for_export(conn)
    writer.writerow(["商品名称", "条码", "单位", "仓库", "数量", "更新时间"])
    for item in rows:
        writer.writerow([item["name"], item["barcode"] or "", item["unit"],
                         item["warehouse_name"], item["qty"], item["updated_at"]])
    return _bom(buf.getvalue()), "stock_current.csv", len(rows)


def export_filename(name: str) -> str:
    return f"{name[:-4]}_{timeutil.today_prefix()}.csv" if name.endswith(".csv") else name
