"""UT-14 时间口径 —— 陷阱 T-11（SQLite CURRENT_TIMESTAMP 存 UTC，与 Python 本地时间混用会统计错乱）
覆盖：now_iso() 格式带 +08:00；23:59:59 与次日 00:00:00 各一笔 →「今日入库」只计后者；
day_range 跨度 >366 天抛 BizError(1002)。
"""
