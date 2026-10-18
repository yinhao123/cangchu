"""商品业务规则 —— PRD 5.3 / docs/03 4.1~4.5

★ 软删商品的条码永久占用：不复用，需复用走「恢复」（PRD 4.3.5，防一码两品）。
★ PUT 的乐观锁用 updated_at 做版本戳：不匹配 → 1011（单人编辑概率低，但成本仅一个 WHERE）。
★ barcode 语义：不传=不改，传 null=清空（validators 的 partial 分支实现）。
"""
from __future__ import annotations

from core.errors import BizError
from models import database, product_repo
from services import validators


def list_products(params: dict) -> dict:
    conn = database.get_connection()
    page, page_size = validators.clamp_page(params.get("page"), params.get("page_size"))
    only_active = str(params.get("only_active", "true")).lower() != "false"
    items, total = product_repo.search(
        conn,
        keyword=params.get("keyword") or None,
        barcode=validators.normalize_barcode(params.get("barcode")) if params.get("barcode") else None,
        category=params.get("category") or None,
        only_active=only_active,
        page=page, page_size=page_size,
        sort=params.get("sort"), order=params.get("order"),
    )
    return {"list": items, "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}


def get(product_id: int) -> dict:
    conn = database.get_connection()
    item = product_repo.get(conn, validators.require_id(product_id, "product_id"))
    if item is None:
        raise BizError(1006, "商品不存在")
    return item


def get_for_scan(barcode_raw) -> dict:
    """扫码主接口（docs/03 4.2）：未登记 → 1007，APP 据此引导去电脑端新增。"""
    conn = database.get_connection()
    barcode = validators.normalize_barcode(barcode_raw)
    if not barcode:
        raise BizError(1002, "参数校验失败", {"errors": {"barcode": ["条码不能为空"]}})
    item = product_repo.find_by_barcode(conn, barcode)
    if item is None:
        raise BizError(1007, "条码未登记", {"barcode": barcode})
    if not item["is_active"]:
        raise BizError(1004, "该商品已停用，不能出入库", {"product_id": item["id"]})
    item["recent_logs"] = recent_logs(conn, item["id"])
    return item


def recent_logs(conn, product_id: int, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """SELECT id, type, quantity, created_at FROM stock_logs
            WHERE product_id = ? ORDER BY created_at DESC, id DESC LIMIT ?""", (product_id, limit)).fetchall()
    return [dict(r) for r in rows]


def create(fields: dict) -> dict:
    clean = validators.validate_product_payload(fields, partial=False)
    conn = database.get_connection()
    if clean.get("barcode"):
        conflict = product_repo.exists_barcode(conn, clean["barcode"])
        if conflict:
            raise BizError(1010, "条码已存在", {"conflict": conflict})
    try:
        return product_repo.create(conn, clean)
    except __import__("sqlite3").IntegrityError:
        # 并发下两个请求同时通过 exists 检查：让数据库唯一约束做最终裁判
        raise BizError(1010, "条码已存在", {"conflict": None})


def update(product_id: int, fields: dict) -> dict:
    conn = database.get_connection()
    current = product_repo.get(conn, product_id)
    if current is None:
        raise BizError(1006, "商品不存在")
    clean = validators.validate_product_payload(fields, partial=True)
    expected = clean.pop("expected_updated_at", None)
    if clean.get("barcode"):
        conflict = product_repo.exists_barcode(conn, clean["barcode"])
        if conflict and conflict["id"] != product_id:
            raise BizError(1010, "条码已存在", {"conflict": conflict})
    if expected is not None and expected != current["updated_at"]:
        raise BizError(1011, "该商品已被他人修改，请刷新后重试",
                       {"current_updated_at": current["updated_at"]})
    rowcount, updated = product_repo.update(conn, product_id, clean, expected)
    if rowcount == 0:
        if expected is not None:
            raise BizError(1011, "该商品已被他人修改，请刷新后重试",
                           {"current_updated_at": product_repo.get(conn, product_id)["updated_at"]})
        raise BizError(1006, "商品不存在")
    return updated


def soft_delete(product_id: int, hard: bool = False) -> dict:
    conn = database.get_connection()
    current = product_repo.get(conn, product_id)
    if current is None:
        raise BizError(1006, "商品不存在")
    logs = product_repo.log_count(conn, product_id)
    if hard:
        if logs:
            raise BizError(1010, "该商品存在历史流水，只能停用不能删除", {"log_count": logs})
        product_repo.hard_delete(conn, product_id)
        return {"deleted": "hard", "log_count": 0}
    product_repo.soft_delete(conn, product_id)
    return {"deleted": "soft", "log_count": logs,
            "note": "历史流水已保留；该条码将永久占用，如需再次使用请恢复此商品"}


def restore(product_id: int) -> dict:
    conn = database.get_connection()
    if product_repo.get(conn, product_id) is None:
        raise BizError(1006, "商品不存在")
    product_repo.restore(conn, product_id)
    return product_repo.get(conn, product_id)
