---
description: "Day3 —— PyInstaller 正式包 + 干净机器实测 + 使用说明 + 上线检查清单"
argument-hint: "[可选：'只出包，文档我自己写']"
allowed-tools: Read, Write, Edit, Bash
---

承接 Day2。**本阶段目标：把代码变成用户敢双击的东西。**

## 任务

1. **正式打包**：`pyinstaller ScanableSelf.spec`（单文件、`console=False`）；确认 `datas` 覆盖 `templates`/`static`/`db/schema.sql`/`db/migrations`/barcode 字体配置，`hiddenimports` 覆盖 `waitress` 子模块；**同时产出一个 `console=True` 的调试版 exe** 供排障。
2. **首启体验**：首次运行自动创建 `%LOCALAPPDATA%\ScanableSelf\` + 建库 + 迁移 + 种子 `admin/admin123` + 生成并持久化 `SECRET_KEY`；自动打开浏览器到登录页；目录不可写 → 弹窗说明（禁止未捕获异常静默退出，T-16/T-17）。
3. **开机自启**：`scripts/start-on-boot.bat`（崩溃后 5 秒重启循环）+ 任务计划程序图文步骤写进使用说明。
4. **干净机器实测**（必做，本地环境通过不算通过）：在一台**未装 Python** 的 Windows 上依次验证：双击启动 ≤8s → 防火墙授权弹窗（**点「取消」时降级表现是否正确**：黄条 + 仍能手输 IP）→ 登录 → 强制改密 → 建商品 → 入库/出库 → 查流水 → 打印预览 → 备份 → 恢复 → 另一台电脑浏览器访问 `http://<IP>:5000` 正常。
5. **使用说明** `docs/使用说明.md`：面向仓管员（怎么扫码出入库、怎么连手机、库存对不上怎么办）+ 面向管理员（备份恢复、账号、端口/防火墙排障）。**禁止**假设读者会命令行；每步配图位说明。
6. **性能基线记录**（PRD 23）：实测并写入 `docs/07-运维手册.md`：`/api/ping`、`/api/products/by-barcode`、`/api/stock/in`、`/api/sync` 200 条的 P50/P95。
7. **上线检查清单**：逐条勾选 `docs/08-上线检查清单.md`，未过项列出来问我。

## 门禁

```
[1] pytest -q 全绿
[2] 干净 Windows 机器实测 10 项全部通过（贴每项结果）
[3] exe 体积 ≤60MB、冷启动到服务可用 ≤8s
[4] 备份→恢复演练通过
```

## 输出

交付物清单（exe 路径、调试版、bat、文档）、干净机器实测逐项结果、性能基线数字、未通过项与原因。

补充说明：$ARGUMENTS
