<#
.SYNOPSIS
    一次性环境初始化：找到 Python → 建 .venv → 装依赖 → 自检。

.DESCRIPTION
    这个脚本存在的唯一理由：Windows 上"装 Python"这一步有太多隐性坑，
    而每一个坑的报错都很难和原因对应上。脚本把坑都堵掉：

      1. Windows 没有 `python3`（那是 macOS/Linux 的写法）
      2. `python` 可能只是 Microsoft Store 的占位符，运行时报
         "Python was not found but can be installed from the Microsoft Store"
      3. pypi.org 在部分网络下不可达，直连会**挂住**而不是报错
      4. 沙箱/权限受限时写不了用户的 pip.ini，只能把镜像配置放在仓库里

    脚本会依次尝试多个解释器，跳过 Store 占位符，最后用找到的那个建 .venv。

.PARAMETER PythonExe
    显式指定解释器路径（自动探测失败时用）。

.PARAMETER SkipInstall
    只建 venv，不装依赖。

.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -PythonExe "G:\Python\Python312\python.exe"
#>
[CmdletBinding()]
param(
    [string]$PythonExe = '',
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$pipConfig = Join-Path $root 'ai-lab\projects\project1-stream-chat\pip.ini'
$requirements = Join-Path $root 'ai-lab\projects\project1-stream-chat\requirements.txt'

function Test-UsablePython([string]$exe) {
    <#
      判断一个解释器是否"真的能用"。

      关键：Windows 的 python.exe 占位符（WindowsApps 下的那个）**存在但不可用** ——
      运行它会打印一句"去 Microsoft Store 安装"并返回非 0。所以不能只 Test-Path。

      版本门槛交给解释器自己判断（`sys.version_info >= (3, 10)`），
      而不是在 PowerShell 里解析版本字符串 —— 后者写过一次就错了一次。
    #>
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    if ($exe -like '*\WindowsApps\*') { return $false }   # Store 占位符，直接排除
    try {
        & $exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

Write-Host "=== 1/4 查找可用的 Python ===" -ForegroundColor Cyan
$candidates = @()
if ($PythonExe) { $candidates += $PythonExe }
$candidates += @(
    'G:\Python\Python312\python.exe',      # 本仓库文档推荐的安装位置
    'C:\Python312\python.exe',
    'C:\Python313\python.exe',
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe')
)
foreach ($name in 'python', 'py') {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
}

$python = $null
foreach ($c in $candidates) {
    if ($c -and (Test-UsablePython $c)) { $python = $c; break }
}

if (-not $python) {
    Write-Host "[x] 没找到可用的 Python 3.10+" -ForegroundColor Red
    Write-Host ""
    Write-Host "    注意 Windows 上没有 python3，而 python 可能只是 Store 占位符。" -ForegroundColor Yellow
    Write-Host "    装一个（装到 G 盘，避免占 C 盘空间）：" -ForegroundColor Yellow
    Write-Host '      $u = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"'
    Write-Host '      Invoke-WebRequest $u -OutFile "$env:TEMP\py.exe"'
    Write-Host '      Start-Process "$env:TEMP\py.exe" -Wait -ArgumentList "/quiet","InstallAllUsers=0","TargetDir=G:\Python","PrependPath=1","Include_launcher=1"'
    Write-Host "    然后重跑本脚本。" -ForegroundColor Yellow
    exit 1
}
Write-Host "  使用解释器：$python" -ForegroundColor Green
& $python --version

Write-Host "=== 2/4 创建虚拟环境 ===" -ForegroundColor Cyan
if (Test-Path $venvPython) {
    Write-Host "  已存在，跳过：$venv" -ForegroundColor DarkGray
} else {
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Host "[x] venv 创建失败" -ForegroundColor Red; exit 1 }
    Write-Host "  已创建：$venv" -ForegroundColor Green
}
& $venvPython --version

if (-not $SkipInstall) {
    Write-Host "=== 3/4 安装依赖 ===" -ForegroundColor Cyan
    # 镜像用命令行参数传，**不要**用 PIP_CONFIG_FILE：
    #
    # 踩过的坑：pip 读配置文件时按系统本地编码（中文 Windows = cp936）处理，
    # 而本仓库路径是 G:\转型\... 含中文，于是 pip 报
    #   "Configuration file contains invalid cp936 characters in ...\pip.ini"
    # 把 pip.ini 改成纯 ASCII 也没用 —— 出问题的是**路径**，不是内容。
    # 结论：非 ASCII 路径下配置文件这条路走不通，直接用命令行参数最省事。
    $mirror = @('--index-url', 'https://pypi.tuna.tsinghua.edu.cn/simple',
                '--trusted-host', 'pypi.tuna.tsinghua.edu.cn',
                '--timeout', '30')
    Write-Host "  镜像：https://pypi.tuna.tsinghua.edu.cn/simple" -ForegroundColor DarkGray
    & $venvPython -m pip install -q --disable-pip-version-check @mirror --upgrade pip 2>&1 |
        Select-Object -Last 3
    & $venvPython -m pip install -q --disable-pip-version-check @mirror -r $requirements 2>&1 |
        Select-Object -Last 3
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[x] 依赖安装失败。" -ForegroundColor Red
        Write-Host "    若卡住不动，说明镜像也不可达；换一个镜像，例如：" -ForegroundColor Yellow
        Write-Host "      --index-url https://mirrors.aliyun.com/pypi/simple" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  依赖已安装" -ForegroundColor Green
    # 不在 PowerShell 里内联 python -c 代码：5.1 的引号规则很容易把脚本解析坏
    # （实测踩过：多一层引号就报 "Missing expression after ','"，报错位置还指到别处）。
    # 需要打印版本时，直接跑下面第 4 步的 env_report.py，它会把版本一起打出来。
} else {
    Write-Host "=== 3/4 跳过依赖安装（-SkipInstall） ===" -ForegroundColor DarkGray
}

Write-Host "=== 4/4 自检 ===" -ForegroundColor Cyan
& $venvPython (Join-Path $root 'ai-lab\tools\env_report.py')
$envExit = $LASTEXITCODE

Write-Host ""
Write-Host "============================================================" -ForegroundColor White
if ($envExit -eq 0) {
    Write-Host " 环境就绪。下一步：" -ForegroundColor Green
} else {
    Write-Host " .venv 已建好，但环境自检有告警（多数不影响学习）。下一步：" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "   跑项目一（无需 API Key）："
Write-Host "     cd ai-lab\projects\project1-stream-chat; .\run.ps1"
Write-Host ""
Write-Host "   跑测试："
Write-Host "     cd ai-lab\projects\project1-stream-chat; & `"$venvPython`" -m pytest"
Write-Host ""
Write-Host "   校验数据集（31 条断言）："
Write-Host "     cd ai-lab; & `"$venvPython`" scripts\verify_dataset.py"
Write-Host "============================================================" -ForegroundColor White
