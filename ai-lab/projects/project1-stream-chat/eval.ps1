<#
.SYNOPSIS
    评估入口 —— Windows 上 `make eval` 的替代品。

.DESCRIPTION
    为什么不写 Makefile：Windows 默认没有 make，装了 mingw/gnuwin32 的那一份
    命令语法又和 bash 下的不同（`make eval` 在 PowerShell 里要先解决 make 从哪来）。
    这个仓库的目标是"换台 Windows 机器 clone 下来就能跑"，
    所以用 .ps1 做统一入口，不引入额外工具链。

    六个动作对应"评估一件 LLM 应用"的六个层次：
      smoke       离线冒烟：证明评估框架自己能跑（不需要 Key，CI 用这个）
      stability   稳定性：同一问题重复 N 次，看答案是否一致
      jitter      对照实验：故意让上游不稳定，验证稳定性指标真的能报警
      gates       门禁：带阈值跑，不达标就非 0 退出（CI 拦截用）
      full        真实模型评估（需要在 backend\.env 配好 Key）
      report      打印上一次的 report.json

.EXAMPLE
    .\eval.ps1 smoke
    .\eval.ps1 stability -Repeat 5
    .\eval.ps1 jitter
    .\eval.ps1 gates -FailUnder 80 -StabilityUnder 90
    .\eval.ps1 full -Model qwen-plus -Repeat 3
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('smoke', 'stability', 'jitter', 'gates', 'full', 'report', 'help')]
    [string]$Action = 'smoke',

    [int]$Repeat = 1,
    [string]$Model = 'deepseek-chat',
    [double]$Temperature = 0.7,
    [double]$FailUnder = 0,
    [double]$StabilityUnder = 0,
    [int]$AppPort = 8811,
    [int]$MockPort = 8812
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $root '..\..\..')).Path
$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
$evalScript = Join-Path $root 'eval\run_eval.py'
$reportPath = Join-Path $root 'eval\report.json'

if (-not (Test-Path $venvPython)) {
    Write-Host "[x] 找不到虚拟环境：$venvPython" -ForegroundColor Red
    Write-Host "    先跑：Set-Location $repo; .\setup.ps1" -ForegroundColor Yellow
    exit 1
}

$env:PYTHONUTF8 = '1'
$common = @('--app-port', $AppPort, '--mock-port', $MockPort)

switch ($Action) {
    'help' {
        Get-Help $PSCommandPath -Detailed
        exit 0
    }
    'smoke' {
        & $venvPython $evalScript --suite smoke @common
    }
    'stability' {
        if ($Repeat -lt 2) { $Repeat = 3 }
        & $venvPython $evalScript --suite smoke --repeat $Repeat `
            --temperature $Temperature @common
    }
    'jitter' {
        # 对照实验：如果这次稳定性没掉下来，说明稳定性指标是坏的
        if ($Repeat -lt 2) { $Repeat = 3 }
        Write-Host "对照实验：故意让上游每次都不同 —— 稳定性必须掉下来" -ForegroundColor Yellow
        & $venvPython $evalScript --suite smoke --repeat $Repeat --jitter-mock `
            --stability-under 90 --temperature $Temperature @common
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ok] 稳定性指标按预期报警了（这就是我们想看到的）" -ForegroundColor Green
            exit 0
        }
        Write-Host "[x] 上游不稳定，但门禁没拦住 —— 稳定性指标是坏的" -ForegroundColor Red
        exit 1
    }
    'gates' {
        if ($Repeat -lt 2) { $Repeat = 3 }
        $fu = if ($FailUnder -gt 0) { $FailUnder } else { 80 }
        $su = if ($StabilityUnder -gt 0) { $StabilityUnder } else { 90 }
        Write-Host "门禁：通过率 >= $fu% ，稳定性 >= $su%" -ForegroundColor Cyan
        & $venvPython $evalScript --suite smoke --repeat $Repeat `
            --fail-under $fu --stability-under $su @common
    }
    'full' {
        Write-Host "真实模型评估：$Model（需要在 backend\.env 配好对应 provider 的 Key）" -ForegroundColor Cyan
        & $venvPython $evalScript --suite full --model $Model --repeat $Repeat `
            --temperature $Temperature --no-serve --base-url "http://127.0.0.1:$AppPort"
    }
    'report' {
        if (-not (Test-Path $reportPath)) {
            Write-Host "[x] 还没有报告，先跑一次 smoke" -ForegroundColor Red
            exit 1
        }
        Get-Content -LiteralPath $reportPath -Encoding UTF8 | Write-Host
        exit 0
    }
}

$code = $LASTEXITCODE
Write-Host ""
if ($code -eq 0) {
    Write-Host "评估通过（exit 0）" -ForegroundColor Green
} else {
    Write-Host "评估未通过（exit $code）" -ForegroundColor Red
}
exit $code
