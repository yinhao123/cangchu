"""日志脱敏 —— PRD 10.4 · CODEBUDDY.md 禁止事项 5
真跑一次登录（带 password）+ 一次 /api/login（带 Token），读取日志文件原文，
断言不含明文密码与 Token（core/logging_setup 的 key 黑名单过滤器负责，不依赖开发者自觉）。
"""
