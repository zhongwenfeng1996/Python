<#
.SYNOPSIS
    一键启动项目一（本地 mock 模式，不需要任何 API Key）。

.DESCRIPTION
    会做四件事：
      1. 找到虚拟环境里的 python（没有就报错并告诉你怎么建）
      2. 在后台起 mock 上游（假装是模型服务）
      3. 在后台起 FastAPI 后端，并把日志写到 logs\
      4. 等端口就绪后打印地址

    停止：按 Ctrl+C，脚本会一并关掉两个后台进程。

.PARAMETER Port
    后端监听端口，默认 8000。

.PARAMETER MockPort
    mock 上游端口，默认 8765。

.PARAMETER NoBrowser
    不自动打开浏览器。

.EXAMPLE
    .\run.ps1
    .\run.ps1 -Port 8100 -NoBrowser
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [int]$MockPort = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $root '..\..\..')).Path          # G:\转型
$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
$mockScript = Join-Path $repo 'ai-lab\week01\mock_server.py'
$backendDir = Join-Path $root 'backend'
$logDir = Join-Path $root 'logs'

if (-not (Test-Path $venvPython)) {
    Write-Host "[x] 找不到虚拟环境：$venvPython" -ForegroundColor Red
    Write-Host "    先建一个并装依赖（国内建议加镜像）：" -ForegroundColor Yellow
    Write-Host "      py -3 -m venv `"$repo\.venv`""
    Write-Host "      & `"$venvPython`" -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple fastapi `"uvicorn[standard]`" httpx pytest pytest-asyncio"
    exit 1
}
if (-not (Test-Path $mockScript)) {
    Write-Host "[x] 找不到 mock 服务：$mockScript" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Wait-Port([int]$p, [int]$timeoutSec = 15) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $client.Connect('127.0.0.1', $p)
            $client.Close()
            return $true
        } catch {
            Start-Sleep -Milliseconds 150
        } finally {
            if ($client) { $client.Dispose() }
        }
    }
    return $false
}

Write-Host "[1/3] 启动 mock 上游 (:$MockPort) ..." -ForegroundColor Cyan
$mock = Start-Process -FilePath $venvPython `
    -ArgumentList @($mockScript, '--port', $MockPort, '--delay', '0.01') `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'mock.out.log') `
    -RedirectStandardError  (Join-Path $logDir 'mock.err.log')
if (-not (Wait-Port $MockPort)) {
    Write-Host "[x] mock 未能在 15s 内启动，看 logs\mock.err.log" -ForegroundColor Red
    Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "[2/3] 启动后端 (:$Port) ..." -ForegroundColor Cyan
$env:OPENAI_BASE_URL = "http://127.0.0.1:$MockPort/v1"
$env:OPENAI_API_KEY  = 'test'
$env:PYTHONUTF8      = '1'
$app = Start-Process -FilePath $venvPython `
    -ArgumentList @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', $Port) `
    -WorkingDirectory $backendDir -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'app.out.log') `
    -RedirectStandardError  (Join-Path $logDir 'app.err.log')
if (-not (Wait-Port $Port)) {
    Write-Host "[x] 后端未能在 15s 内启动，看 logs\app.err.log" -ForegroundColor Red
    Stop-Process -Id $app.Id, $mock.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "[3/3] 就绪" -ForegroundColor Green
$url = "http://127.0.0.1:$Port/"
Write-Host ""
Write-Host "  打开：      $url" -ForegroundColor White
Write-Host "  模式：      本地 mock（不花钱，不需要 API Key）" -ForegroundColor Yellow
Write-Host "  API 文档：  ${url}docs" -ForegroundColor White
Write-Host "  应用日志：  $logDir\app.err.log" -ForegroundColor DarkGray
Write-Host "  上游日志：  $logDir\mock.err.log" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  按 Ctrl+C 停止（会同时关掉 mock 与后端）" -ForegroundColor DarkGray

if (-not $NoBrowser) { Start-Process $url | Out-Null }

try {
    while ($true) {
        Start-Sleep -Seconds 1
        if ($app.HasExited) {
            Write-Host "[!] 后端进程已退出（exit=$($app.ExitCode)），看 logs\app.err.log" -ForegroundColor Red
            break
        }
    }
} finally {
    Write-Host "`n正在停止 ..." -ForegroundColor DarkGray
    Stop-Process -Id $app.Id  -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue
    Write-Host "已停止。" -ForegroundColor DarkGray
}
