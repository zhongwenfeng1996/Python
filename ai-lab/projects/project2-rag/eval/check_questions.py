#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核对评估集的每条 must_contain 是否真的出现在 gold_doc 里。

为什么单独做这个（run_eval.py 也会查，但它的输出不够细）：
  出题时最容易犯的错就是"我记得文档里写了 X"，实际没写。
  这个脚本一次性列出**所有**题目的核对结果，方便批量修正。

用法：
    & G:\\转型\\.venv\\Scripts\\python.exe ai-lab\\projects\\project2-rag\\eval\\check_questions.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rag.corpus import load_corpus      # noqa: E402


def main() -> int:
    docs = {d.rel_path: d for d in load_corpus()}
    qpath = HERE / "questions.jsonl"
    rows = []
    for line in qpath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        rows.append(json.loads(line))

    print("=" * 78)
    print(f"  评估集核对 · {len(rows)} 条（语料 {len(docs)} 篇）")
    print("=" * 78)

    n_bad = 0
    for q in rows:
        gold = q.get("gold_doc", "")
        mc = q.get("must_contain", "")
        if not gold:
            print(f"  [不可答] {q['id']}  {q['question'][:50]}")
            continue
        doc = docs.get(gold)
        if doc is None:
            n_bad += 1
            print(f"  [❌文档不在语料] {q['id']}  gold_doc={gold!r}")
            # 帮它找找相近的
            base = Path(gold).name
            cands = [k for k in docs if Path(k).name == base]
            if cands:
                print(f"       也许你想要：{cands}")
            continue
        if mc and mc not in doc.text:
            n_bad += 1
            print(f"  [❌找不到 must_contain] {q['id']}  {mc!r}  在 {gold}")
            continue
        print(f"  [✅] {q['id']}  {mc!r:>22}  {gold}")

    print()
    print("=" * 78)
    print(f"  {'❌ ' + str(n_bad) + ' 条需要修' if n_bad else '✅ 全部通过'}")
    print("=" * 78)
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
