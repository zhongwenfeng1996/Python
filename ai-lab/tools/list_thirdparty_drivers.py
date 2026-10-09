#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
列出所有**正在运行的第三方内核驱动** —— 升级失败排查的核心清单。

为什么这是关键：
  bugcheck 0xCE = DRIVER_UNLOADED_WITHOUT_CANCELLING_PENDING_OPERATIONS
  含义是"某驱动卸载时留下未完成操作"。
  升级过程（Safe OS 阶段）会卸载/重载大量驱动，
  所以**开机就加载的第三方内核驱动**是首要嫌疑。

判定方法（不靠关键字猜测）：
  用 Get-AuthenticodeSignature 查每个 .sys 的签名者。
  **不是 Microsoft 签名的 = 第三方**。这个判据是硬的。

只读诊断。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def run(cmd: list[str], timeout: int = 300) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"[执行失败: {exc}]"


def hr(t: str) -> None:
    print()
    print("=" * 78)
    print(f"  {t}")
    print("=" * 78)


def main() -> int:
    hr("正在运行的驱动（用签名者判定是否第三方）")

    # 一次拿到：名字、状态、启动类型、路径
    ps = (
        "Get-CimInstance Win32_SystemDriver | "
        "Where-Object {$_.State -eq 'Running'} | "
        "Select-Object Name,StartMode,PathName | "
        "ConvertTo-Csv -NoTypeInformation"
    )
    out = run(["powershell", "-NoProfile", "-Command", ps])
    rows = []
    for line in out.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 3:
            continue
        rows.append(parts)

    print(f"  正在运行的系统驱动共 {len(rows)} 个，逐个查签名…\n")

    third_party = []
    ms_count = 0
    unknown = []

    # 批量取签名（一次 PowerShell 调用，避免几百次进程启动）
    paths = []
    for name, mode, path in rows:
        p = path.replace(r"\??\\", "").replace(r"\SystemRoot", r"C:\Windows")
        p = p.replace(r"\??\\", "").lstrip("\\")
        if p and Path(p).exists():
            paths.append((name, mode, p))
        else:
            unknown.append((name, mode, path))

    # 用 Get-AuthenticodeSignature 批量签名
    if paths:
        quoted = ",".join("'" + p.replace("'", "''") + "'" for _, _, p in paths)
        sig_ps = (
            f"$paths = @({quoted}); "
            "foreach ($p in $paths) { "
            "  $s = Get-AuthenticodeSignature -LiteralPath $p -ErrorAction SilentlyContinue; "
            "  $subj = if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { 'UNSIGNED' }; "
            "  Write-Output ($p + '|' + $subj) }"
        )
        sig_out = run(["powershell", "-NoProfile", "-Command", sig_ps], timeout=900)
        sig_map = {}
        for line in sig_out.splitlines():
            if "|" in line:
                pth, subj = line.rsplit("|", 1)
                sig_map[pth.strip()] = subj.strip()

        for name, mode, p in paths:
            subj = sig_map.get(p, "")
            is_ms = ("Microsoft" in subj and "Windows" in subj) or \
                    ("Microsoft Corporation" in subj) or \
                    ("Microsoft Windows" in subj)
            if is_ms:
                ms_count += 1
            else:
                cn = ""
                for part in subj.split(","):
                    part = part.strip()
                    if part.startswith("CN="):
                        cn = part[3:]
                        break
                third_party.append((name, mode, p, cn or subj or "?"))
    else:
        print("  没取到可用的驱动路径")

    print(f"  微软签名: {ms_count} 个（正常，略过）")
    print(f"  非微软签名: {len(third_party)} 个")
    print(f"  路径不存在（内核内置/已卸载）: {len(unknown)} 个")
    print()

    if third_party:
        print("  ⚠️ 第三方内核驱动清单（按厂商分组）:")
        by_vendor: dict[str, list] = {}
        for name, mode, p, cn in third_party:
            by_vendor.setdefault(cn, []).append((name, mode, Path(p).name))
        for vendor in sorted(by_vendor, key=lambda v: -len(by_vendor[v])):
            items = by_vendor[vendor]
            print(f"\n    【{vendor}】{len(items)} 个")
            for name, mode, fname in sorted(items):
                print(f"       {name:<26} {mode:<8} {fname}")
    else:
        print("  ✅ 没有第三方内核驱动在运行")

    hr("重点嫌疑：这几类最可能干扰升级")
    risky_keywords = {
        "安全": ["360", "huorong", "sysdiag", "qqprotect", "sangfor", "edr",
                 "defender", "avast", "kaspersky"],
        "虚拟化/网络过滤": ["vpn", "wireguard", "sangfor", "tap", "vnic",
                            "oray", "sunlogin", "teamviewer"],
        "外设/数位板": ["gaomon", "ftdi", "hid", "tablet"],
        "磁盘/存储过滤": ["stor", "disk", "filter", "flt", "volume"],
    }
    for label, keys in risky_keywords.items():
        hits = [tp for tp in third_party
                if any(k in tp[0].lower() or k in tp[1].lower() or k in tp[2].lower()
                       or k in tp[3].lower() for k in keys)]
        if hits:
            print(f"\n  【{label}】")
            for name, mode, p, cn in hits:
                print(f"     {name:<26} {Path(p).name:<24} [{cn[:36]}]")

    print()
    print("=" * 78)
    print("  说明：")
    print("    · 这些驱动并非都有问题。但升级 Safe OS 阶段会卸载/重载它们，")
    print("      其中任何一个有 bug 都会导致 0xC1900101。")
    print("    · 最稳的验证方式是**干净启动**：临时禁用所有非微软服务与启动项，")
    print("      这样第三方驱动不加载，再试升级。成功了就说明是其中某个。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
