"""业务规则层：事务边界、库存扣减、权限与校验都在这一层。
禁止 render_template、禁止 sqlite3 直接调用（必须经 models/）。PRD 2.3 / CODEBUDDY.md 分层结构。
"""
