# 恢复 prepare_upgrade.ps1 改动的驱动启动类型
#
# 读取 G:\转型\logs\driver_original_start.json（prepare_upgrade.ps1 写下的原值），
# 把每个驱动的 Start 值改回去。
#
# 什么时候用它
# ------------
#   · 升级成功 → 也可以不恢复（这些本来是卸载残留，禁用掉更好）
#   · 升级失败 / 想还原现场 → 跑这个脚本恢复原状
#   · 某个软件（深信服 VPN、腾讯）用不了 → 跑这个脚本恢复
#
# 用法（管理员 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File G:\转型\ai-lab\tools\restore_drivers.ps1

$ErrorActionPreference = 'Continue'
$origFile = 'G:\转型\logs\driver_original_start.json'

function L($m) { Write-Host "$(Get-Date -Format 'HH:mm:ss')  $m" }

if (-not (Test-Path $origFile)) {
    Write-Host "❌ 找不到原值清单：$origFile" -ForegroundColor Red
    Write-Host "   说明 prepare_upgrade.ps1 没跑过，或清单被删了。"
    Write-Host "   手动恢复方法：把 Start 值改回原样（迅雷 XLGuard=1, XLWFP=2，"
    Write-Host "   腾讯 QQProtectX64=2，深信服 SangforVnic=3，XLServicePlatform=2）"
    exit 1
}

$id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object System.Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "❌ 需要管理员权限" -ForegroundColor Red
    exit 1
}

L "=== 恢复驱动启动类型 ==="
L "原值清单：$origFile"

# PS 5.1 没有 -AsHashtable，手动转
$orig = @{}
try {
    $loaded = Get-Content $origFile -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $loaded.PSObject.Properties) { $orig[$p.Name] = [int]$p.Value }
} catch {
    L "❌ 解析失败：$($_.Exception.Message)"
    exit 1
}

# Start 值 -> sc 参数
$startArg = @{ 0 = 'boot'; 1 = 'system'; 2 = 'auto'; 3 = 'demand'; 4 = 'disabled' }

foreach ($name in $orig.Keys) {
    $val = $orig[$name]
    $arg = $startArg[$val]
    L ""
    L "--- $name （恢复到 Start=$val / $arg）---"
    $r = & sc.exe config $name start= $arg 2>&1
    L "  sc config: $($r -join ' | ')"
    if ($arg -ne 'disabled') {
        $r2 = & sc.exe start $name 2>&1
        L "  sc start : $($r2 -join ' | ')"
    }
    $now = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$name" -ErrorAction SilentlyContinue).Start
    L "  验证：Start 现在 = $now  $($(if ($now -eq $val) { '✅ 已恢复' } else { '⚠️ 不一致' }))"
}

L ""
L "=== 恢复完成 ==="
L "提示：有些驱动要重启后才会真正加载。"
