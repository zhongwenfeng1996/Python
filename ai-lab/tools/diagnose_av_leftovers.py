#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查 360 / 火绒 / 其他安全软件的残留：服务、内核驱动、安装目录

为什么单独查这个：
  安全中心显示注册了 3 套杀软（360杀毒、360安全卫士、火绒），
  但进程和服务列表里都查不到 —— 典型的"卸载残留"。
  残留的**内核驱动和过滤驱动**是 0xC1900101 升级失败的头号原因：
  它们在 Safe OS 阶段被加载/卸载时留下未完成操作（正是 bugcheck 0xCE 的含义）。

这个脚本只读，不改任何东西。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# 各家安全软件的驱动/服务名特征
VENDOR_PATTERNS = {
    "360": ["360", "qhsafe", "qhactive", "qutmdrv", "360box", "360fsflt",
            "360netmon", "360antisrv", "zhudongfangyu", "safesvr", "bapidrv",
            "dsark", "dsdriver"],
    "火绒 Huorong": ["huorong", "hr", "sysdiag", "hrwfpdrv", "hrkrnl",
                     "hrfilter", "火绒"],
    "腾讯": ["qqpcmgr", "tencent", "qqpc", "tsafer"],
    "金山": ["kingsoft", "ksafe", "金山"],
    "百度": ["baidu", "bd000", "baidusd"],
    "驱动类工具": ["drivergenius", "drivergenius", "drvlife", "驱动精灵",
                   "驱动人生", "drvinst"],
    "其他常见": ["wsc", "safemon", "nse", "symantec", "kaspersky", "avast",
                 "avgnt", "avgn", "mbam"],
}


def run(cmd: list[str], timeout: int = 120) -> str:
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


def match_vendor(name: str) -> str | None:
    low = name.lower()
    for vendor, keys in VENDOR_PATTERNS.items():
        if any(k.lower() in low for k in keys):
            return vendor
    return None


def check_services() -> list[tuple[str, str, str, str]]:
    hr("1. 所有服务里匹配安全软件特征的（含已停止/已禁用）")
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_Service | "
               "Select-Object Name,DisplayName,State,StartMode,PathName | "
               "ConvertTo-Csv -NoTypeInformation"])
    found = []
    for line in out.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 5:
            continue
        name, disp, state, mode, path = parts[0], parts[1], parts[2], parts[3], parts[4]
        v = match_vendor(name) or match_vendor(disp)
        if v:
            found.append((v, name, f"{state}/{mode}", path))
    if found:
        for v, name, st, path in found:
            print(f"  ⚠️ [{v}] {name}  ({st})")
            print(f"        {path[:150]}")
    else:
        print("  ✅ 服务里没有匹配的安全软件特征")
    return found


def check_kernel_drivers() -> list[tuple[str, str, str]]:
    hr("2. 内核驱动（driverquery）里匹配安全软件特征的")
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_SystemDriver | "
               "Select-Object Name,DisplayName,State,StartMode,PathName | "
               "ConvertTo-Csv -NoTypeInformation"], timeout=180)
    found = []
    for line in out.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 5:
            continue
        name, disp, state, mode, path = parts[0], parts[1], parts[2], parts[3], parts[4]
        v = match_vendor(name) or match_vendor(disp)
        if v:
            found.append((v, name, f"{state}/{mode}  {path}"))
    if found:
        for v, name, info in found:
            print(f"  ⚠️ [{v}] {name}")
            print(f"        {info[:150]}")
    else:
        print("  ✅ 驱动列表里没有匹配的安全软件特征")
    return found


def check_leftover_dirs() -> None:
    hr("3. 磁盘上残留的安装目录")
    candidates = [
        r"C:\Program Files (x86)\360", r"C:\Program Files\360",
        r"C:\ProgramData\360safe", r"C:\ProgramData\360sd",
        r"C:\Program Files\Huorong", r"C:\Program Files (x86)\Huorong",
        r"C:\Program Files\Huorong\Sysdiag",
        r"C:\Program Files (x86)\Tencent", r"C:\Program Files\Tencent",
        r"C:\Program Files (x86)\Kingsoft", r"C:\Program Files\Kingsoft",
        r"C:\Program Files (x86)\Baidu", r"C:\Program Files\Baidu",
    ]
    any_found = False
    for c in candidates:
        p = Path(c)
        if p.exists():
            any_found = True
            try:
                files = list(p.rglob("*"))[:200]
                sz = sum(f.stat().st_size for f in files if f.is_file())
                print(f"  ⚠️ 存在: {c}  ({len(files)} 个条目, ≥{sz // 1024} KB)")
            except Exception:  # noqa: BLE001
                print(f"  ⚠️ 存在: {c}")
    if not any_found:
        print("  ✅ 常见安全软件目录都不存在")


def check_av_registry() -> None:
    hr("4. 安全中心注册表里的 AV 注册项（谁还在'自称'存在）")
    keys = [
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    found = []
    for base in keys:
        out = run(["reg", "query", base, "/s", "/f", "360"])
        if "HKEY" in out:
            found.append(("360", base))
        out2 = run(["reg", "query", base, "/s", "/f", "火绒"])
        if "HKEY" in out2:
            found.append(("火绒", base))
    if found:
        for name, base in found:
            print(f"  ⚠️ 卸载注册表里还有 {name} 的项（{base}）")
    else:
        print("  ✅ 卸载注册表里没有 360/火绒 的残留项")


def check_wsc_state() -> None:
    hr("5. 安全中心三家的实时状态（productState 解码）")
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance -Namespace root/SecurityCenter2 "
               "-ClassName AntiVirusProduct | "
               "Select-Object displayName,productState,pathToSignedProductExe | "
               "ConvertTo-Csv -NoTypeInformation"])
    print("  productState 位含义（十六进制第 3 位）：")
    print("    0x?0 = 关闭    0x?1 = 开启且最新    0x?2 = 开启但过期")
    print("    0x1? = 由用户控制   0x0? = 由系统/策略控制")
    print()
    for line in out.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            state = int(parts[1])
        except ValueError:
            continue
        hx = f"{state:06X}"
        exe = parts[2] if len(parts) > 2 else ""
        on = (state >> 12) & 0xF          # 实时防护开关位
        up = (state >> 4) & 0xF           # 是否最新
        print(f"  {name}")
        print(f"      productState = {state} (0x{hx})  防护={'开' if on else '关'}/"
              f"定义={'新' if up else '旧'}")
        print(f"      exe = {exe[:120]}")
        exists = Path(exe).exists() if exe else False
        print(f"      该 exe 是否还在 → {'是' if exists else '❌ 否（残留注册项）'}")


def main() -> int:
    print("=" * 78)
    print("  安全软件残留排查（360 / 火绒 / 其他）")
    print("  只读诊断")
    print("=" * 78)
    svc = check_services()
    drv = check_kernel_drivers()
    check_leftover_dirs()
    check_av_registry()
    check_wsc_state()

    hr("结论")
    print(f"  匹配到的服务: {len(svc)} 个")
    print(f"  匹配到的驱动: {len(drv)} 个")
    if svc or drv:
        print()
        print("  → 有残留实体存在。这些驱动/服务在升级的 Safe OS 阶段")
        print("    被加载和卸载，正是 0xC1900101 + bugcheck 0xCE 的高发原因。")
        print("    下一步：用官方卸载程序彻底卸载，或用干净启动重试升级。")
    else:
        print()
        print("  → 服务/驱动层没查到残留，但安全中心仍注册着。")
        print("    可能只是注册表项残留（干扰较小）。")
        print("    下一步：优先尝试「干净启动 + 拔外设」重试升级。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
