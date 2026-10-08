#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键重建 + 校验数据集。

用法：
    python ai-lab/scripts/rebuild_dataset.py                  # 默认规模（与 data/out 现状一致）
    python ai-lab/scripts/rebuild_dataset.py --users 1200 --events 9000 --tenants 12
    python ai-lab/scripts/rebuild_dataset.py --check-only      # 只校验，不重新生成

为什么需要一个统一入口：
    "生成"与"校验"必须绑在一起。文档里写的数据特征（30% 时区、0.5% 重复邮箱、
    最近 30 天登录下跌……）如果没人周期性验证，就会悄悄与数据脱节 —— 这份数据集
    已经出过一次这种事故。改生成器之后跑一次这个脚本，才知道改动有没有破坏别的东西。

退出码：0 = 生成成功且全部断言通过；非 0 = 有断言不成立。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description="重建并校验自造数据集")
    ap.add_argument("--users", type=int, default=1200)
    ap.add_argument("--events", type=int, default=9000)
    ap.add_argument("--tenants", type=int, default=12)
    ap.add_argument("--seed", type=int, default=None, help="不传则用生成器默认种子")
    ap.add_argument("--check-only", action="store_true", help="跳过生成，只跑校验")
    args = ap.parse_args()

    if not args.check_only:
        cmd = [sys.executable, str(HERE / "generate_saas_data.py"),
               "--users", str(args.users), "--events", str(args.events),
               "--tenants", str(args.tenants)]
        if args.seed is not None:
            cmd += ["--seed", str(args.seed)]
        print(f"[1/2] 生成数据集（users={args.users} events={args.events} tenants={args.tenants}）")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            return proc.returncode
        # 生成器的口径对照表很有价值，原样转出，便于直接粘进 README
        print(proc.stdout)
    else:
        print("[1/2] 跳过生成（--check-only）")

    print("[2/2] 校验数据集")
    proc = subprocess.run([sys.executable, str(HERE / "verify_dataset.py")],
                          capture_output=True, text=True, encoding="utf-8")
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
