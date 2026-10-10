#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从评估集里截取前 N 条可答题，生成临时评估集 —— 用于小规模试跑。

为什么要小规模试跑：LLM 重排一次全量评估是 52 题 × 50 候选，
按每批 10 个算是约 260 次 API 调用。先在 5 条题上验证接线，
比直接烧全量稳妥。

用法：
    python eval/make_subset.py 5              # 前 5 条可答题
    python eval/make_subset.py 5 --by-type rewrite   # 按类型筛
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, help="取几条可答题")
    ap.add_argument("--by-type", default="", help="只取某个类型，如 rewrite")
    ap.add_argument("--out", default="", help="输出路径，默认 eval/_subset.jsonl")
    args = ap.parse_args()

    src = HERE / "questions.jsonl"
    rows = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        rows.append(json.loads(line))

    pool = [q for q in rows if q.get("gold_doc")]
    if args.by_type:
        pool = [q for q in pool if q.get("type") == args.by_type]
    picked = pool[:args.n]

    out = Path(args.out) if args.out else (HERE / "_subset.jsonl")
    with out.open("w", encoding="utf-8") as f:
        for q in picked:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    print(f"  {len(picked)} 条可答题 -> {out}")
    for q in picked:
        print(f"    {q['id']}  [{q.get('type')}]  {q['question'][:44]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
