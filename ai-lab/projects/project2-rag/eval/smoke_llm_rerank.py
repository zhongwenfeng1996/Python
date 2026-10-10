#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 重排的小规模验证 —— 先确认能用、再看花钱量。

为什么先小规模：一次完整评估是 52 题 × 50 候选 = 2600 次 API 调用。
不先用几条题验证，就是拿真金白银试错。

这个脚本做三件事：
  1. 用 3 条题跑 LLM 重排，看分数分布是否合理（不是全 0 或全 10）
  2. 对比 sparse 与 LLM 重排的命中名次变化
  3. 报出 API 调用次数、缓存命中、错误数 —— 估算全量评估的成本

用法：
    python eval/smoke_llm_rerank.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rag.chunking import chunk_document                       # noqa: E402
from rag.corpus import load_corpus                             # noqa: E402
from rag.embeddings import get_embedder                        # noqa: E402
from rag.llm_rerank import LLMRerankConfig, LLMReranker, LLMScorer  # noqa: E402
from rag.rerank import TwoStageRetriever                       # noqa: E402
from rag.retrieve import Retriever                             # noqa: E402
from rag.store import Store                                    # noqa: E402
from run_eval import load_questions                            # noqa: E402


def rank_of(hits, gold: str) -> int:
    for h in hits:
        if h.source_path == gold:
            return h.rank
    return 0


def main() -> int:
    questions = [q for q in load_questions(HERE / "questions.jsonl") if q.answerable]

    # 各类型各取一条：改写 / 部分改写 / 字面重合
    pick = [q for q in questions if q.id in ("q032", "q006", "q012")]
    if not pick:
        pick = questions[:3]

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "smoke-llm.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    cache = str(HERE.parent / "data" / "llm-score-cache.json")
    scorer = LLMScorer(LLMRerankConfig(
        cache_path=cache,
        model="deepseek-flash",
        concurrency=6,
        max_chars=1000,
    ))
    print("=" * 84)
    print("  LLM 重排 · 小规模验证")
    print("=" * 84)
    print(f"  模型     : {scorer.cfg.model}")
    print(f"  并发     : {scorer.cfg.concurrency}")
    print(f"  缓存文件 : {cache}")
    print(f"  取 {len(pick)} 条题验证")
    print()

    llm_rr = LLMReranker(scorer)

    for q in pick:
        # 第一阶段：与词法重排完全相同的 union 召回（保证可比）
        pool_retriever = TwoStageRetriever(
            retriever, llm_rr, recall_channels="union", recall_k=12)
        pool = pool_retriever.recall(q.question)
        pool = pool[:12]              # 小规模：只取 12 个候选

        sparse_hits = retriever.search(q.question, k=12, mode="sparse")
        s_rank = rank_of(sparse_hits, q.gold_doc)

        # ⚠️ 用 score_many_sync（批量）而不是逐条 ——
        #    踩过：这里原来是逐条调用，和实际评估走的批量路径不一致，
        #    于是"smoke 通过"但全量评估失败（批量才暴露 max_tokens 不够）。
        #    **验证要走和生产一致的路径，否则验证没有意义。**
        scores = scorer.score_many_sync(q.question, [h.text for h in pool])
        order = sorted(range(len(pool)), key=lambda i: -scores[i])
        llm_rank = next((r for r, i in enumerate(order, 1)
                         if pool[i].source_path == q.gold_doc), 0)

        print(f"  ── {q.id}  {q.question[:52]}")
        print(f"     类型: {q.qtype}   候选 {len(pool)} 个")
        print(f"     分数分布: 最高 {max(scores):.0f}  最低 {min(scores):.0f}  "
              f"非零 {sum(1 for s in scores if s > 0)}/{len(scores)}")
        print(f"     gold 名次:  sparse 第 {s_rank}  ->  LLM 第 {llm_rank}   "
              + ("✅ 改善" if 0 < llm_rank < s_rank else
                 ("⚠️ 变差" if llm_rank > s_rank or llm_rank == 0 else "持平")))
        # 看 gold 与最高分块的差距（判断 LLM 是否真的在区分）
        top_i = order[0]
        print(f"     LLM 认为最相关的是: {pool[top_i].source_path}")
        print(f"       gold 是:          {q.gold_doc}")
        print()

    st = scorer.stats
    scorer.save_cache()
    print("─" * 84)
    print(f"  API 调用统计: 缓存命中 {st['cache_hit']}  实际调用 {st['cache_miss']}  "
          f"错误 {st['errors']}")
    per_call = st["cache_miss"]
    print(f"  全量评估预估（52 题 × 50 候选 = 2600 次）:")
    print(f"     首次跑约 2600 次调用；有缓存后重跑几乎为 0")
    print(f"     deepseek-flash 单次约 200 输入 token → 总输入约 52 万 token")
    print()
    print("  ✅ 如果上面的分数不是全 0/全 10，说明打分有效")
    print("  ⚠️ 如果 gold 名次普遍变差，说明提示词或模型需要换")
    print("=" * 84)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
