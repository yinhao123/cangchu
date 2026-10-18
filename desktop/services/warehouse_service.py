"""仓库业务规则 —— PRD 5.3 / docs/03 4.6~4.7 · docs/13 4.1（两级树）

★ 有库存余额（qty != 0）的仓库禁止删除或停用：否则库存无处可去（PRD 3.5 二次确认 + 409）。
★ 0002 起 warehouses 是两级树（仓库 level=1 / 库位 level=2）：
  - parent_id=0 表示顶级；非 0 时必须指向一个启用中的 level=1 仓库；
  - 最多两级（level 已达 2 的节点不能再挂下级）；
  - 有子库位的仓库禁止停用（否则子库位变成孤儿）；
  - 已建仓库禁止改挂 parent_id（改层级会让库存归属说不清，要改就新建）。
"""
from __future__ import annotations

import sqlite3

from core.errors import BizError
from models import database, warehouse_repo
from services import validators


def list_warehouses(include_inactive: bool = False) -> dict:
    conn = database.get_connection()
    items = warehouse_repo.list_all(conn, include_inactive=include_inactive)
    return {"list": items, "total": len(items)}


def create(fields: dict) -> dict:
    name = validators.require_str(fields.get("name"), "name", max_len=30)
    code = validators.require_str(fields.get("code"), "code", max_len=32, required=False)
    category = validators.require_str(fields.get("category"), "category", max_len=32, required=False)
    address = validators.require_str(fields.get("address"), "address", max_len=100, required=False)
    parent_id = validators.require_int(fields.get("parent_id"), "parent_id", minimum=0)
    conn = database.get_connection()
    parent = _resolve_parent(conn, parent_id)
    if warehouse_repo.find_by_name(conn, name):
        raise BizError(1010, "仓库名称已存在", {"conflict": warehouse_repo.find_by_name(conn, name)})
    if code and warehouse_repo.find_by_code(conn, code):
        raise BizError(1010, "仓库编码已存在", {"conflict": warehouse_repo.find_by_code(conn, code)})
    try:
        return warehouse_repo.create(conn, name, address, code=code, parent_id=parent, category=category)
    except sqlite3.IntegrityError:
        raise BizError(1010, "仓库名称或编码已存在", {"conflict": None})


def update(warehouse_id: int, fields: dict) -> dict:
    conn = database.get_connection()
    current = warehouse_repo.get(conn, warehouse_id)
    if current is None:
        raise BizError(1006, "仓库不存在")
    clean: dict = {}
    if "name" in fields:
        clean["name"] = validators.require_str(fields["name"], "name", max_len=30)
    if "address" in fields:
        clean["address"] = validators.require_str(fields["address"], "address", max_len=100, required=False)
    if "code" in fields:
        new_code = validators.require_str(fields["code"], "code", max_len=32, required=False)
        if new_code and new_code != current.get("code") and warehouse_repo.find_by_code(conn, new_code):
            raise BizError(1010, "仓库编码已存在", {"conflict": warehouse_repo.find_by_code(conn, new_code)})
        clean["code"] = new_code
    if "category" in fields:
        clean["category"] = validators.require_str(fields["category"], "category", max_len=32, required=False)
    if "parent_id" in fields:
        wanted = validators.require_int(fields["parent_id"], "parent_id", minimum=0)
        if wanted != int(current["parent_id"]):
            raise BizError(1017, "已存在的仓库不能改挂上级（要调整层级请新建）",
                           {"warehouse_id": warehouse_id, "current_parent_id": current["parent_id"]})
    if "sort_order" in fields:
        clean["sort_order"] = validators.require_int(fields["sort_order"], "sort_order")
    if "is_active" in fields:
        flag = fields["is_active"]
        flag = 1 if (flag is True or str(flag).lower() in ("1", "true")) else 0
        if flag == 0:
            if warehouse_repo.has_stock(conn, warehouse_id):
                raise BizError(1010, "该仓库仍有库存，不能停用",
                               {"breakdown": warehouse_repo.stock_breakdown(conn, warehouse_id)[:20]})
            if warehouse_repo.has_children(conn, warehouse_id):
                raise BizError(1017, "该仓库下还有库位，请先停用或移走全部库位",
                               {"warehouse_id": warehouse_id})
        clean["is_active"] = flag
    warehouse_repo.update(conn, warehouse_id, clean)
    return warehouse_repo.get(conn, warehouse_id)


def _resolve_parent(conn, parent_id: int) -> int:
    """父级校验：0=顶级；否则必须是启用中的 level=1 仓库（两级树封顶）。"""
    pid = int(parent_id or 0)
    if pid == 0:
        return 0
    parent = warehouse_repo.get(conn, pid)
    if parent is None:
        raise BizError(1017, "上级仓库不存在", {"parent_id": pid})
    if int(parent["level"]) != 1:
        raise BizError(1017, "只支持两级仓库，库位下不能再挂下级", {"parent_id": pid})
    if not parent["is_active"]:
        raise BizError(1017, "上级仓库已停用，不能在其下新建库位", {"parent_id": pid})
    return pid


def assert_usable(conn, warehouse_id: int) -> dict:
    """出入库前置校验：存在且启用（否则 1008，PRD 5.4）。"""
    row = warehouse_repo.get(conn, warehouse_id)
    if row is None:
        raise BizError(1006, "仓库不存在", {"warehouse_id": warehouse_id})
    if not row["is_active"]:
        raise BizError(1008, "仓库已停用，请换仓库", {"warehouse_id": warehouse_id, "name": row["name"]})
    return row
