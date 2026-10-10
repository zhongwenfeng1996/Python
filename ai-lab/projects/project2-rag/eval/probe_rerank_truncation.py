#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证：把重排打分的截断从 400 放宽，B 类能否排上来？

## 假设（有强证据）

上一版诊断（probe_missing_answer.py）发现：

    B 类（池里有但没排进前 5）11 条，它们的**目标块 100% 超过 400 字符**，
    平均 582 字符；而全部块的中位长度是 399 字符。

而重排打分时 `max_chars=400` —— 也就是**约一半的块会被截断**，
含答案的那些（偏长）答案常在后半段，**LLM 根本没看到答案**，
自然给低分。

这个参数是我当初扫出来的，但扫描标准是"**哪个组合最省 token**"
（见 ADR-009 的参数网格），**没有用"哪个组合最准"**。
这是个方法论错误：省 token 只有在**不损失效果**时才值得。

## 这个脚本怎么做（成本控制）

不去重跑全量（那要 48 分钟）。只做一件有针对性的事：

  对 B 类那 11 条题，把它们的候选池用**更大的 max_chars** 重新打分，
  然后看目标块的名次是否上升。

用到的技巧：
  · 只重打**没缓存的**那部分（缓存键含段落原文，截断变了就是新键）
  · 只跑 11 条题而不是 52 条
  · 候选池限制在 30 个（够看名次变化，不必全池）

用法：
    python eval/probe_rerank_truncation.py
    python eval/probe_rerank_truncation.py --max-chars 1200 --pool 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from rag.chunking import chunk_document                       # noqa: E402
from rag.corpus import load_corpus                             # noqa: E402
from rag.embeddings import get_embedder                        # noqa: E402
from rag.llm_rerank import LLMRerankConfig, LLMScorer          # noqa: E402
from rag.rerank import TwoStageRetriever                       # noqa: E402
from rag.retrieve import Retriever                             # noqa: E402
from rag.store import Store                                    # noqa: E402
from run_eval import load_questions                            # noqa: E402

# 上一版诊断找出的 B 类（池里有、但重排没排进前 5）
B_CLASS = ["q040", "q055", "q004", "q017", "q042", "q041",
           "q022", "q007", "q020", "q051", "q050"]


class _Noop:
    def rerank(self, query, hits, top_k):  # noqa: ANN001, ARG002
        return list(hits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-chars", type=int, default=1500)
    ap.add_argument("--pool", type=int, default=30)
    ap.add_argument("--batch", type=int, default=10)
    args = ap.parse_args()

    questions = {q.id: q for q in load_questions(HERE / "questions.jsonl")
                 if q.answerable}
    targets = [questions[i] for i in B_CLASS if i in questions]

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "probe-trunc-rr.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)
    ts = TwoStageRetriever(retriever, _Noop(), recall_channels="union", recall_k=50)

    print("=" * 100)
    print("  验证：放宽打分截断能否让 B 类排上来")
    print("=" * 100)
    print(f"  对比两种打分截断: 旧 400  vs  新 {args.max_chars}")
    print(f"  只测 B 类 {len(targets)} 条；候选池取前 {args.pool}")
    print()
    print("  ⚠️ 缓存键含段落原文，所以换了截断就是**新键**，必然真调 API")
    print()

    cache_path = str(HERE.parent / "data" / "llm-score-cache.json")
    old = LLMScorer(LLMRerankConfig(cache_path=cache_path,
                                    batch_size=args.batch, max_chars=400))
    new = LLMScorer(LLMRerankConfig(cache_path=cache_path,
                                    batch_size=args.batch,
                                    max_chars=args.max_chars))
    old_cache = old._cache  # noqa: SLF001

    print(f"  {'id':<7} {'旧名次':>7} {'新名次':>7} {'变化':>6}  块长  问题")
    print("  " + "-" * 88)

    improved = same = worse = 0
    old_ranks, new_ranks = [], []
    for q in targets:
        pool = ts.recall(q.question)[: args.pool]

        # 目标块 = 含 must_contain 的那个
        tgt = next((i for i, h in enumerate(pool)
                    if q.must_contain and q.must_contain in h.text), None)
        if tgt is None:
            print(f"  {q.id:<7} {'—':>7} {'—':>7}   池里无目标块（前 {args.pool}）")
            continue

        # 旧分数（来自既有缓存，400 截断）
        olds = [old_cache.get(old._key("deepseek-flash", q.question, h.text))  # noqa: SLF001
                for h in pool]
        # 新分数（用更大截断重新打分）
        news = new.score_many_sync(q.question, [h.text for h in pool])

        def rank_of(scores, k):  # noqa: ANN001
            order = sorted(range(len(pool)),
                           key=lambda i: -(scores[i] if scores[i] is not None else -1))
            return next((r for r, i in enumerate(order, 1) if i == k), 0)

        r_old = rank_of(olds, tgt)
        r_new = rank_of(news, tgt)
        old_ranks.append(r_old)
        new_ranks.append(r_new)

        delta = r_old - r_new
        if delta > 0:
            improved += 1
            mark = f"↑{delta}"
        elif delta == 0:
            same += 1
            mark = "—"
        else:
            worse += 1
            mark = f"↓{-delta}"
        print(f"  {q.id:<7} {r_old:>7} {r_new:>7} {mark:>6}  "
              f"{len(pool[tgt].text):>5}  {q.question[:36]}")

    new.save_cache()
    st = new.stats

    print()
    print("─" * 100)
    print(f"  改善 {improved} 条 / 持平 {same} 条 / 变差 {worse} 条")
    if old_ranks:
        print(f"  旧名次: {old_ranks}  中位 {sorted(old_ranks)[len(old_ranks)//2]}")
        print(f"  新名次: {new_ranks}  中位 {sorted(new_ranks)[len(new_ranks)//2]}")
        in5_old = sum(1 for r in old_ranks if 0 < r <= 5)
        in5_new = sum(1 for r in new_ranks if 0 < r <= 5)
        print(f"  进入前 5 的: 旧 {in5_old}/{len(old_ranks)}  ->  "
              f"新 {in5_new}/{len(old_ranks)}")
    print()
    print(f"  本次真调 API: {st['cache_miss']} 次   命中缓存: {st['cache_hit']} 次"
          f"   错误: {st['errors']}")
    print()
    print("=" * 100)
    print("  判定")
    print("=" * 100)
    print(f"    · 若新名次明显上升 → 截断是主因，应把 max_chars 调大（{args.max_chars} 左右）")
    print("      代价：prompt token 增加（每块约 400→1500 字符），成本上升")
    print("      —— 但这是**效果换成本**，方向正确；当初只为省 token 调参是错的")
    print("    · 若名次没变 → 截断不是主因，得考虑别的原因（embedding 弱、块切得不好）")
    print("=" * 100)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
