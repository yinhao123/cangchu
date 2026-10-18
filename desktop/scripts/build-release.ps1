# 打包发布脚本 —— P7 交付（Win7 线：Python 3.8.10 + PyInstaller 5.13.2）
#
# 用法（在 desktop 目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1
#
# 前置条件：
#   1) 已安装 Python 3.8.10（默认取 %LOCALAPPDATA%\Programs\Python\Python38\python.exe）
#   2) 该解释器已装 requirements.txt + pyinstaller==5.13.2
#   3) static\spa 是最新的前端构建产物（改过前端必须先 npm run build）
param(
    [string]$Python = "$env:LOCALAPPDATA\Programs\Python\Python38\python.exe",
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

if (-not (Test-Path $Python)) {
    throw "找不到 Python 3.8.10：$Python（Win7 线必须用 3.8.10，见 docs/12）"
}
$version = & $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($version -ne "3.8.10") {
    throw "当前解释器是 $version，Win7 交付线要求 3.8.10（PyInstaller 5.13.2 与 Flask 3.0.3 的锁定依据）"
}

# 1) 门禁：全量测试（3.8 下跑，顺带验证 PEP 585/604 等 3.8 兼容性，T-38）
if (-not $SkipTests) {
    Write-Host "[1/4] pytest -q（Python $version）"
    $env:PYTHONUTF8 = "1"
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "测试未全绿，停止打包" }
}

# 2) 前端产物自检：缺 static\spa\index.html = 交付后白屏（T-36）
Write-Host "[2/4] 检查前端构建产物"
if (-not (Test-Path "static\spa\index.html")) {
    throw "static\spa\index.html 不存在：先在 desktop\web 执行 npm run build（产物必须随包提交）"
}

# 3) 打包（onedir，产物无 _internal 子目录）
Write-Host "[3/4] PyInstaller"
& $Python -m PyInstaller --noconfirm --clean ScanableSelf.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

# 4) 交付目录整理：演示脚本 + 版本说明
Write-Host "[4/4] 整理 dist"
$dist = Join-Path $root "dist\ScanableSelf"
Copy-Item "scripts\demo-mode.bat" (Join-Path $dist "启动演示模式.bat") -Force
Copy-Item "scripts\start-on-boot.bat" (Join-Path $dist "start-on-boot.bat") -Force
if (Test-Path "..\docs\使用说明.md") {
    Copy-Item "..\docs\使用说明.md" (Join-Path $dist "使用说明.md") -Force
}

# 关键文件自检：漏一个就是现场事故
$must = @(
    "ScanableSelf.exe",
    "ScanableSelf-debug.exe",
    "static\spa\index.html",
    "templates\errors\500.html",
    "db\schema.sql",
    "keys\public.pem",
    "barcode\fonts\DejaVuSansMono.ttf"
)
$missing = $must | Where-Object { -not (Test-Path (Join-Path $dist $_)) }
if ($missing) { throw ("产物缺少关键文件：" + ($missing -join ", ")) }

$size = [math]::Round(((Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
$zip = Join-Path $root ("dist\ScanableSelf-1.0.0-win7-x64.zip")
Compress-Archive -Path (Join-Path $dist "*") -DestinationPath $zip -Force

Write-Host ""
Write-Host "完成：$dist（$size MB）"
Write-Host "压缩包：$zip"
