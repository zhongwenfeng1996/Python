# 启动 Windows 10 22H2 就地升级
#
# 用法（管理员 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File G:\转型\ai-lab\tools\start_upgrade.ps1
#
# 背景
# ----
# 第一次尝试（易升）失败：0xC1900101-0x20003，Safe OS 阶段注入 KB5026361 时
# bugcheck 0xCE（驱动卸载留下未完成操作）。
#
# 之后做的准备：
#   · 建了还原点（序号 20）
#   · 禁用并卸载了 5 个第三方残留驱动（迅雷 3 个 + 腾讯 + 深信服）
#   · 重启确认过：正在运行的第三方内核驱动 = 0
#
# 参数说明（这些参数很关键）
# --------------------------
#   /auto upgrade          自动执行全部升级阶段（含重启），不弹选项
#   /DynamicUpdate Disable 不联网找可选更新 —— 装的是本地 install.esd，
#                          跳过动态更新能省时间、少一次网络依赖、少一个失败面
#   /BitLocker TryKeepActive
#                          如果开了 BitLocker，尽量保持激活，避免升级后要恢复密钥
#   /MigrateDrivers all    带上已装的驱动一起迁移（默认行为，写出来更明确）
#   /ShowOOBE None         跳过升级后的首次设置向导（保留原有账户设置）
#
# ⚠️ 重要：启动后**不要中断它**。
#    它会自己重启几次，那是正常的。上一次失败部分原因就是进程被提前打断。

$ErrorActionPreference = 'Stop'
$log = 'G:\转型\logs\start_upgrade.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function L($m) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $m"
    $line | Out-File $log -Append -Encoding UTF8
    Write-Host $line
}

"=== 启动升级 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $log -Encoding UTF8

# --- 管理员检查 ---
$id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object System.Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    L "❌ 需要管理员权限。请用『以管理员身份运行』打开 PowerShell 再跑本脚本。"
    exit 1
}
L "✓ 管理员权限（$($id.Name)）"

# --- 前置检查 ---
$setup = 'C:\$GetCurrent\media\setup.exe'
if (-not (Test-Path -LiteralPath $setup)) {
    L "❌ 找不到 setup.exe：$setup"
    L "   升级介质不在了。需要重新用易升下载，或下官方 ISO。"
    exit 1
}
$esd = 'C:\$GetCurrent\media\sources\install.esd'
if (-not (Test-Path -LiteralPath $esd)) {
    L "❌ 找不到 install.esd（安装镜像）。介质不完整，无法升级。"
    exit 1
}
L "✓ 介质完整：setup.exe + install.esd（$([math]::Round((Get-Item -LiteralPath $esd).Length/1GB,2)) GB）"

$free = [math]::Round((Get-PSDrive C).Free / 1GB, 2)
L "✓ C 盘空闲：$free GB"
if ($free -lt 20) {
    L "⚠️ 空闲不足 20 GB，升级可能失败。建议先清理。"
}

$build = (Get-CimInstance Win32_OperatingSystem).BuildNumber
L "✓ 当前 Build：$build（目标 19045 = 22H2）"

# 检查有没有正在跑的 setup，避免冲突
$running = Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -match 'SetupHost|setup$|Windows10Upgrade' }
if ($running) {
    L "⚠️ 检测到正在运行的升级进程："
    $running | ForEach-Object { L "     PID $($_.Id)  $($_.ProcessName)" }
    L "   请先等它结束，或手动结束后再跑本脚本。"
    exit 1
}

# --- 确认 ---
L ""
L "将要执行："
L "  $setup /auto upgrade /DynamicUpdate Disable /BitLocker TryKeepActive /ShowOOBE None"
L ""
L "预计：30~60 分钟，会自动重启 2~3 次"
L "⚠️ 过程中不要关机、不要强制重启"
L ""

$args = @(
    '/auto', 'upgrade',
    '/DynamicUpdate', 'Disable',
    '/BitLocker', 'TryKeepActive',
    '/ShowOOBE', 'None'
)

L "启动 setup.exe …"
try {
    $p = Start-Process -FilePath $setup -ArgumentList $args -PassThru -ErrorAction Stop
    L "✓ 已启动，PID = $($p.Id)"
    L ""
    L "接下来它会自己做。你可以关掉这个窗口，但【不要关掉 setup 的窗口】。"
    L ""
    L "监控进度的方法（另开 PowerShell）："
    L "  Get-Content 'C:\`$WINDOWS.~BT\Sources\Panther\setupact.log' -Tail 20 -Wait"
    L ""
    L "或者用仓库里的脚本看阶段："
    L "  python G:\转型\ai-lab\tools\watch_upgrade.py"
} catch {
    L "❌ 启动失败：$($_.Exception.Message)"
    exit 1
}
