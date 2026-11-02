$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
    $pyArgs = @("-3")
} else {
    $python = "python"
    $pyArgs = @()
}

& $python @pyArgs -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "需要 Python 3.11 或更高版本。请从 python.org 安装，并勾选 tcl/tk 与 pip。"
    exit 1
}

& $python @pyArgs -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python @pyArgs -m PyInstaller --noconfirm --clean image_workbench.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "已生成: dist\图片工坊\图片工坊.exe"
Write-Host "请保留整个 dist\图片工坊 文件夹，不要只拷贝 exe。"
