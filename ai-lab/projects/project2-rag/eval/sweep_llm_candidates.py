#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 重排的成本-效果曲线 —— 候选池大小该取多少？

## 为什么要测这个

全量评估跑一次要 **48 分钟**（2873 秒）。原因是每条查询的候选池约 95 个
（dense@50 + sparse@50 的并集），52 条题 = 4000+ 次打分。

但召回段的数据显示：
    recall@10 = 0.9423
    recall@20 = 0.9423
    recall@50 = 1.0000
gold 在 10~20 名内就能覆盖 94% 的题。**是不是没必要给 LLM 看 95 个？**

这个脚本用**已缓存的打分**（零 API 调用）重算不同候选池下的指标，
画出成本-效果曲线，回答"取多少最划算"。

⚠️ 注意口径：这里是从**同一个池子**里截前 N 个候选，
所以衡量的是"**如果只把前 N 个送给 LLM，效果会怎样**"。
如果实际生产中把候选池也缩小到 N，召回段会少一些 gold（recall@N 那行），
两者结合才是真实效果。所以脚本把两个数都打出来。

用法：
    python eval/sweep_llm_candidates.py
"""

from __future__ import annotations

import json
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
from rag.retrieve import Hit, Retriever                        # noqa: E402
from rag.store import Store                                    # noqa: E402
from run_eval import load_questions                            # noqa: E402


class _Noop:
    """占位重排器：只要召回结果，不要重排。"""

    def rerank(self, query, hits, top_k):  # noqa: ANN001, ARG002
        return list(hits)


def main() -> int:
    questions = [q for q in load_questions(HERE / "questions.jsonl") if q.answerable]
    n = len(questions)

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "sweep-llm.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    cfg = LLMRerankConfig(cache_path=str(HERE.parent / "data" / "llm-score-cache.json"))
    scorer = LLMScorer(cfg)
    cache = scorer._cache  # noqa: SLF001

    ts = TwoStageRetriever(retriever, _Noop(), recall_channels="union", recall_k=50)

    # 预算所有查询的候选池 + 缓存的分数（零 API 调用）
    data: list[tuple[object, list[Hit], list[float | None]]] = []
    missing = 0
    for q in questions:
        pool = ts.recall(q.question)
        scores: list[float | None] = []
        for h in pool:
            s = cache.get(scorer._key(cfg.model, q.question, h.text))  # noqa: SLF001
            if s is None:
                missing += 1
            scores.append(s)
        data.append((q, pool, scores))

    print("=" * 96)
    print("  LLM 重排的候选池大小 — 成本/效果曲线（用已缓存打分，零 API 调用）")
    print("=" * 96)
    print(f"  评估集: {n} 条可答题  缓存: {len(cache)} 条  缺分: {missing} 条")
    print()
    print(f"  {'候选上限':>8} {'recall池':>9} {'recall@5':>9} {'MRR':>8} {'hit@1':>8} "
          f"{'打分次数':>9} {'vs最小':>7} {'vs全池':>7}")
    print("  " + "-" * 86)

    baseline_calls = None
    full_calls = None
    rows: list[tuple] = []
    for cap in (5, 10, 15, 20, 30, 40, 50, 70, 95):
        rec_pool = r5 = mrr = h1 = 0.0
        calls = 0
        for q, pool, scores in data:
            sub = [s for s in scores[:cap] if s is not None]
            sub_hits = pool[:cap]
            calls += len(sub_hits)
            # 召回（只看池子里有没有 gold）
            if any(h.source_path == q.gold_doc for h in sub_hits):
                rec_pool += 1
            # 重排后取前 5
            order = sorted(range(len(sub_hits)), key=lambda i: -(sub[i] if i < len(sub) else 0.0))
            top5 = [sub_hits[i] for i in order[:5]]
            for r, h in enumerate(top5, 1):
                if h.source_path == q.gold_doc:
                    r5 += 1
                    mrr += 1.0 / r
                    if r == 1:
                        h1 += 1
                    break
        if baseline_calls is None:
            baseline_calls = calls
        rows.append((cap, rec_pool / n, r5 / n, mrr / n, h1 / n, calls))

    # 全池（95）作为"最贵"的参照
    full_calls = rows[-1][5]
    for cap, rp, r5, mr, hh, calls in rows:
        print(f"  {cap:>8} {rp:>9.4f} {r5:>9.4f} {mr:>8.4f} "
              f"{hh:>8.4f} {calls:>9} {calls/baseline_calls:>6.1f}x "
              f"{calls/full_calls:>6.0%}")

    print()
    print("  " + "=" * 92)
    print("  读法：")
    print("    · 找 MRR/hit@1 基本饱和的位置 —— 再往上加候选是浪费")
    print("    · '相对成本'是按打分次数算的，与 API 花费成正比")
    print("    · recall池 这一列是**上限**：池子里没有 gold，重排再强也救不回来")
    print("  " + "=" * 92)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
