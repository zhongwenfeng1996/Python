#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多次重跑来量化端到端的**真实波动区间**

## 为什么必须做

我观察到的端到端数字（同一份代码、同一套评估集）：

    0.9615  →  0.9423  →  0.8654

跨度 0.10，约 **5 条题**。这已经不是"小数点后的噪声"，
而是**报告数字会误导人**的程度。

ADR-011 已经证明了模型输出不稳定（同一道题跑 4 次只弃答 1 次），
所以正确的做法是：**跑 N 次，报均值 + 区间**，而不是单点。

## 这个脚本做什么

跑 N 次 `run_generation.py`（每次都会写一个 JSON），
把 N 次的指标汇总成"均值 ± 区间"，并列出每条的弃答情况，
看**波动来自哪几条题**。

⚠️ 每次都会真调生成 API（约 50 次调用，1~2 元），
   重排分数有缓存所以不用重打。

用法：
    python eval/probe_variance.py --n 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args()

    outs: list[Path] = []
    for i in range(1, args.n + 1):
        out = HERE.parent / "data" / f"variance-{i}.json"
        print(f"  第 {i}/{args.n} 次…", flush=True)
        r = subprocess.run(
            [sys.executable, "-u", str(HERE / "run_generation.py"),
             "--out", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(HERE.parent))
        if r.returncode != 0:
            print(f"    ❌ 失败：{(r.stderr or r.stdout or '')[-300:]}")
            continue
        outs.append(out)

    if not outs:
        print("❌ 一次都没成功")
        return 1

    runs = [json.loads(p.read_text(encoding="utf-8")) for p in outs]
    keys = ["abstain_acc", "false_abstain", "cites_ok_rate",
            "give_cite_rate", "answer_chunk_ctx_rate", "e2e"]
    labels = {
        "abstain_acc": "弃答准确率（不可答题）",
        "false_abstain": "误弃答率（可答题被拒）",
        "cites_ok_rate": "引用合法率",
        "give_cite_rate": "引用覆盖率",
        "answer_chunk_ctx_rate": "含答案块在资料里（上限）",
        "e2e": "端到端",
    }

    print()
    print("=" * 92)
    print(f"  端到端波动量化（{len(runs)} 次运行）")
    print("=" * 92)
    print(f"  {'指标':<26} {'最小':>8} {'最大':>8} {'均值':>8} {'极差':>8}  逐次")
    print("  " + "-" * 86)
    for k in keys:
        vals = [r["metrics"][k] for r in runs if k in r["metrics"]]
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        print(f"  {labels[k]:<26} {lo:>8.4f} {hi:>8.4f} "
              f"{statistics.mean(vals):>8.4f} {hi-lo:>8.4f}  "
              f"{[round(v, 4) for v in vals]}")

    print()
    print("  ★ 波动来自哪几条题（跨运行弃答情况不一致的）")
    # 按题 id 汇总：几次运行里弃答了几次
    per_q: dict[str, list[bool]] = {}
    for r in runs:
        for rec in r["records"]:
            per_q.setdefault(rec["id"], []).append(rec["abstained"])
    unstable = {qid: xs for qid, xs in per_q.items()
                if len(set(xs)) > 1}
    print(f"    共 {len(unstable)} 条题在不同运行里弃答与否不同:")
    for qid, xs in sorted(unstable.items()):
        n_ab = sum(xs)
        kind = next((rec["type"] for rec in runs[0]["records"]
                     if rec["id"] == qid), "?")
        ans = next((rec["answerable"] for rec in runs[0]["records"]
                    if rec["id"] == qid), True)
        print(f"      {qid:<7} [{kind:<8}] 可答={ans}  "
              f"{len(xs)} 次里弃答 {n_ab} 次")
    print()

    stab = len(per_q) - len(unstable)
    print(f"    稳定的: {stab}/{len(per_q)} 条")
    print()
    print("=" * 92)
    print("  结论怎么写进报告")
    print("=" * 92)
    print("    · 端到端这类指标**必须给区间**（均值 ± 极差），不能给单点")
    print("    · 极差如果超过 0.05，说明'改进'的说法要谨慎 ——")
    print("      除非改动的效果明显大于波动")
    print("    · 上表列的'不稳定题'是**复现性差**的题，值得单独看")
    print("      （可能是边界情况，也可能说明评估集有歧义）")
    print("=" * 92)
    # 顺手清理
    for p in outs:
        p.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
