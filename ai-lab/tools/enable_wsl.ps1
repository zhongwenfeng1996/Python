# 启用 Docker Desktop 需要的 Windows 功能
#
# 需要启用的两个功能：
#   Microsoft-Windows-Subsystem-Linux   WSL 本体
#   VirtualMachinePlatform              虚拟机平台（WSL2 依赖它）
#
# 为什么不用 `wsl --install`：
#   那条命令在 Windows 10 19045 上也能用，但它会顺带装一个发行版
#   （Ubuntu），而我们只需要 WSL 运行时 —— Docker Desktop 自带它的
#   发行版（docker-desktop）。少装一个发行版更干净。
#
# ⚠️ 启用这两个功能后**必须重启**才生效。
#
# 用法（管理员 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File G:\转型\ai-lab\tools\enable_wsl.ps1

$ErrorActionPreference = 'Continue'
$log = 'G:\转型\logs\enable_wsl.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function L($m) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $m"
    $line | Out-File $log -Append -Encoding UTF8
    Write-Host $line
}

"=== 启用 WSL 功能 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $log -Encoding UTF8

$id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object System.Security.Principal.WindowsPrincipal($id)
L "用户: $($id.Name)  管理员位: $($pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator))"
if (-not $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    L "❌ 需要管理员权限"
    exit 1
}

$os = Get-CimInstance Win32_OperatingSystem
L "系统: $($os.Caption) Build $($os.BuildNumber)"
L ""

# ----------------------------------------------------------------------
# 记录启用前的状态（供对比）
# ----------------------------------------------------------------------
L "--- 启用前状态 ---"
foreach ($f in 'Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform') {
    $out = & dism /online /get-featureinfo /featurename:$f 2>&1
    $state = ($out | Select-String -Pattern '^状态|^State' | Select-Object -First 1)
    L "  $f : $(if ($state) { $state.Line.Trim() } else { '(查询失败)' })"
}
L ""

# ----------------------------------------------------------------------
# 启用功能
# ----------------------------------------------------------------------
# /norestart：不自动重启，让用户决定什么时候重启（避免打断手头工作）
$features = @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform')
foreach ($f in $features) {
    L "--- 启用 $f ---"
    $out = & dism /online /enable-feature /featurename:$f /all /norestart 2>&1
    foreach ($line in $out) {
        if ($line -match '错误|Error|成功|success|完成|complete|重新启动|restart') {
            L "  $($line.Trim())"
        }
    }
    L "  dism 退出码: $LASTEXITCODE"
}
L ""

# ----------------------------------------------------------------------
# 验证
# ----------------------------------------------------------------------
L "--- 启用后状态 ---"
foreach ($f in $features) {
    $out = & dism /online /get-featureinfo /featurename:$f 2>&1
    $state = ($out | Select-String -Pattern '^状态|^State' | Select-Object -First 1)
    L "  $f : $(if ($state) { $state.Line.Trim() } else { '(查询失败)' })"
}
L ""

# ----------------------------------------------------------------------
# 是否要求重启
# ----------------------------------------------------------------------
$pending = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
L "需要重启才生效: $pending"
L ""
L "=== 下一步 ==="
L "  重启电脑，然后回来告诉我。"
L "  重启后我会验证 wsl 是否可用，再继续装 WSL2 内核与 Docker Desktop。"
