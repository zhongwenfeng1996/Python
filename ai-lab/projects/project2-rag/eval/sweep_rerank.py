#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重排权重扫描 —— 用同一套评估集，系统地试参数组合。

## 为什么要扫描而不是手调

手调的问题：改一个参数、跑一次、看到数字变好就以为找到了。
但评估集只有 26 条可答题，**单条题的得失就能让 recall@5 变动 0.038**
（1/26）。所以必须：

  · 一次跑完整网格，看趋势而不是看单点
  · 同时报 recall@5 和 hit@1（它们是**权衡关系**，不能只看一个）
  · 记录每个配置对应的"池中丢失"题数 —— 定位是哪几条题在掉

## 用法

    python eval/sweep_rerank.py
"""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rag.chunking import chunk_document                    # noqa: E402
from rag.corpus import load_corpus                          # noqa: E402
from rag.embeddings import get_embedder                     # noqa: E402
from rag.rerank import LexicalReranker, RerankWeights, TwoStageRetriever  # noqa: E402
from rag.retrieve import Retriever                          # noqa: E402
from rag.store import Store                                 # noqa: E402
from run_eval import load_questions                         # noqa: E402


def rank_of(hits, gold: str) -> int:
    for h in hits:
        if h.source_path == gold:
            return h.rank
    return 0


def main() -> int:
    questions = [q for q in load_questions(HERE / "questions.jsonl") if q.answerable]
    n = len(questions)

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))
    texts = [c.text for c in chunks]

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "sweep.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    print("=" * 92)
    print(f"  重排权重扫描 · {n} 条可答题 · {len(chunks)} 块 · k=5")
    print("=" * 92)

    # 基线（不重排）
    print("\n  基线（不重排）:")
    for mode in ("dense", "sparse", "hybrid"):
        r5 = mrr = h1 = 0.0
        for q in questions:
            hits = retriever.search(q.question, k=5, mode=mode)
            rk = rank_of(hits, q.gold_doc)
            if rk:
                r5 += 1
                mrr += 1.0 / rk
                if rk == 1:
                    h1 += 1
        print(f"    {mode:<10} recall@5={r5/n:.4f}  MRR={mrr/n:.4f}  hit@1={h1/n:.4f}")

    # 网格
    print(f"\n  {'overlap':>8} {'heading':>8} {'prox':>6} | {'recall@5':>9} {'MRR':>8} "
          f"{'hit@1':>8} | {'池中丢失':>8} {'池内中位':>8}")
    print("  " + "-" * 88)

    best_recall = (0.0, None)
    best_mrr = (0.0, None)
    best_h1 = (0.0, None)

    for w_ov, w_hd, w_px in product([1.0], [0.0, 0.3, 0.8, 1.5, 2.5], [0.0, 0.2]):
        reranker = LexicalReranker(texts, weights=RerankWeights(w_ov, w_hd, w_px, 0.0))
        ts = TwoStageRetriever(retriever, reranker, recall_channels="union", recall_k=50)

        r5 = mrr = h1 = 0.0
        lost = 0
        in_pool_ranks: list[int] = []
        for q in questions:
            pool = ts.recall(q.question)
            before = rank_of(pool, q.gold_doc)
            if before:
                in_pool_ranks.append(before)
            final = ts.search(q.question, k=5)
            rk = rank_of(final, q.gold_doc)
            if rk:
                r5 += 1
                mrr += 1.0 / rk
                if rk == 1:
                    h1 += 1
            elif before:
                lost += 1

        rec, mr, hh = r5 / n, mrr / n, h1 / n
        med = sorted(in_pool_ranks)[len(in_pool_ranks) // 2] if in_pool_ranks else 0
        print(f"  {w_ov:>8.1f} {w_hd:>8.1f} {w_px:>6.1f} | {rec:>9.4f} {mr:>8.4f} "
              f"{hh:>8.4f} | {lost:>8} {med:>8}")

        if rec > best_recall[0]:
            best_recall = (rec, (w_ov, w_hd, w_px))
        if mr > best_mrr[0]:
            best_mrr = (mr, (w_ov, w_hd, w_px))
        if hh > best_h1[0]:
            best_h1 = (hh, (w_ov, w_hd, w_px))

    print("  " + "-" * 88)
    print(f"\n  最高 recall@5 : {best_recall[0]:.4f}  权重 {best_recall[1]}")
    print(f"  最高 MRR      : {best_mrr[0]:.4f}  权重 {best_mrr[1]}")
    print(f"  最高 hit@1    : {best_h1[0]:.4f}  权重 {best_h1[1]}")
    print()
    # 「1 条题值多少」必须**动态算**，不能写死。
    # 踩过的坑：评估集从 30 条扩到 56 条后，这里还印着旧数字（1 条 = 0.0385），
    # 于是判断标准整个错位 —— 实际 1 条题只值 1/52 ≈ 0.019，
    # 很多"看起来是改进"的差异其实就在噪声里。
    per_q = 1.0 / n if n else 0.0
    print(f"  注意：{n} 条题，**1 条题的得失 = {per_q:.4f}**。")
    print(f"        所以小于 {per_q:.3f} 的差异不能当成'改进'，那可能只是巧合。")
    print("=" * 92)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
