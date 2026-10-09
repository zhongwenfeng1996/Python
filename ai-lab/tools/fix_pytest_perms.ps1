# 修复升级后被锁住的 pytest 临时目录权限
#
# 症状（实测）
# ----------
#   MCP 项目测试从 24 passed 变成 23 passed + 1 error：
#     PermissionError: [WinError 5] 拒绝访问。:
#       'G:\转型\ai-lab\projects\mcp-minimal\pytest-of-Administrator'
#   PytestCacheWarning: could not create cache path ...\.pytest_cache\v\cache
#
# 原因
# ----
#   Windows 升级后，仓库里某些目录的 ACL 变得只允许更高权限访问
#   （这些目录是在提权会话里产生的，继承了受限的权限）。
#   当前开发用的进程不是管理员（管理员位=False），于是读写被拒。
#
# 做法
# ----
#   对受影响的目录：接管所有权 -> 授予当前用户完全控制 -> 删除（让 pytest 重建）
#
# 为什么可以放心删
# ----------------
#   · pytest-of-* 和 .pytest_cache 都是**临时/缓存目录**，删除后自动重建
#   · 它们不在 git 里（已被 .gitignore 覆盖），删了不会有任何代码损失
#
# 用法（管理员 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File G:\转型\ai-lab\tools\fix_pytest_perms.ps1

$ErrorActionPreference = 'Continue'
$log = 'G:\转型\logs\fix_pytest_perms.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function L($m) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $m"
    $line | Out-File $log -Append -Encoding UTF8
    Write-Host $line
}

"=== 修复 pytest 目录权限 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $log -Encoding UTF8

$id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object System.Security.Principal.WindowsPrincipal($id)
L "用户: $($id.Name)  管理员位: $($pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator))"
if (-not $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    L "❌ 需要管理员权限"
    exit 1
}

$repo = 'G:\转型'
$me = New-Object System.Security.Principal.NTAccount($id.Name)
$admins = New-Object System.Security.Principal.NTAccount('BUILTIN\Administrators')

# 要找的目标：pytest 的临时目录 + 缓存目录
#
# ⚠️ 通配符必须开好边界 —— 这里踩过一次真实的坑
# ---------------------------------------------------------------
# 第一版用了 `pytest-*` 这个模式，结果它**匹配到了**
#     .venv\Lib\site-packages\pytest-9.1.1.dist-info
# 也就是 pytest 自己的**包元数据目录**，被脚本当垃圾删掉了。
# 后果：pytest 代码本体还在、命令还能跑，但 `pip show pytest` 报
# "Package(s) not found" —— pip 元数据丢了，`pip list` 会显示不全。
# 修复代价：重装一次 pytest（--force-reinstall --no-deps）。
#
# 教训：**清理脚本的通配符必须限定目录层级**，不能在整个仓库里
#       用一个宽松的模式乱匹配，尤其绝不能碰 .venv。
$searchRoots = @(Join-Path $repo 'ai-lab')      # 只在这个范围找
$targets = @()
foreach ($root in $searchRoots) {
    foreach ($pat in 'pytest-of-*', '.pytest_cache') {
        $targets += Get-ChildItem -LiteralPath $root -Recurse -Directory -Filter $pat -ErrorAction SilentlyContinue
    }
}
# 先删最深的（否则父目录删了子目录就找不到了）
$targets = $targets | Sort-Object { $_.FullName.Length } -Descending | Select-Object -Unique

# 安全网：绝不删除虚拟环境 / site-packages 下的任何东西
$safe = @()
foreach ($t in $targets) {
    if ($t.FullName -match '\\\.venv\\' -or $t.FullName -match 'site-packages') {
        L "  [安全网拦下] 跳过（在虚拟环境里，不该由本脚本处理）: $($t.FullName)"
    } else {
        $safe += $t
    }
}
$targets = $safe

L ""
L "找到 $($targets.Count) 个目标目录"

foreach ($t in $targets) {
    L ""
    L "--- $($t.FullName.Replace($repo + '\', '')) ---"
    try {
        $acl = Get-Acl -LiteralPath $t.FullName -ErrorAction Stop
        L "  当前 Owner: $($acl.Owner)"
    } catch {
        L "  读 ACL 失败（说明确实没权限）: $($_.Exception.Message.Split([char]10)[0])"
        $acl = $null
    }

    # 1) 接管所有权
    try {
        if ($null -eq $acl) {
            # 没权限读 ACL，用 takeown 命令（它是为这种场景设计的）
            $r = & takeown /F $t.FullName /R /D Y 2>&1
            L "  takeown: $(($r | Select-Object -First 2) -join ' | ')"
        } else {
            $acl.SetOwner($admins)
            Set-Acl -LiteralPath $t.FullName -AclObject $acl -ErrorAction Stop
            L "  ✓ 所有权已接管 -> Administrators"
        }
    } catch {
        L "  接管所有权失败: $($_.Exception.Message.Split([char]10)[0])"
    }

    # 2) 授予完全控制（同时给当前用户和 Administrators，避免以后再锁）
    try {
        $acl2 = Get-Acl -LiteralPath $t.FullName -ErrorAction Stop
        foreach ($acct in @($me, $admins)) {
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                $acct, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
            $acl2.SetAccessRule($rule)
        }
        Set-Acl -LiteralPath $t.FullName -AclObject $acl2 -ErrorAction Stop
        L "  ✓ 已授予完全控制（$($id.Name) + Administrators）"
    } catch {
        L "  授权失败: $($_.Exception.Message.Split([char]10)[0])"
    }

    # 3) 删除（这是临时目录，删了会自动重建）
    try {
        Remove-Item -LiteralPath $t.FullName -Recurse -Force -ErrorAction Stop
        L "  ✓ 已删除（pytest 会按需重建）"
    } catch {
        L "  ⚠️ 删除失败: $($_.Exception.Message.Split([char]10)[0])"
    }
}

L ""
L "=== 验证：用非提权身份试写 ==="
foreach ($probe in @(
    "$repo\ai-lab\projects\mcp-minimal\pytest-of-Administrator",
    "$repo\ai-lab\projects\mcp-minimal\.pytest_cache"
)) {
    if (Test-Path $probe) {
        L "  仍存在: $probe"
    } else {
        L "  已清除: $probe"
    }
}

L ""
L "=== 完成。回到非提权终端重跑测试即可。 ==="
