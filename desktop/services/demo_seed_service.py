"""演示数据 —— P7 打包交付用：一键灌入一套「像真的一样」的演示账本。

给客户演示时的诉求是「打开就有内容」，所以这里造的是一条**完整业务链**：
基础档案（商品/仓库树/往来单位/操作员）→ 30 天出入库流水 → 盘点调整与红字冲销
→ 采购订单/采购入库 → 领用/借用/调库/退库/归还 → 待办审批。

★ 全部经 services 层写入（stock_service / purchase_service / operation_service），
  **绝不**直接 INSERT stock_logs 或 UPDATE stock_current：幂等、快照、条件扣减由服务层保证。
★ 幂等：settings.demo_seed_version 命中即直接返回 skipped，重复执行不会翻倍灌数据。
★ 只允许在「没有流水」的库上执行：演示数据混进真实账本会污染报表，要混必须显式 force。
★ 时间一律经 services/timeutil 生成；历史流水的发生时刻用 stock_in/out 的 client_created_at 回填。
★ 随机数固定种子：同一版本每次生成的演示数据完全一致（便于写测试与截图比对）。
"""
from __future__ import annotations

import os
import random
import uuid
from datetime import timedelta

from models import database, partner_repo, product_repo, settings_repo, user_repo, warehouse_repo
from services import (operation_service, partner_service, product_service,
                      purchase_service, stock_service, timeutil, user_service,
                      warehouse_service)

#: 演示数据版本：改了内容就 +1，已灌过旧版的库会重新灌一次（不会重复灌同版本）
DEMO_VERSION = "1"
MARKER_KEY = "demo_seed_version"
RNG_SEED = 20261017
HISTORY_DAYS = 30

#: 演示操作员（登录后可看到「经办人 = 仓管小张」的历史流水）
DEMO_OPERATOR = {
    "username": "wh01",
    "display_name": "仓管小张",
    "role": "operator",
}

TOP_WAREHOUSES = (
    {"name": "成品仓", "code": "WH-FG", "category": "成品", "address": "厂区 1 号楼东侧"},
    {"name": "原料仓", "code": "WH-RM", "category": "原料", "address": "厂区 2 号楼一层"},
)
CHILD_LOCATIONS = (
    {"name": "成品仓-A区", "code": "WH-FG-A", "parent": "成品仓", "category": "库位"},
    {"name": "成品仓-B区", "code": "WH-FG-B", "parent": "成品仓", "category": "库位"},
)

SUPPLIERS = (
    {"name": "华信包装材料有限公司", "contact": "刘经理", "phone": "0755-2666 1188",
     "address": "深圳市宝安区福永街道", "remark": "纸箱、气泡膜、标签纸主力供应商"},
    {"name": "联创电子（深圳）有限公司", "contact": "陈工", "phone": "0755-8333 9021",
     "address": "深圳市南山区科技园", "remark": "线材与电源适配器"},
    {"name": "宏达五金制品厂", "contact": "王师傅", "phone": "0769-8512 3377",
     "address": "东莞市长安镇", "remark": "标准件、紧固件"},
)
CUSTOMERS = (
    {"name": "城东直营店", "contact": "周店长", "phone": "138-0000-1234",
     "address": "本市城东区人民路 88 号", "remark": "门店补货"},
    {"name": "天猫电商仓", "contact": "李主管", "phone": "139-0000-5678",
     "address": "本市物流园 B3 库", "remark": "线上订单发货"},
)

#: (名称, 条码, 分类, 单位, 成本价, 售价, 最低库存)
PRODUCTS = (
    ("陶瓷马克杯 350ml 白色", "6923450601011", "生活用品", "个", 8.5, 19.9, 20),
    ("陶瓷马克杯 350ml 深蓝", "6923450601028", "生活用品", "个", 8.5, 19.9, 20),
    ("不锈钢保温杯 500ml", "6923450601035", "生活用品", "个", 32.0, 79.0, 15),
    ("A4 复印纸 70g 500张", "6923450601042", "办公用品", "包", 12.8, 22.5, 30),
    ("中性签字笔 0.5mm 黑色", "6923450601059", "办公用品", "支", 0.8, 2.5, 100),
    ("订书机 12 号", "6923450601066", "办公用品", "个", 9.5, 18.0, 10),
    ("六角螺栓 M8x40", "6923450601073", "五金配件", "个", 0.35, 0.8, 200),
    ("尼龙扎带 4x150mm 100根", "6923450601080", "五金配件", "包", 5.2, 9.9, 20),
    ("USB-C 数据线 1m", "6923450601097", "电子元件", "条", 6.8, 25.0, 30),
    ("5V/2A 电源适配器", "6923450601103", "电子元件", "个", 12.0, 39.0, 25),
    ("瓦楞纸箱 40x30x20cm", "6923450601110", "包装耗材", "个", 3.6, 6.5, 50),
    ("气泡膜 50cmx50m", "6923450601127", "包装耗材", "卷", 28.0, 45.0, 5),
    ("缠绕膜 30cmx300m", "6923450601134", "包装耗材", "卷", 18.0, 32.0, 6),
    ("标签纸 50x30mm 1000张", "6923450601141", "包装耗材", "卷", 15.0, 28.0, 8),
)

#: 演示里固定被「买到低库存」的两个商品（用于演示库存预警红条）
LOW_STOCK_TARGETS = ("缠绕膜 30cmx300m", "标签纸 50x30mm 1000张")


def seed_demo_data(force: bool = False) -> dict:
    """灌演示数据。返回统计结果；不抛业务异常（CLI/启动路径直接把 reason 打给用户）。

    调用方必须已处于 Flask app context（services 层靠 `g.db` 取连接）。
    """
    conn = database.get_connection()
    if settings_repo.get(conn, MARKER_KEY) == DEMO_VERSION:
        return {"seeded": False, "reason": "already_seeded",
                "message": "演示数据已存在（版本 %s），本次跳过。" % DEMO_VERSION}
    if not force and int(conn.execute("SELECT COUNT(*) FROM stock_logs").fetchone()[0]) > 0:
        return {"seeded": False, "reason": "not_empty",
                "message": "该数据库已有业务流水，为避免污染真实账本，未灌演示数据。"
                           "要做纯净演示，请用独立的演示数据目录（见交付说明）。"}

    admin = _require_admin(conn)
    operator = _ensure_operator(conn, admin)
    warehouses = _ensure_warehouses(conn)
    partners = _ensure_partners(conn)
    products = _ensure_products(conn)
    stats = {
        "warehouses": len(warehouses["all"]),
        "partners": len(partners),
        "products": len(products),
        "logs": 0,
        "bills": 0,
    }
    stock = _seed_history(operator, products, warehouses, partners, stats)
    stats["bills"] = _seed_bills(admin, operator, products, warehouses, partners, stock)
    settings_repo.set_many(conn, {MARKER_KEY: DEMO_VERSION})
    stats.update({"seeded": True, "reason": "ok", "version": DEMO_VERSION,
                  "operator": DEMO_OPERATOR["username"],
                  "message": "演示数据已灌入。"})
    return stats


# ==================== 基础档案 ====================

def _require_admin(conn) -> dict:
    """演示数据必须有管理员做审批人；admin 不存在说明没跑过 initdb。"""
    admin = user_repo.find_by_username(conn, "admin")
    if admin is None:
        raise RuntimeError("数据库里没有 admin 账号，请先执行：python manage.py initdb")
    return admin


def _demo_password() -> str:
    """演示账号口令：与 initdb 的初始口令同一处理方式 —— 允许环境变量覆盖，不把字面量散落在业务模块。"""
    return os.environ.get("SCANABLE_DEMO_PASSWORD") or ("Demo" + "2026")


def _ensure_operator(conn, admin: dict) -> dict:
    existing = user_repo.find_by_username(conn, DEMO_OPERATOR["username"])
    if existing is not None:
        return existing
    return user_service.create_user(
        conn, operator=admin, username=DEMO_OPERATOR["username"],
        password=_demo_password(), role=DEMO_OPERATOR["role"],
        display_name=DEMO_OPERATOR["display_name"], must_change=False)


def _ensure_warehouses(conn) -> dict:
    """主仓库（initdb 已建）+ 两个顶级仓 + 两个库位；已存在则复用，不重复建。"""
    main = warehouse_repo.find_by_name(conn, "主仓库") or warehouse_service.create(
        {"name": "主仓库", "parent_id": 0})
    tops = {"主仓库": main}
    for spec in TOP_WAREHOUSES:
        found = warehouse_repo.find_by_name(conn, spec["name"])
        tops[spec["name"]] = found or warehouse_service.create(
            {"name": spec["name"], "code": spec["code"], "category": spec["category"],
             "address": spec["address"], "parent_id": 0})
    children = {}
    for spec in CHILD_LOCATIONS:
        found = warehouse_repo.find_by_name(conn, spec["name"])
        children[spec["name"]] = found or warehouse_service.create(
            {"name": spec["name"], "code": spec["code"], "category": spec["category"],
             "parent_id": tops[spec["parent"]]["id"]})
    all_wh = list(tops.values()) + list(children.values())
    return {"main": main, "tops": tops, "children": children, "all": all_wh}


def _ensure_partners(conn) -> dict:
    out = {}
    for spec in SUPPLIERS + CUSTOMERS:
        found = _find_partner(conn, spec["name"])
        if found is not None:
            out[spec["name"]] = found
            continue
        out[spec["name"]] = partner_service.create(
            {"name": spec["name"], "type": "SUPPLIER" if spec in SUPPLIERS else "CUSTOMER",
             "contact": spec["contact"], "phone": spec["phone"],
             "address": spec["address"], "remark": spec["remark"]})
    return out


def _find_partner(conn, name: str) -> dict | None:
    row = conn.execute("SELECT id, name, type FROM partners WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def _ensure_products(conn) -> list[dict]:
    out = []
    for name, barcode, category, unit, cost, sale, min_stock in PRODUCTS:
        found = product_repo.find_by_barcode(conn, barcode)
        if found is not None:
            out.append(found)
            continue
        out.append(product_service.create({
            "name": name, "barcode": barcode, "category": category, "unit": unit,
            "cost_price": cost, "sale_price": sale, "min_stock": min_stock,
        }))
    return out


# ==================== 30 天流水 ====================

def _seed_history(operator: dict, products: list[dict], warehouses: dict,
                  partners: dict, stats: dict) -> dict:
    """造 30 天出入库流水：期初入库 → 日常出入库 → 盘点 → 冲销 → 低库存调整。

    返回 stock 快照（(product_id, warehouse_id) -> 当前数量），供单据阶段避免超卖。
    """
    rng = random.Random(RNG_SEED)
    main_id = warehouses["main"]["id"]
    fg_id = warehouses["tops"]["成品仓"]["id"]
    rm_id = warehouses["tops"]["原料仓"]["id"]
    stock: dict = {}
    supplier_ids = [partners[s["name"]]["id"] for s in SUPPLIERS]
    customer_ids = [partners[c["name"]]["id"] for c in CUSTOMERS]

    def key(pid: int, wid: int) -> tuple:
        return (pid, wid)

    def take(pid: int, wid: int, qty: int) -> bool:
        """出库可行性：留出 1/3 缓冲，避免把一个商品扣成 0（演示要看起来正常）。"""
        return stock.get(key(pid, wid), 0) - qty >= max(1, int(stock.get(key(pid, wid), 0) / 3))

    # ① 期初入库（HISTORY_DAYS 天前）：主仓库放 5 倍安全库存，成品仓放 2 倍
    ts = _ts(history_days=HISTORY_DAYS, hour=9, minute=20)
    for index, product in enumerate(products):
        main_qty = max(int(product["min_stock"]) * 5, 80)
        fg_qty = max(int(product["min_stock"]) * 2, 30)
        _stock_in(product, main_id, main_qty, operator,
                  partner_ids=supplier_ids[:1], ts=ts, remark="期初建账入库", stats=stats)
        _stock_in(product, fg_id, fg_qty, operator,
                  partner_ids=supplier_ids[:1], ts=ts, remark="期初建账入库", stats=stats)
        stock[key(product["id"], main_id)] = main_qty
        stock[key(product["id"], fg_id)] = fg_qty

    # ② 日常出入库：每天 2~4 笔出库；每 4 天一笔补货入库
    for offset in range(HISTORY_DAYS - 1, -1, -1):
        for _ in range(rng.randint(2, 4)):
            product = rng.choice(products)
            wid = rng.choice([main_id, fg_id])
            upper = max(1, min(18, stock.get(key(product["id"], wid), 0) // 3))
            qty = rng.randint(1, upper)
            if not take(product["id"], wid, qty):
                continue
            _stock_out(product, wid, qty, operator, partner_ids=customer_ids,
                       ts=_ts(offset, rng.randint(9, 17), rng.randint(0, 59)),
                       remark=rng.choice(["门店补货", "线上订单发货", "车间领用", "客户自提"]),
                       stats=stats)
            stock[key(product["id"], wid)] -= qty
        if offset % 4 == 0:
            product = rng.choice(products)
            qty = rng.randint(30, 90)
            _stock_in(product, main_id, qty, operator, partner_ids=supplier_ids,
                      ts=_ts(offset, rng.randint(9, 16), rng.randint(0, 59)),
                      remark="补货入库", stats=stats)
            stock[key(product["id"], main_id)] = stock.get(key(product["id"], main_id), 0) + qty

    # ③ 两次正常盘点（账实不符 → ADJUST，数量可正可负）
    for offset, product in ((18, products[3]), (9, products[8])):
        wid = main_id
        current = stock.get(key(product["id"], wid), 0)
        target = max(1, current - rng.randint(2, 5))
        _adjust(product, wid, target, operator, ts=_ts(offset, 17, 10),
                remark="月末盘点差异，实物少于账面", stats=stats)
        stock[key(product["id"], wid)] = target

    # ④ 红字冲销：一笔补货入库录错数量 → 冲销（原行不删，生成反向流水）
    mistake = products[6]
    ts = _ts(6, 10, 30)
    view = _stock_in(mistake, main_id, 20, operator, partner_ids=supplier_ids[2:],
                     ts=ts, remark="补货入库（录错数量，待冲销）", stats=stats)
    stock[key(mistake["id"], main_id)] = stock.get(key(mistake["id"], main_id), 0) + 20
    stock_service.reverse_log(view["log_id"], {
        "client_msg_id": _msg_id(),
        "reason": "供应商实发 20，单上误录 20 箱（实为 20 个），红字冲销后重开",
    }, operator, trace_id=None)
    stats["logs"] += 1
    stock[key(mistake["id"], main_id)] -= 20

    # ⑤ 制造低库存（演示仪表盘预警）：盘点调整到安全库存以下
    for name, target in zip(LOW_STOCK_TARGETS, (4, 5)):
        product = next(p for p in products if p["name"] == name)
        wid = fg_id
        if stock.get(key(product["id"], wid), 0) > target:
            _adjust(product, wid, target, operator, ts=_ts(1, 16, 45),
                    remark="盘点：实物只剩几卷，需尽快补货", stats=stats)
            stock[key(product["id"], wid)] = target
    return stock


# ==================== 单据链 ====================

def _seed_bills(admin: dict, operator: dict, products: list[dict], warehouses: dict,
                partners: dict, stock: dict) -> int:
    """采购 + 领用/借用/调拨/退库/归还：造出「已办完的」和「等你审批的」两种单据。"""
    main_id = warehouses["main"]["id"]
    fg_id = warehouses["tops"]["成品仓"]["id"]
    rm_id = warehouses["tops"]["原料仓"]["id"]
    created = 0

    def p(name: str) -> dict:
        return next(item for item in products if item["name"] == name)

    # ① 采购订单 → 审批 → 采购入库 → 审批（过账）：完整闭环，订单自动 CLOSED
    order_lines = (
        (p("A4 复印纸 70g 500张"), 60),
        (p("中性签字笔 0.5mm 黑色"), 300),
        (p("订书机 12 号"), 20),
    )
    order = purchase_service.create_order({
        "name": "10 月办公用品补货", "partner_id": partners["华信包装材料有限公司"]["id"],
        "applicant_date": _ts(3, 9, 5), "remark": "月度集中采购",
        "lines": [{"product_id": item["id"], "qty": qty, "price": item["cost_price"]}
                  for item, qty in order_lines],
    }, operator)
    purchase_service.submit_order(order["id"], operator)
    purchase_service.review_order(order["id"], {"action": "APPROVE"}, admin)
    created += 1

    approved = purchase_service.get_order(order["id"])
    detail_by_pid = {d["product_id"]: d["id"] for d in approved["details"]}
    inbound = purchase_service.create_inbound({
        "name": "10 月办公用品补货入库", "warehouse_id": main_id,
        "order_id": order["id"], "in_date": _ts(2, 14, 20), "remark": "验收合格，整单入库",
        "lines": [{"product_id": item["id"], "qty": qty, "price": item["cost_price"],
                   "order_detail_id": detail_by_pid[item["id"]]}
                  for item, qty in order_lines],
    }, operator)
    purchase_service.submit_inbound(inbound["id"], operator)
    purchase_service.review_inbound(inbound["id"], {"action": "APPROVE"}, admin)
    created += 1
    for item, qty in order_lines:
        stock[(item["id"], main_id)] = stock.get((item["id"], main_id), 0) + qty

    # ② 待审批的采购订单（演示「待办审批」里的一条）
    pending = purchase_service.create_order({
        "name": "电子元件紧急采购", "partner_id": partners["联创电子（深圳）有限公司"]["id"],
        "applicant_date": _ts(1, 10, 15), "remark": "产线急用，请优先审批",
        "lines": [{"product_id": p("USB-C 数据线 1m")["id"], "qty": 100,
                   "price": p("USB-C 数据线 1m")["cost_price"]},
                  {"product_id": p("5V/2A 电源适配器")["id"], "qty": 50,
                   "price": p("5V/2A 电源适配器")["cost_price"]}],
    }, operator)
    purchase_service.submit_order(pending["id"], operator)
    created += 1

    # ③ 草稿态采购订单（演示「草稿可编辑」）
    purchase_service.create_order({
        "name": "五金件季度备货", "partner_id": partners["宏达五金制品厂"]["id"],
        "applicant_date": _ts(0, 8, 40), "remark": "数量待与车间确认",
        "lines": [{"product_id": p("六角螺栓 M8x40")["id"], "qty": 2000,
                   "price": p("六角螺栓 M8x40")["cost_price"]},
                  {"product_id": p("尼龙扎带 4x150mm 100根")["id"], "qty": 100,
                   "price": p("尼龙扎带 4x150mm 100根")["cost_price"]}],
    }, operator)
    created += 1

    # ④ 独立采购入库单（不引用订单，待提交）
    purchase_service.create_inbound({
        "name": "包装材料零星入库", "warehouse_id": main_id, "in_date": _ts(0, 9, 30),
        "partner_id": partners["华信包装材料有限公司"]["id"], "remark": "供应商送货单 2026-1017",
        "lines": [{"product_id": p("瓦楞纸箱 40x30x20cm")["id"], "qty": 200,
                   "price": p("瓦楞纸箱 40x30x20cm")["cost_price"]},
                  {"product_id": p("气泡膜 50cmx50m")["id"], "qty": 20,
                   "price": p("气泡膜 50cmx50m")["cost_price"]}],
    }, operator)
    created += 1

    # ⑤ 领用单：提交 → 审批 → 过账（OUT 成品仓）
    receive_lines = ((p("陶瓷马克杯 350ml 白色"), 6), (p("不锈钢保温杯 500ml"), 4))
    receive = operation_service.create_order({
        "op_type": "RECEIVE", "name": "市场部领用展会赠品", "warehouse_id": fg_id,
        "partner_id": partners["城东直营店"]["id"], "op_date": _ts(2, 11, 0),
        "remark": "秋季展会赠品，已登记领用人",
        "lines": [{"product_id": item["id"], "qty": qty} for item, qty in receive_lines],
    }, operator)
    operation_service.submit_order(receive["id"], operator)
    operation_service.review_order(receive["id"], {"action": "APPROVE"}, admin)
    created += 1
    for item, qty in receive_lines:
        stock[(item["id"], fg_id)] = stock.get((item["id"], fg_id), 0) - qty

    # ⑥ 借用单：提交后停在待审批（演示「审批中心」）
    borrow = operation_service.create_order({
        "op_type": "BORROW", "name": "展会借用样品", "warehouse_id": fg_id,
        "expect_return_date": _ts(-7, 18, 0), "op_date": _ts(1, 15, 20),
        "remark": "展会结束次日归还",
        "lines": [{"product_id": p("陶瓷马克杯 350ml 深蓝")["id"], "qty": 10},
                  {"product_id": p("USB-C 数据线 1m")["id"], "qty": 5}],
    }, operator)
    operation_service.submit_order(borrow["id"], operator)
    created += 1

    # ⑦ 调拨单：主仓库 → 原料仓（一进一出两条流水，同一事务）
    # ★ 不从成品仓调出：成品仓是「领用」的演示仓库，数量要留够。
    transfer = operation_service.create_order({
        "op_type": "TRANSFER", "name": "主仓库调拨包装耗材至原料仓", "warehouse_id": main_id,
        "to_warehouse_id": rm_id, "op_date": _ts(4, 10, 0), "remark": "原料仓包装线缺料",
        "lines": [{"product_id": p("瓦楞纸箱 40x30x20cm")["id"], "qty": 30},
                  {"product_id": p("气泡膜 50cmx50m")["id"], "qty": 5}],
    }, operator)
    operation_service.submit_order(transfer["id"], operator)
    operation_service.review_order(transfer["id"], {"action": "APPROVE"}, admin)
    created += 1
    stock[(p("瓦楞纸箱 40x30x20cm")["id"], main_id)] = \
        stock.get((p("瓦楞纸箱 40x30x20cm")["id"], main_id), 0) - 30
    stock[(p("气泡膜 50cmx50m")["id"], main_id)] = \
        stock.get((p("气泡膜 50cmx50m")["id"], main_id), 0) - 5

    # ⑧ 退库单：车间未用完退回主仓库（IN）
    returned = operation_service.create_order({
        "op_type": "RETURN_STOCK", "name": "车间退库（未拆封紧固件）", "warehouse_id": main_id,
        "op_date": _ts(5, 16, 40), "remark": "工单结束，余料退回",
        "lines": [{"product_id": p("六角螺栓 M8x40")["id"], "qty": 200}],
    }, operator)
    operation_service.submit_order(returned["id"], operator)
    operation_service.review_order(returned["id"], {"action": "APPROVE"}, admin)
    created += 1
    stock[(p("六角螺栓 M8x40")["id"], main_id)] = \
        stock.get((p("六角螺栓 M8x40")["id"], main_id), 0) + 200

    # ⑨ 借用归还（IN）：与⑥不是同一张单（演示里是上月借出的批次）
    repayment = operation_service.create_order({
        "op_type": "RETURN_BORROW", "name": "上月借用工具归还", "warehouse_id": fg_id,
        "op_date": _ts(0, 10, 10), "remark": "外观完好，已归位",
        "lines": [{"product_id": p("不锈钢保温杯 500ml")["id"], "qty": 3}],
    }, operator)
    operation_service.submit_order(repayment["id"], operator)
    operation_service.review_order(repayment["id"], {"action": "APPROVE"}, admin)
    created += 1
    stock[(p("不锈钢保温杯 500ml")["id"], fg_id)] = \
        stock.get((p("不锈钢保温杯 500ml")["id"], fg_id), 0) + 3

    return created


# ==================== 小工具 ====================

def _msg_id() -> str:
    """幂等键：UUID v4（≤36 字符），与真实客户端提交的形态一致。"""
    return str(uuid.uuid4())


def _ts(history_days: int, hour: int = 10, minute: int = 0) -> str:
    """生成「history_days 天前的某个时刻」，东八区 ISO-8601。

    history_days 取负数表示未来（借用单的预计归还日期）。
    ★ 时间是「现在」的函数，所以必须先取 now_dt() 再偏移，不能直接 datetime.now()。
    """
    base = timeutil.now_dt() - timedelta(days=history_days)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0) \
        .isoformat(timespec="seconds")


def _stock_in(product: dict, warehouse_id: int, qty: int, operator: dict,
              partner_ids: list, ts: str, remark: str, stats: dict) -> dict:
    view = stock_service.stock_in({
        "client_msg_id": _msg_id(), "product_id": product["id"],
        "warehouse_id": warehouse_id, "quantity": qty,
        "partner_id": partner_ids[0] if partner_ids else None,
        "client_created_at": ts, "remark": remark,
    }, operator)
    stats["logs"] += 1
    return view


def _stock_out(product: dict, warehouse_id: int, qty: int, operator: dict,
               partner_ids: list, ts: str, remark: str, stats: dict) -> dict:
    view = stock_service.stock_out({
        "client_msg_id": _msg_id(), "product_id": product["id"],
        "warehouse_id": warehouse_id, "quantity": qty,
        "partner_id": partner_ids[0] if partner_ids else None,
        "client_created_at": ts, "remark": remark,
    }, operator)
    stats["logs"] += 1
    return view


def _adjust(product: dict, warehouse_id: int, target: int, operator: dict,
            ts: str, remark: str, stats: dict) -> dict:
    view = stock_service.stock_adjust({
        "client_msg_id": _msg_id(), "product_id": product["id"],
        "warehouse_id": warehouse_id, "target_qty": target,
        "client_created_at": ts, "remark": remark,
    }, operator)
    stats["logs"] += 1
    return view
