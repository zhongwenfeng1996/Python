#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精确排查安全软件残留：直接找**驱动文件**，不做字符串模糊匹配。

为什么要重写：
  上一版用"厂商关键字匹配服务名"太宽泛，把系统服务（wscsvc）、
  Google Chrome 的 elevation_service 都误报了。
  真正可靠的证据是**磁盘上有哪些 .sys 文件**、**注册表 Services 键下
  有哪些第三方驱动服务** —— 这些骗不了人。

这个脚本只读。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# 已知的第三方安全/驱动类软件的内核驱动文件名（精确名，不用子串）
KNOWN_DRIVERS = {
    # 360 系
    "360box.sys": "360 安全卫士", "360fsflt.sys": "360 安全卫士",
    "360netmon.sys": "360 安全卫士", "dsark.sys": "360 杀毒",
    "dsdriver.sys": "360 杀毒", "qhsafe.sys": "360 安全卫士",
    "qhactivex.sys": "360 安全卫士", "qutmdrv.sys": "360 安全卫士",
    "bapidrv.sys": "360 安全卫士", "360antisrv.sys": "360 安全卫士",
    "sysdiag.sys": "火绒 Huorong", "hrwfpdrv.sys": "火绒 Huorong",
    "hrkrnl.sys": "火绒 Huorong", "hrfilter.sys": "火绒 Huorong",
    "sysdiag_win10.sys": "火绒 Huorong",
    # 腾讯
    "tsafer.sys": "腾讯电脑管家", "qqpcmgr.sys": "腾讯电脑管家",
    "qqpctray.sys": "腾讯电脑管家",
    # 金山 / 百度 / 其他
    "ksafe.sys": "金山毒霸", "bd0001.sys": "百度卫士",
    "bd0002.sys": "百度卫士", "bdfile.sys": "百度卫士",
    # 驱动管理类工具（也常干扰升级）
    "drivergenius.sys": "驱动精灵", "drvinst.sys": "驱动人生",
    "sysceo.sys": "系统总裁类工具",
    # 远程/虚拟化类（有时干扰）
    "sunlogin.sys": "向日葵远程",
}

# 这些是 Windows 自带的，即使名字看着像也不用管
MS_DRIVER_ALLOWLIST = {
    "mshidkmdf.sys", "mshidumdf.sys",   # 微软 HID 驱动（上一版把它们误报成火绒了）
    "wdcsam64.sys",                      # WD 硬盘的驱动，微软签名
    "wudfrd.sys", "wudfusbcciddriver.sys",
}


def run(cmd: list[str], timeout: int = 180) -> str:
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


def scan_driver_files() -> list[tuple[str, Path, str]]:
    """
    扫描驱动目录，找已知第三方安全软件的 .sys 文件。
    证据最硬 —— 文件在就是装了/残留了。
    """
    hr("1. 驱动目录里的第三方安全软件文件（最硬的证据）")
    dirs = [
        Path(r"C:\Windows\System32\drivers"),
        Path(r"C:\Windows\SysWOW64\drivers"),
        Path(r"C:\Windows\System32\DriverStore\FileRepository"),
    ]
    hits: list[tuple[str, Path, str]] = []
    for d in dirs:
        if not d.exists():
            continue
        try:
            for f in d.rglob("*.sys"):
                name = f.name.lower()
                if name in MS_DRIVER_ALLOWLIST:
                    continue
                vendor = KNOWN_DRIVERS.get(name)
                if vendor:
                    sz = f.stat().st_size
                    hits.append((vendor, f, f"{sz // 1024} KB  {f.stat().st_mtime}"))
        except Exception:  # noqa: BLE001
            continue

    if hits:
        # 去重（DriverStore 会有多份）
        seen = set()
        for vendor, path, info in sorted(hits):
            key = (vendor, path.name.lower())
            if key in seen:
                continue
            seen.add(key)
            print(f"  ⚠️ [{vendor}]  {path}")
            print(f"        {info}")
    else:
        print("  ✅ 驱动目录里没有已知第三方安全软件的 .sys 文件")
    return hits


def check_services_registry() -> None:
    """
    直接查注册表 Services 键下有没有第三方安全软件的驱动服务。
    比查 Win32_SystemDriver 更全（能查到已停止的）。
    """
    hr("2. 注册表 Services 键下的第三方安全软件驱动服务")
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Services' | "
               "Select-Object -ExpandProperty PSChildName"])
    names = [n.strip() for n in out.splitlines() if n.strip()]
    # 精确匹配已知驱动服务名（去掉 .sys 后缀）
    known_svc = {k[:-4]: v for k, v in KNOWN_DRIVERS.items()}
    found = []
    for n in names:
        v = known_svc.get(n.lower())
        if v:
            img = run(["powershell", "-NoProfile", "-Command",
                       f"(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\{n}' "
                       f"-ErrorAction SilentlyContinue).ImagePath"])
            start = run(["powershell", "-NoProfile", "-Command",
                         f"(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\{n}' "
                         f"-ErrorAction SilentlyContinue).Start"])
            found.append((v, n, img.strip(), start.strip()))
    if found:
        print("  Start 值含义：0=引导 1=系统 2=自动 3=手动 4=已禁用")
        for v, n, img, start in found:
            print(f"  ⚠️ [{v}] {n}   Start={start}")
            print(f"        {img[:130]}")
    else:
        print("  ✅ 注册表 Services 里没有已知安全软件驱动服务")


def check_hardware_devices() -> None:
    hr("3. 有问题的硬件设备（ConfigManagerErrorCode != 0）")
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_PnPEntity | "
               "Where-Object {$_.ConfigManagerErrorCode -ne 0} | "
               "Select-Object Name,DeviceID,ConfigManagerErrorCode | "
               "ConvertTo-Csv -NoTypeInformation"])
    lines = [ln for ln in out.splitlines()[1:] if ln.strip()]
    if not lines:
        print("  ✅ 没有报错的设备")
        return
    print("  错误码含义：22=已禁用 28=驱动未安装 47=准备移除 43/10=驱动问题")
    for ln in lines:
        parts = [p.strip('"') for p in ln.split('","')]
        if len(parts) >= 3:
            print(f"  ⚠️ [{parts[2]}] {parts[0]}")
            print(f"        {parts[1][:120]}")


def check_av_exe_presence() -> None:
    hr("4. 安全中心注册的那几个 exe，实际还在不在")
    exes = [
        (r"C:\Program Files (x86)\360\360sd\WscControl.exe", "360杀毒"),
        (r"C:\Program Files (x86)\360\360Safe\safemon\360tray.exe", "360安全卫士"),
        (r"C:\Program Files (x86)\Huorong\Sysdiag\bin\wsctrlsvc.exe", "火绒安全软件"),
    ]
    for path, name in exes:
        p = Path(path)
        if p.exists():
            sz = p.stat().st_size
            print(f"  ⚠️ [{name}] 存在  {sz // 1024} KB")
            print(f"        {path}")
        else:
            print(f"  ✓ [{name}] 不存在（纯注册项残留）")


def check_services_by_exe() -> None:
    """查有没有服务的可执行文件指向那三个安全软件目录（比名字匹配准）。"""
    hr("5. 可执行文件指向安全软件目录的服务/驱动")
    targets = [r"\360\\", r"\huorong\\", r"\tencent\\", r"\kingsoft\\"]
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_Service | "
               "Select-Object Name,State,StartMode,PathName | "
               "ConvertTo-Csv -NoTypeInformation"])
    found = []
    for ln in out.splitlines()[1:]:
        parts = [p.strip('"') for p in ln.split('","')]
        if len(parts) < 4:
            continue
        name, state, mode, path = parts
        low = path.lower()
        if any(t.replace("\\\\", "\\") in low for t in targets):
            found.append((name, state, mode, path))
    if found:
        for name, state, mode, path in found:
            print(f"  ⚠️ {name}  ({state}/{mode})")
            print(f"        {path[:140]}")
    else:
        print("  ✅ 没有服务的 exe 指向那些目录")


def main() -> int:
    print("=" * 78)
    print("  精确排查：安全软件驱动与服务（用文件证据，不做名字模糊匹配）")
    print("  只读诊断")
    print("=" * 78)
    drv = scan_driver_files()
    check_services_registry()
    check_hardware_devices()
    check_av_exe_presence()
    check_services_by_exe()

    hr("结论")
    if drv:
        print(f"  发现 {len(drv)} 个第三方安全软件驱动文件。")
        print("  这些驱动在升级 Safe OS 阶段被加载/卸载 —— 与 bugcheck 0xCE")
        print("  （驱动卸载时留下未完成操作）高度吻合。")
        print()
        print("  处理优先级：")
        print("    1. 有安装目录的（火绒/360）→ 用官方卸载程序卸载，重启")
        print("    2. 只有文件的残留 → 可用驱动清理，或直接清干净目录（谨慎）")
        print("    3. 重试升级")
    else:
        print("  驱动层干净。")
        print("  → 主要嫌疑转为：外设（USB UAS 存储设备报错 47）+ 偶发因素。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
