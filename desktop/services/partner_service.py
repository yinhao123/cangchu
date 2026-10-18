"""客户/供应商业务规则 —— docs/03 4.8"""
from __future__ import annotations

import sqlite3

from core.errors import BizError
from models import database, partner_repo
from services import validators

TYPES = ("CUSTOMER", "SUPPLIER")


def list_partners(partner_type=None) -> dict:
    """按类型列出往来单位（docs/03 4.8）。

    ★ `type` 是**可选**筛选：不传就返回全部启用记录。
      出入库页的「盘点调整」模式不需要区分客户/供应商，前端会把 type 传成 undefined；
      这里若强制必填，页面一切到调整模式就会弹「type 不能为空」——
      那是实现比契约更严导致的假故障，不是用户填错了什么。
    """
    conn = database.get_connection()
    if partner_type in (None, ""):
        items = partner_repo.list_active(conn)
        return {"list": items, "total": len(items), "type": None}
    ptype = validators.require_choice(partner_type, "type", TYPES)
    items = partner_repo.list_by_type(conn, ptype)
    return {"list": items, "total": len(items), "type": ptype}


def create(fields: dict) -> dict:
    clean = {
        "name": validators.require_str(fields.get("name"), "name", max_len=50),
        "type": validators.require_choice(fields.get("type"), "type", TYPES),
        "contact": validators.require_str(fields.get("contact"), "contact", max_len=30, required=False),
        "phone": validators.require_str(fields.get("phone"), "phone", max_len=30, required=False),
        "address": validators.require_str(fields.get("address"), "address", max_len=100, required=False),
        "remark": validators.require_str(fields.get("remark"), "remark", max_len=200, required=False),
    }
    conn = database.get_connection()
    conflict = partner_repo.find_by_name_type(conn, clean["name"], clean["type"])
    if conflict:
        raise BizError(1010, "同名同类型的往来单位已存在", {"conflict": conflict})
    try:
        return partner_repo.create(conn, clean)
    except sqlite3.IntegrityError:
        raise BizError(1010, "同名同类型的往来单位已存在", {"conflict": None})
