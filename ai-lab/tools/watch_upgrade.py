#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监控 Windows 升级进度 —— 正确解读阶段，不再被 "100%" 误导。

为什么需要这个脚本
------------------
第一次升级失败时，我（AI）看到 setupact.log 里的
    MOUPG  Overall progress: [100%]
就判断"已就绪、可以重启"，让用户重启，结果安装中途被打断。

**那个 100% 是"下载/准备阶段"的 100%，不是整体安装的 100%。**
真正的安装（Safe OS 离线阶段）在它之后才开始。

这个脚本做的事：
  1. 解析当前的 **Setup Phase**（真正决定能做什么）
  2. 从 MOUPG 进度行里取"下载阶段"与"整体"两个不同口径
  3. 明确告诉调用者：现在能不能重启
  4. 检查有没有出现失败特征（bugcheck / rollback）

用法：
    python watch_upgrade.py            # 看一次
    python watch_upgrade.py --watch    # 持续盯（每 15 秒刷新）
    python watch_upgrade.py --reboot-ok   # 只回答"现在能不能重启"
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

LOGS = [
    Path(r"C:\$WINDOWS.~BT\Sources\Panther\setupact.log"),
    Path(r"C:\$WINDOWS.~BT\Sources\Rollback\setupact.log"),
    Path(r"C:\Windows\Panther\setupact.log"),
]

# Setup 阶段的顺序（大致），用来说明"现在走到哪了"
PHASE_ORDER = [
    "SetupPhaseDownLevel",      # 启动
    "SetupPhaseInitialize",     # 初始化
    "SetupPhaseGather",         # 收集信息
    "SetupPhaseCompatCheck",    # 兼容性检查
    "SetupPhaseDownload",       # 下载
    "SetupPhaseInstall",        # 安装（Safe OS 离线阶段就在这里面）
    "SetupPhasePostFinalize",   # 收尾：等待重启
    "SetupPhasePreOnline",      # 重启后在线阶段
    "SetupPhaseOnline",         # 在线迁移
    "SetupPhaseFinalize",       # 最终收尾
    "SetupPhaseEnd",            # 结束
]

# 这些阶段表示"正在进行安装，绝对不要重启"
DONT_REBOOT = {"SetupPhaseInstall", "SetupPhaseOnline", "SetupPhasePreOnline"}
# 这些阶段表示"可以重启"（系统在等重启）
REBOOT_OK = {"SetupPhasePostFinalize", "SetupPhaseFinalize", "SetupPhaseEnd"}


def scan_for_phase(path: Path, max_bytes: int = 60_000_000) -> list[tuple[str, str]]:
    """
    扫描全文找 "Setup phase change: [A] -> [B]"，返回 [(时间戳, 新阶段)]。

    为什么要扫全文：阶段变化是稀疏事件，最后一次变化可能在日志很靠前的位置。
    只读尾部会漏掉，导致误判"还没开始"。

    日志可能很大（本次实测涨到几十 MB），所以逐块读、只保留匹配行，
    不把整个文件读进内存。
    """
    results: list[tuple[str, str]] = []
    pat = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[^\n]*?"
                     r"Setup phase change:\s*\[(\w+)\]\s*->\s*\[(\w+)\]")
    try:
        read = 0
        with path.open("rb") as f:
            for raw in f:
                read += len(raw)
                if read > max_bytes:
                    break
                try:
                    line = raw.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
                m = pat.search(line)
                if m:
                    results.append((m.group(1), m.group(3)))
    except Exception:  # noqa: BLE001
        pass
    return results


def read_tail(path: Path, n: int = 400) -> list[str]:
    try:
        # 日志可能很大，只读尾部
        size = path.stat().st_size
        with path.open("rb") as f:
            chunk = min(size, 400_000)
            f.seek(max(0, size - chunk))
            data = f.read().decode("utf-8", errors="replace")
        return data.splitlines()[-n:]
    except Exception:  # noqa: BLE001
        return []


def analyze() -> dict:
    out: dict = {
        "log": None, "phase": None, "phase_history": [],
        "progress_lines": [], "errors": [], "rollback": False,
        "last_write": None, "verdict": "未知", "advice": "",
    }

    target = None
    newest = 0.0
    for lg in LOGS:
        if lg.exists():
            m = lg.stat().st_mtime
            if m > newest:
                newest = m
                target = lg
    if target is None:
        out["verdict"] = "找不到日志"
        out["advice"] = "升级还没开始？或者日志被清理了。"
        return out

    out["log"] = str(target)
    out["last_write"] = time.strftime("%H:%M:%S", time.localtime(newest))
    lines = read_tail(target)

    # 阶段变化：**必须扫全文，不能只看尾部**
    # ⚠️ 这是个实测踩到的坑：第一版只读尾部 400 行，
    #    而 "Setup phase change" 行往往出现在很靠前的位置
    #    （阶段变化是稀疏事件），于是显示 phase=None，
    #    看起来像"还没开始"，其实早就进安装阶段了。
    phase_lines = scan_for_phase(target)
    for _ts, to_phase in phase_lines:
        out["phase_history"].append(to_phase)
    if phase_lines:
        out["phase"] = phase_lines[-1][1]

    # 进度（区分两个口径）
    for ln in lines[-60:]:
        m = re.search(r"MOUPG\s+Overall progress:\s*\[(\d+)%\]", ln)
        if m:
            out["progress_lines"].append(("下载/准备阶段", m.group(1)))
        m2 = re.search(r"Global progress:\s*(\d+),\s*Phase progress:\s*(\d+)", ln)
        if m2:
            out["progress_lines"].append(
                (f"安装（整体 {m2.group(1)}% / 本阶段 {m2.group(2)}%）", m2.group(2)))
        m3 = re.search(r"SPDismProgressCallback:\s*Progress:\s*(\d+)", ln)
        if m3:
            out["progress_lines"].append(("DISIM 应用映像", m3.group(1)))

    # 失败特征
    for ln in lines:
        if re.search(r"bugcheck|BugCheck|0xC1900101|RollbackExecuteSequence|"
                     r"Rolling back|rollback started", ln, re.I):
            out["errors"].append(ln.strip()[:180])
    if any("Rollback" in e for e in out["errors"]):
        out["rollback"] = True

    # 结论
    ph = out["phase"]
    if out["rollback"]:
        out["verdict"] = "⚠️ 正在回滚（本次升级失败）"
        out["advice"] = "查 SetupDiag 拿失败原因：G:\\SetupDiag.exe"
    elif ph in DONT_REBOOT:
        out["verdict"] = f"🚫 正在安装（{ph}）—— 绝对不要重启/关机"
        out["advice"] = "等它自己完成。可以看日志，但别动系统。"
    elif ph in REBOOT_OK:
        out["verdict"] = f"✅ 可以重启了（{ph}）"
        out["advice"] = "系统已准备好，重启会进入在线阶段。让 Windows 自己弹提示更稳。"
    elif ph:
        out["verdict"] = f"进行中（{ph}）"
        out["advice"] = "如果不是安装阶段，可以等；不确定就别动。"
    else:
        out["verdict"] = "日志里还没有阶段信息"
        out["advice"] = "升级可能刚启动，稍等再查。"

    return out


def render(r: dict) -> None:
    print("=" * 78)
    print("  Windows 升级进度监控")
    print("=" * 78)
    print(f"  日志    : {r['log']}")
    print(f"  最后写入: {r['last_write']}")
    print()
    print(f"  当前阶段: {r['phase']}")
    if r["phase_history"]:
        hist = " → ".join(dict.fromkeys(r["phase_history"]))
        print(f"  阶段轨迹: {hist}")
    print()
    if r["phase"] in PHASE_ORDER:
        idx = PHASE_ORDER.index(r["phase"])
        bar = "".join("█" if i <= idx else "·" for i in range(len(PHASE_ORDER)))
        print(f"  进度条  : {bar}")
        print(f"            {PHASE_ORDER[0]} … {r['phase']}")
    print()
    if r["progress_lines"]:
        print("  进度读数（注意区分口径）：")
        seen = set()
        for label, pct in reversed(r["progress_lines"][-6:]):
            key = (label, pct)
            if key in seen:
                continue
            seen.add(key)
            print(f"     {label:<38} {pct}%")
    print()
    if r["errors"]:
        print("  ⚠️ 异常特征：")
        for e in r["errors"][-5:]:
            print(f"     {e}")
        print()
    print("─" * 78)
    print(f"  {r['verdict']}")
    print(f"  {r['advice']}")
    print("=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="持续监控")
    ap.add_argument("--interval", type=int, default=15)
    ap.add_argument("--reboot-ok", action="store_true",
                    help="只输出结论，便于脚本判断")
    args = ap.parse_args()

    if args.reboot_ok:
        r = analyze()
        ph = r["phase"]
        print(f"phase={ph}")
        if r["rollback"]:
            print("verdict=ROLLED_BACK")
            return 2
        if ph in DONT_REBOOT:
            print("verdict=DO_NOT_REBOOT")
            return 1
        if ph in REBOOT_OK:
            print("verdict=REBOOT_OK")
            return 0
        print("verdict=WAIT")
        return 1

    if not args.watch:
        render(analyze())
        return 0

    try:
        while True:
            r = analyze()
            print("\033[2J\033[H", end="")     # 清屏
            render(r)
            if r["rollback"] or r["phase"] in ("SetupPhaseEnd", "SetupPhaseFinalize"):
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n（已停止监控）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
