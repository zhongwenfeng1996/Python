# 升级前准备：建还原点 + 禁用疑似干扰升级的残留驱动
#
# 为什么需要这个脚本
# ------------------
# Windows 1903 -> 22H2 升级失败，SetupDiag 结论：
#     Error    = 0xC1900101-0x20003
#     Phase    = Safe OS
#     Operation= Add [1] package ...KB5026361
#     Bugcheck = 0xCE DRIVER_UNLOADED_WITHOUT_CANCELLING_PENDING_OPERATIONS
#
# 0xCE 的字面意思是"驱动卸载时留下未完成操作"，
# 而升级的 Safe OS 阶段会卸载/重载大量驱动。
#
# 诊断发现（详见 ai-lab/tools/list_thirdparty_drivers.py）：
#   运行中的第三方内核驱动只有 3 个，加上服务层共 5 个可疑项：
#     XLGuard.sys         迅雷    Start=1(System)  —— 迅雷已卸载，驱动却留着
#     xlwfp.sys           迅雷    Start=2(Auto)    —— WFP 网络过滤驱动
#     XLServicePlatform   迅雷    服务 Auto
#     QQProtectX64.sys    腾讯    Start=2(Auto)
#     SangforVnic.sys     深信服  Start=3(Manual)
#
# 本脚本做的事（全部可逆）
# ------------------------
#   1. 启用 C 盘系统还原并分配卷影空间
#   2. 创建还原点
#   3. 把上述 5 项设为"禁用"（Start=4），记下原值以便回滚
#
# 不做的事
# --------
#   · 不删除任何文件
#   · 不动 Microsoft 签名的驱动
#   · 不改网络/安全策略
#
# 回滚方法：跑同目录的 restore_drivers.ps1（会读本脚本写下的原值清单）

$ErrorActionPreference = 'Continue'
$log = 'G:\转型\logs\prepare_upgrade.log'
$origFile = 'G:\转型\logs\driver_original_start.json'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function L($m) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $m"
    $line | Out-File $log -Append -Encoding UTF8
    Write-Host $line
}

"=== 升级前准备 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $log -Encoding UTF8

# 确认是管理员
$id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object System.Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    L "❌ 不是管理员权限，退出"
    exit 1
}
L "✓ 管理员权限确认（$($id.Name)）"

# ----------------------------------------------------------------------
# 步骤 1：启用系统还原
# ----------------------------------------------------------------------
L ""
L "--- 步骤 1：启用 C 盘系统还原 ---"
try {
    Enable-ComputerRestore -Drive 'C:\' -ErrorAction Stop
    L "✓ Enable-ComputerRestore C:\ 成功"
} catch {
    L "⚠️ Enable-ComputerRestore 失败：$($_.Exception.Message)"
    # 退路：用 WMI 打开
    try {
        $sr = Get-CimInstance -Namespace root/default -ClassName SystemRestore -ErrorAction Stop
        Invoke-CimMethod -InputObject $sr -MethodName Enable -Arguments @{Drive = 'C:\' } | Out-Null
        L "✓ 改用 WMI Enable 成功"
    } catch {
        L "❌ WMI 也失败：$($_.Exception.Message)"
    }
}

# ----------------------------------------------------------------------
# 步骤 2：分配卷影存储空间
#   不分配的话，还原点创建会因"空间不足"失败。
#   C 盘 187 GB，分配 10 GB（约 5%）足够存一个还原点。
# ----------------------------------------------------------------------
L ""
L "--- 步骤 2：分配卷影存储空间（10 GB）---"
$out = & vssadmin resize shadowstorage /for=C: /on=C: /maxsize=10GB 2>&1
L "  vssadmin: $($out -join ' | ')"
$out2 = & vssadmin list shadowstorage 2>&1
foreach ($line in $out2) { L "  $line" }

# ----------------------------------------------------------------------
# 步骤 3：创建还原点
# ----------------------------------------------------------------------
L ""
L "--- 步骤 3：创建还原点 ---"
# 关掉系统自带的"24 小时内不重复建点"限制，否则可能被跳过
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore' `
    -Name 'SystemRestorePointCreationFrequency' -Value 0 -Type DWord -ErrorAction SilentlyContinue
try {
    Checkpoint-Computer -Description '升级 22H2 前（prepare_upgrade.ps1）' `
        -RestorePointType 'MODIFY_SETTINGS' -ErrorAction Stop
    L "✓ 还原点创建成功"
} catch {
    L "⚠️ Checkpoint-Computer 失败：$($_.Exception.Message)"
    # 退路：WMI
    try {
        $sr = Get-CimInstance -Namespace root/default -ClassName SystemRestore -ErrorAction Stop
        $r = Invoke-CimMethod -InputObject $sr -MethodName CreateRestorePoint `
            -Arguments @{ Description = '升级 22H2 前'; RestorePointType = 0; EventType = 100 }
        L "  WMI 返回码: $($r.ReturnValue)（0 = 成功）"
    } catch {
        L "❌ WMI 建点也失败：$($_.Exception.Message)"
    }
}
Start-Sleep -Seconds 3
$points = Get-ComputerRestorePoint -ErrorAction SilentlyContinue
if ($points) {
    L "  现有还原点："
    $points | ForEach-Object { L "    序号 $($_.SequenceNumber)  $($_.Description)  $($_.CreationTime)" }
} else {
    L "  ⚠️ 查不到还原点（可能刚建还没索引，或创建失败）"
}

# ----------------------------------------------------------------------
# 步骤 4：禁用疑似干扰升级的驱动与服务（记录原值）
# ----------------------------------------------------------------------
L ""
L "--- 步骤 4：禁用疑似干扰项（全部可逆）---"

$targets = @(
    @{ Name = 'XLGuard';           Kind = 'driver';  Note = '迅雷内核驱动（迅雷已卸载，残留）' },
    @{ Name = 'XLWFP';             Kind = 'driver';  Note = '迅雷 WFP 网络过滤驱动' },
    @{ Name = 'QQProtectX64';      Kind = 'driver';  Note = '腾讯 QQProtect 内核驱动' },
    @{ Name = 'SangforVnic';       Kind = 'driver';  Note = '深信服 VPN 虚拟网卡' },
    @{ Name = 'XLServicePlatform'; Kind = 'service'; Note = '迅雷服务' }
)

$orig = @{}
if (Test-Path $origFile) {
    # ⚠️ 不能用 ConvertFrom-Json -AsHashtable —— 那是 PowerShell 6+ 的参数，
    #    本机是 5.1，会直接报错。改成手动转成 hashtable。
    try {
        $loaded = Get-Content $origFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $loaded.PSObject.Properties) {
            $orig[$p.Name] = [int]$p.Value
        }
    } catch {
        L "  （原值文件解析失败，将重新记录：$($_.Exception.Message)）"
        $orig = @{}
    }
}

foreach ($t in $targets) {
    $key = "HKLM:\SYSTEM\CurrentControlSet\Services\$($t.Name)"
    if (-not (Test-Path $key)) {
        L "  [跳过] $($t.Name) —— 注册表键不存在"
        continue
    }
    $cur = (Get-ItemProperty $key -ErrorAction SilentlyContinue).Start
    if ($null -eq $cur) {
        L "  [跳过] $($t.Name) —— 读不到 Start 值"
        continue
    }
    # 只在第一次记录原值（避免重跑时覆盖成已禁用的值）
    if (-not $orig.ContainsKey($t.Name)) {
        $orig[$t.Name] = [int]$cur
        L "  记录原值 $($t.Name): Start=$cur"
    } else {
        L "  已有记录 $($t.Name): Start=$($orig[$t.Name])（保留不覆盖）"
    }

    # 先停服务（正在运行的驱动不能直接改 Start）
    $r1 = & sc.exe stop $t.Name 2>&1
    L "    sc stop $($t.Name): $($r1 -join ' | ')"

    # 设为禁用
    $r2 = & sc.exe config $t.Name start= disabled 2>&1
    $ok = ($r2 -join '') -match 'SUCCESS|成功'
    L "    sc config $($t.Name) start= disabled: $($r2 -join ' | ')"

    $now = (Get-ItemProperty $key -ErrorAction SilentlyContinue).Start
    L "    验证：Start 现在 = $now  $($(if ($now -eq 4) { '✅ 已禁用' } else { '⚠️ 未生效' }))   [$($t.Note)]"
}

$orig | ConvertTo-Json | Out-File $origFile -Encoding UTF8
L ""
L "原值清单已保存：$origFile"
L "（恢复用 ai-lab\tools\restore_drivers.ps1）"

# ----------------------------------------------------------------------
# 汇总
# ----------------------------------------------------------------------
L ""
L "--- 当前状态汇总 ---"
foreach ($t in $targets) {
    $key = "HKLM:\SYSTEM\CurrentControlSet\Services\$($t.Name)"
    if (Test-Path $key) {
        $s = (Get-ItemProperty $key -ErrorAction SilentlyContinue).Start
        $st = switch ($s) { 0 {'引导'} 1 {'系统'} 2 {'自动'} 3 {'手动'} 4 {'已禁用'} default {"未知($s)"} }
        $run = (Get-CimInstance Win32_SystemDriver -Filter "Name='$($t.Name)'" -ErrorAction SilentlyContinue).State
        if (-not $run) { $run = (Get-CimInstance Win32_Service -Filter "Name='$($t.Name)'" -ErrorAction SilentlyContinue).State }
        L ("  {0,-20} Start={1} ({2})  运行状态={3}" -f $t.Name, $s, $st, $run)
    }
}
L ""
L "=== 准备完成 ==="
