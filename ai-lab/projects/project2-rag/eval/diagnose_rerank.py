#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断重排为什么没超过纯 sparse（分两段定位问题）

## 为什么要分段诊断

两段式检索有两个可能出问题的环节：
  ① **召回段**：候选池里就没有 gold → 重排再厉害也救不回来
  ② **重排段**：候选池里有 gold，但打分把它排到后面了

不区分这两者，就只能瞎调权重。所以先量：
  · 召回段的 recall@recall_k（gold 有没有进池）
  · 重排后的 recall@5 / MRR
  · gold 在重排前后的名次变化

用法：
    python eval/diagnose_rerank.py
"""

from __future__ import annotations

import sys
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
    """gold 在 hits 里的名次（1-based），0 = 没找到。"""
    for h in hits:
        if h.source_path == gold:
            return h.rank
    return 0


def main() -> int:
    questions = load_questions(HERE / "questions.jsonl")
    answerable = [q for q in questions if q.answerable]

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "diagnose-rerank.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    print("=" * 80)
    print(f"  重排分段诊断 · {len(answerable)} 条可答题  ·  {len(chunks)} 块")
    print("=" * 80)

    # ---------------- 召回段 ----------------
    print("\n【第一段：召回能力】gold 有没有进候选池？\n")
    print(f"  {'召回方式':<22} {'recall@10':>10} {'recall@20':>10} {'recall@50':>10} {'recall@100':>11}")
    print("  " + "-" * 68)

    recall_cfg: dict[str, list[int]] = {}
    for label, mode in (("sparse", "sparse"), ("dense", "dense"),
                        ("union(50+50)", "union")):
        r10 = r20 = r50 = r100 = 0
        for q in answerable:
            if mode == "union":
                # 与 rag/rerank.py 的 recall() 保持一致：**交错**合并，
                # 否则 dense 的重复项会把 sparse 独有的候选挤出池外
                d = retriever.search(q.question, k=50, mode="dense")
                sp = retriever.search(q.question, k=50, mode="sparse")
                merged = []
                seen = set()
                for i in range(max(len(d), len(sp))):
                    for hits_ in (d, sp):
                        if i < len(hits_):
                            h = hits_[i]
                            if h.chunk_id not in seen:
                                seen.add(h.chunk_id)
                                merged.append(h)
                # union 没有统一排名，按下标当名次
                hits = [type(h)(chunk_id=h.chunk_id, score=h.score,
                               source_path=h.source_path, heading_path=h.heading_path,
                               text=h.text, rank=i + 1, channel=h.channel)
                        for i, h in enumerate(merged)]
            else:
                hits = retriever.search(q.question, k=100, mode=mode)
            r = rank_of(hits, q.gold_doc)
            if r and r <= 10: r10 += 1
            if r and r <= 20: r20 += 1
            if r and r <= 50: r50 += 1
            if r: r100 += 1
        n = len(answerable)
        print(f"  {label:<22} {r10/n:>10.4f} {r20/n:>10.4f} {r50/n:>10.4f} {r100/n:>11.4f}")
        recall_cfg[label] = [r10, r20, r50, r100]

    # ---------------- 重排段 ----------------
    print("\n【第二段：重排能力】池里有 gold 时，重排把它排到了哪？\n")

    for wname, w in (
        ("默认 overlap1.0 head0.8 prox0.3", RerankWeights(1.0, 0.8, 0.3, 0.0)),
        ("只 overlap", RerankWeights(1.0, 0.0, 0.0, 0.0)),
        ("overlap+heading", RerankWeights(1.0, 0.8, 0.0, 0.0)),
        ("overlap+proximity", RerankWeights(1.0, 0.0, 0.3, 0.0)),
        ("调高 heading", RerankWeights(1.0, 2.0, 0.3, 0.0)),
        ("调低 heading", RerankWeights(1.0, 0.3, 0.3, 0.0)),
    ):
        rr = LexicalReranker([c.text for c in chunks], weights=w)
        ts = TwoStageRetriever(retriever, rr, recall_channels="union", recall_k=50)

        n_hit5 = 0
        n_hit1 = 0
        mrr = 0.0
        in_pool_but_lost = 0
        ranks_before: list[int] = []
        ranks_after: list[int] = []

        for q in answerable:
            pool = ts.recall(q.question)
            before = rank_of(pool, q.gold_doc)
            final = ts.search(q.question, k=5)
            after = rank_of(final, q.gold_doc)

            if before:
                ranks_before.append(before)
                if after:
                    n_hit5 += 1
                    mrr += 1.0 / after
                    if after == 1:
                        n_hit1 += 1
                else:
                    in_pool_but_lost += 1
                ranks_after.append(after)
            else:
                ranks_before.append(0)
                ranks_after.append(0)

        n = len(answerable)
        med_before = sorted(x for x in ranks_before if x)[len([x for x in ranks_before if x]) // 2] \
            if any(ranks_before) else 0
        print(f"  {wname:<34} recall@5={n_hit5/n:.4f}  MRR={mrr/n:.4f}  "
              f"hit@1={n_hit1/n:.4f}  池中丢失={in_pool_but_lost}  池内中位名次={med_before}")

    # ---------------- 逐题看：哪些题被重排害了 ----------------
    print("\n【第三段：谁被重排排后了】用默认权重的逐题对比\n")
    rr = LexicalReranker([c.text for c in chunks], weights=RerankWeights(1.0, 0.8, 0.3, 0.0))
    ts = TwoStageRetriever(retriever, rr, recall_channels="union", recall_k=50)

    worsened: list[tuple[str, str, int, int]] = []
    improved: list[tuple[str, str, int, int]] = []
    for q in answerable:
        base = rank_of(retriever.search(q.question, k=100, mode="sparse"), q.gold_doc)
        final = rank_of(ts.search(q.question, k=100), q.gold_doc)
        if base and final:
            if final > base:
                worsened.append((q.id, q.question, base, final))
            elif final < base:
                improved.append((q.id, q.question, base, final))

    print(f"  被重排改善的: {len(improved)} 条")
    for qid, qs, b, f in improved[:8]:
        print(f"    {qid}  名次 {b} -> {f}   {qs[:46]}")
    print(f"\n  被重排弄差的: {len(worsened)} 条")
    for qid, qs, b, f in worsened[:8]:
        print(f"    {qid}  名次 {b} -> {f}   {qs[:46]}")

    store.close()
    print("\n" + "=" * 80)
    print("  读法：")
    print("    · 如果召回段的 recall@50 不到 1.0 → 问题在召回，重排救不了")
    print("    · 如果召回段是 1.0 但重排 recall@5 掉了 → 问题在重排打分")
    print("    · '池中丢失' 数 = 池里有 gold 但重排后掉出 top5 的题数")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
