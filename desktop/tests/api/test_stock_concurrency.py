"""UT-06 并发负库存回归 —— 陷阱 T-06（本项目最致命的实现错误）
8 线程对同一 (product, warehouse) 各提出库 2 件、库存仅 10 件 →
断言：成功笔数 × 2 ≤ 10；stock_current.qty ≥ 0；SUM(stock_logs) 与 qty 一致（不变式成立）。
★ 必须真起 threading.Thread 并发，不能用顺序循环冒充；这是原子扣减（条件更新 + rowcount）存在的证明。
"""
