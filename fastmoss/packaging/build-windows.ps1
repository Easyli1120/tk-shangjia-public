$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not [Environment]::Is64BitOperatingSystem) { throw '需要 64 位 Windows' }
$env:PYTHONUTF8 = '1'
& py -3.12 -m venv .venv-build
if ($LASTEXITCODE) { throw '请在构建电脑安装 Python 3.12 x64（员工电脑不需要）' }
$python = Join-Path $PWD '.venv-build\Scripts\python.exe'
& $python -m pip install --disable-pip-version-check -r packaging/requirements-build.txt
if ($LASTEXITCODE) { throw '构建依赖安装失败' }
& $python -m unittest discover -s tests -q
if ($LASTEXITCODE) { throw '回归测试未通过，停止打包' }
& packaging/prepare-windows.ps1
& $python -m PyInstaller --noconfirm --clean packaging/TKShangjia.spec
if ($LASTEXITCODE) { throw '程序打包失败' }
Copy-Item build/vendor dist/TKShangjia/_internal/vendor -Recurse -Force
& $python packaging/smoke.py --executable dist/TKShangjia/TKShangjia.exe
if ($LASTEXITCODE) { throw '独立程序测试失败' }
$iscc = $env:TKSHANGJIA_ISCC
if (-not $iscc) { $iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
if (-not (Test-Path $iscc)) { throw '构建电脑还需安装 Inno Setup 6，再重新运行本脚本' }
& $iscc packaging/installer.iss
if ($LASTEXITCODE) { throw '安装包生成失败' }
Get-FileHash ..\release\TKShangjia-Setup-2026.11.24-x64.exe -Algorithm SHA256 | Format-List
$setup = Get-Item ..\release\TKShangjia-Setup-2026.11.24-x64.exe
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
"$hash  $($setup.Name)" | Set-Content -Encoding ASCII "$($setup.FullName).sha256"
Copy-Item build/vendor/dependencies.json ../release/dependencies.json
Copy-Item packaging/employee-readme.txt ../release/安装使用说明.txt
