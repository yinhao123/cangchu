@echo off
rem ============================================================
rem Demo mode - safe for customer demos.
rem   1) uses a SEPARATE data folder (demo-data) so the real
rem      ledger is never touched
rem   2) seeds a full demo dataset on first run (idempotent)
rem   3) opens the browser 3 seconds later
rem Keep this file next to ScanableSelf.exe.
rem ============================================================
setlocal
set "APP_DIR=%~dp0"
set "SCANABLE_DATA_DIR=%APP_DIR%demo-data"
if not exist "%SCANABLE_DATA_DIR%" mkdir "%SCANABLE_DATA_DIR%"
echo Starting demo instance, data folder: %SCANABLE_DATA_DIR%
start "" "%APP_DIR%ScanableSelf.exe" --demo
timeout /t 3 /nobreak >nul
start "" http://localhost:5000/
endlocal
