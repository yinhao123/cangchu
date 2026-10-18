"""单据号生成 —— docs/13 第 4.2 / 4.3 节

规则：`PREFIX + yyyymmdd + 4 位流水`（例 `PO202610170001`），按天重置。
★ 必须在**事务内 SQL 现算**（与商品内码同一套路）：靠 `MAX(code)` 现算 + 表级 UNIQUE 兜底，
  不另设计数表 —— 计数表会与单据表产生第二处真相，断电/回滚后必然对不上。
★ 调用方必须先开事务，否则并发下会算出同号（SQLite 单写者 + UNIQUE 只会让后者报 1010，
  虽不产生脏数据，但用户会看到「保存失败」）。
"""
from __future__ import annotations

import uuid

from services import timeutil

CODE_TABLES = {
    "PO": "wms_purchase_order",              # 采购订单
    "PI": "wms_purchase_in_warehousing",     # 采购入库单
    "GO": "wms_goods_operation_order",       # 领用/退库/归还/借用/调拨单（P4）
    "ST": "wms_stocktake",                   # 盘点单（P0-2）
    "SO": "wms_sales_order",                 # 销货单（P1-4）
    "SR": "wms_sales_return",                # 销售退货单（P1-4）
}


def next_code(conn, prefix: str, at: str | None = None) -> str:
    """返回下一个单据号；prefix 必须在 CODE_TABLES 白名单内（防注入）。"""
    prefix = str(prefix or "").upper()
    table = CODE_TABLES.get(prefix)
    if table is None:
        raise ValueError(f"未知的单据前缀：{prefix}")
    day = str(at or timeutil.now_iso())[:10].replace("-", "")
    row = conn.execute(f"SELECT MAX(code) FROM {table} WHERE code LIKE ?",
                       (f"{prefix}{day}%",)).fetchone()
    seq = 1 if row[0] is None else int(str(row[0])[-4:]) + 1
    return f"{prefix}{day}{seq:04d}"


def bill_msg_id(bill_code: str, detail_id: int, suffix: str = "") -> str:
    """单据过账的幂等键：由「单据号 + 明细行 + 用途后缀」确定性派生，且必须是 UUID v4。

    ★ 不能每次现生成随机 UUID —— 那样审批接口被重复点击就失去幂等保护（T-09）；
    ★ 也不能用 UUID v5 —— validators 只放行 v1/v4。故用 v5 的位做种子，再造一个 v4。
    ★ 调拨单一行要产出两条流水（源仓 OUT / 目标仓 IN），靠 suffix 区分。
    """
    seed = uuid.uuid5(uuid.NAMESPACE_URL,
                      f"scanable/bill/{bill_code}/{detail_id}{suffix}").int
    return str(uuid.UUID(int=seed, version=4))
