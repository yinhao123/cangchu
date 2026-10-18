@echo off
rem 崩溃后 5 秒重启循环 —— PRD 15.5（自用项目不做 Windows 服务化，复杂度过高）
rem 配合法：任务计划程序 → 登录时触发 → 运行本 bat。步骤见 docs/使用说明.md
set APP_DIR=%LOCALAPPDATA%\ScanableSelf
:loop
ScanableSelf.exe >> "%APP_DIR%\logs\restart.log" 2>&1
echo [%date% %time%] 进程退出，5 秒后重启 >> "%APP_DIR%\logs\restart.log"
timeout /t 5 /nobreak >nul
goto loop
