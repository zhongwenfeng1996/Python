#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断 Windows 升级失败原因：0xC1900101-0x20003（Safe OS 阶段，驱动相关问题）

背景：
  易升升级 1903 -> 22H2 失败，回滚。SetupDiag 结论：
    Last Phase     = Safe OS
    Last Operation = Add [1] package ...KB5026361
    Error          = 0xC1900101-0x20003
    Bugcheck       = 0xCE DRIVER_UNLOADED_WITHOUT_CANCELLING_PENDING_OPERATIONS

  0xC1900101 官方排查方向：第三方驱动/安全软件/优化工具干扰。

这个脚本只读，不改任何东西。输出给用户看，用来判断重试前要动什么。
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
import sys
from pathlib import Path

# 强制 stdout 为 UTF-8，避免重定向到文件时按 GBK 编码报错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def run(cmd: list[str], timeout: int = 120) -> str:
    """跑一个命令，吞掉报错，返回文本。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"[执行失败: {exc}]"


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


# ======================================================================
# 1. 过滤驱动（安全软件、加密软件最爱用的东西）
# ======================================================================

def check_filter_drivers() -> list[str]:
    hr("1. 过滤驱动（fltmc）—— 第三方安全/加密软件会在这一层")
    out = run(["fltmc", "filters"])
    print(out.strip())

    # 微软自带的过滤驱动白名单
    known_ms = {
        "bindflt", "storqosflt", "wcifs", "cldflt", "filecrypt", "luafv",
        "npsvctrig", "wof", "FileInfo", "WdFilter", "FileCrypt",
        "bindFlt", "UnionFS", "WimFltr", "WimFsf",
    }
    suspicious: list[str] = []
    for line in out.splitlines():
        m = re.match(r"^\s*(\S+)\s+(\d+)\s+(\d+)", line)
        if not m:
            continue
        name = m.group(1)
        if name not in known_ms:
            suspicious.append(name)
    if suspicious:
        print("\n  ⚠️ 非微软自带的过滤驱动（升级时可能干扰）:")
        for s in suspicious:
            print(f"      - {s}")
    else:
        print("\n  ✅ 没发现非微软自带的过滤驱动")
    return suspicious


# ======================================================================
# 2. 第三方安全软件 / 杀毒
# ======================================================================

def check_av() -> None:
    hr("2. 已注册的安全软件（WMI SecurityCenter2）")
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance -Namespace root/SecurityCenter2 "
               "-ClassName AntiVirusProduct -ErrorAction SilentlyContinue | "
               "Select-Object displayName,productState | Format-List"])
    print(out.strip() or "  （查不到）")

    hr("2b. 常见国产安全/优化软件进程与服务")
    keywords = ["360", "huorong", "火绒", "tencent", "qqpcmgr", "kingsoft",
                "金山", "baidu", "百度", "rising", "瑞星", "jiangmin", "江民",
                "avast", "avg", "kaspersky", "卡巴", "norton", "诺顿",
                "mcafee", "symantec", "eset", "bitdefender", "malwarebytes",
                "电脑管家", "安全卫士", "优化大师", "驱动精灵", "驱动人生"]
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-Process | Select-Object -ExpandProperty ProcessName"])
    procs = [p.strip() for p in out.splitlines() if p.strip()]
    hits = [p for p in procs if any(k.lower() in p.lower() for k in keywords)]
    if hits:
        print(f"  ⚠️ 发现可疑进程: {sorted(set(hits))}")
    else:
        print("  ✅ 进程里没发现常见安全/优化软件")

    out = run(["powershell", "-NoProfile", "-Command",
               "Get-Service | Where-Object {$_.Status -eq 'Running'} | "
               "Select-Object -ExpandProperty DisplayName"])
    names = [n.strip() for n in out.splitlines() if n.strip()]
    hits2 = [n for n in names if any(k.lower() in n.lower() for k in keywords)]
    if hits2:
        print(f"  ⚠️ 发现可疑服务: {hits2}")
    else:
        print("  ✅ 服务里没发现常见安全/优化软件")


# ======================================================================
# 3. 存储 / 芯片组驱动（0xCE bugcheck 高发区）
# ======================================================================

def check_storage_drivers() -> None:
    hr("3. 存储控制器与磁盘驱动（AMD 平台 + NVMe 是高发组合）")
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_PnPEntity | "
               "Where-Object {$_.PNPClass -in @('SCSIAdapter','HDC','System','DiskDrive')} | "
               "Select-Object Name,Manufacturer,DriverVersion,PNPClass | "
               "Format-Table -AutoSize | Out-String -Width 200"])
    print(out.strip()[:4000])


# ======================================================================
# 4. 有问题的设备（黄色感叹号）
# ======================================================================

def check_problem_devices() -> None:
    hr("4. 有问题的设备（ConfigManagerErrorCode != 0）")
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_PnPEntity | "
               "Where-Object {$_.ConfigManagerErrorCode -ne 0} | "
               "Select-Object Name,ConfigManagerErrorCode | Format-Table -AutoSize | Out-String -Width 200"])
    print(out.strip() or "  ✅ 没有报错的设备")


# ======================================================================
# 5. 非微软的内核驱动（第三方 .sys）
# ======================================================================

def check_third_party_drivers() -> None:
    hr("5. 正在运行的非微软内核驱动（这类驱动最可能触发 0xC1900101）")
    out = run(["driverquery", "/v", "/fo", "csv"], timeout=180)
    if "[执行失败" in out:
        print(out)
        return
    try:
        rows = list(csv.DictReader(io.StringIO(out)))
    except Exception as exc:  # noqa: BLE001
        print(f"  解析失败: {exc}")
        return

    suspects = []
    for r in rows:
        if r.get("State", "").strip() != "Running":
            continue
        path = (r.get("Path", "") or "").strip()
        if not path:
            continue                      # 内核内置的没有路径
        low = path.lower()
        if "\\windows\\system32\\drivers\\" in low or "\\system32\\drivers\\" in low:
            # 微软自带驱动一般也在 drivers 目录，所以要看是否有第三方签名线索
            # 这里只列出非 win32k/wdf 等常见微软名的
            base = Path(path).name.lower()
            ms_like = base.startswith(("ndis", "tcpip", "http", "afd", "netbt",
                                       "volmgr", "volsnap", "storport", "storahci",
                                       "stornvme", "disk", "partmgr", "fvevol",
                                       "iorate", "mup", "npfs", "msfs", "fltmgr",
                                       "ks", "usb", "hid", "acpi", "pci", "wdf",
                                       "ntfs", "fastfat", "refs", "cng", "ksecdd",
                                       "tpm", "win32k", "dxgkrnl", "watchdog"))
            if not ms_like:
                suspects.append((base, path, r.get("DisplayName", "")))

    if suspects:
        print(f"  可能非微软的已运行驱动（{len(suspects)} 个，需人工确认）:")
        seen = set()
        for base, path, disp in suspects:
            if base in seen:
                continue
            seen.add(base)
            print(f"    {base:<28} {disp}")
    else:
        print("  ✅ 没发现明显非微软的驱动")


# ======================================================================
# 6. 从回滚日志里找直接证据
# ======================================================================

def check_rollback_logs() -> None:
    hr("6. 回滚日志里的直接证据（Searching Rollback\\setupact.log）")
    log = Path(r"C:\$WINDOWS.~BT\Sources\Rollback\setupact.log")
    if not log.exists():
        print(f"  不存在: {log}")
        return
    text = log.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    print(f"  日志共 {len(lines)} 行，搜关键失败点：\n")
    pat = re.compile(r"bugcheck|BugCheck|0xCE|0xC1900101|rollback|Rollback|"
                     r"DriverVerifier|TM|unexpected|critic|Critic|Fatal", re.I)
    hits = [ln for ln in lines if pat.search(ln)]
    for ln in hits[-25:]:
        print("  " + ln.strip()[:170])

    hr("6b. 回滚目录里的 diagerr.xml")
    err = Path(r"C:\$WINDOWS.~BT\Sources\Rollback\diagerr.xml")
    if err.exists():
        t = err.read_text(encoding="utf-8", errors="replace")
        cnt = len(re.findall(r"<Error", t))
        print(f"  错误记录数: {cnt}")
        for m in re.findall(r"<Error[^>]{0,300}", t)[:5]:
            print("    " + m[:200])


def main() -> int:
    print("=" * 78)
    print("  升级失败诊断 · 0xC1900101-0x20003（Safe OS 阶段，驱动相关）")
    print("  只读诊断，不修改任何设置")
    print("=" * 78)
    check_filter_drivers()
    check_av()
    check_storage_drivers()
    check_problem_devices()
    check_third_party_drivers()
    check_rollback_logs()
    print()
    print("=" * 78)
    print("  诊断结束")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
