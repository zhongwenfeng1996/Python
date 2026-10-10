#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评估集质量审计 —— 量一下"题面有多依赖字面词匹配"

## 为什么要做这个

现状：sparse（词法）的 recall@5 = 1.0000，看起来完美。
但要问一句：**是检索好，还是题太容易？**

如果题面用词与文档高度重合（问"flush"，文档里就写着 flush），
那词法方法当然满分 —— 这种评估集**无法暴露真实短板**，
也无法为将来接入真实 embedding 提供有意义的对照基线。

这个脚本量化两件事：
  1. 每条题的**查询词有多少比例出现在 gold 文档里**（字面重合率）
  2. 哪些题属于"改写问句"（问题的关键词与文档用词不同义）

## 用法

    python eval/audit_questions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rag.corpus import load_corpus          # noqa: E402
from rag.embeddings import _ngrams          # noqa: E402
from run_eval import load_questions         # noqa: E402


def main() -> int:
    questions = load_questions(HERE / "questions.jsonl")
    docs = {d.rel_path: d for d in load_corpus()}
    answerable = [q for q in questions if q.answerable]

    print("=" * 92)
    print(f"  评估集审计 · {len(questions)} 条（可答 {len(answerable)} / "
          f"不可答 {len(questions) - len(answerable)}）")
    print("=" * 92)

    rows = []
    for q in answerable:
        doc = docs.get(q.gold_doc)
        if doc is None:
            rows.append((q.id, q.question, -1.0, q.qtype, "gold 不在语料"))
            continue
        qg = set(_ngrams(q.question))
        dg = set(_ngrams(doc.text))
        # 只看查询侧：问题的 n-gram 有多少比例在 gold 文档里出现
        overlap = len(qg & dg) / max(len(qg), 1)
        rows.append((q.id, q.question, overlap, q.qtype, ""))

    rows.sort(key=lambda r: r[2])

    print(f"\n  {'id':<6} {'重合率':>7}  {'类型':<12} 问题")
    print("  " + "-" * 86)
    for qid, qs, ov, qt, note in rows:
        flag = ""
        if ov < 0:
            flag = "⚠️"
        elif ov < 0.35:
            flag = "🔴 改写"
        elif ov < 0.55:
            flag = "🟡 部分改写"
        else:
            flag = "🟢 字面重合"
        print(f"  {qid:<6} {ov:>7.3f}  {qt:<12} {flag} {qs[:44]}")
        if note:
            print(f"         {note}")

    # 分桶统计
    literal = [r for r in rows if r[2] >= 0.55]
    partial = [r for r in rows if 0.35 <= r[2] < 0.55]
    rewrite = [r for r in rows if 0 <= r[2] < 0.35]
    n = len(rows)

    print("\n  " + "-" * 86)
    print("  分桶：")
    print(f"    字面重合 (≥0.55)  : {len(literal):>3} 条 ({len(literal)/n*100:>5.1f}%)")
    print(f"    部分改写 (0.35~0.55): {len(partial):>3} 条 ({len(partial)/n*100:>5.1f}%)")
    print(f"    改写问句 (<0.35)  : {len(rewrite):>3} 条 ({len(rewrite)/n*100:>5.1f}%)")
    print()
    print("  " + "=" * 88)
    if len(rewrite) / n < 0.25:
        print("  ⚠️ 诊断：改写问句占比过低（<25%）。")
        print("     后果：sparse 会显得近乎满分，**掩盖词法方案的真实短板**，")
        print("           也无法为将来接入真实 embedding 提供有意义的对照。")
        print("     建议：补充「用不同措辞问同一件事」的题。")
    else:
        print("  ✅ 改写问句占比合理，评估集能区分词法与语义方案。")
    print("  " + "=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
