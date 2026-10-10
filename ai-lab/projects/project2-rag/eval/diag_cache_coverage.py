#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断 LLM 打分的缓存覆盖情况与耗时瓶颈

## 要回答的问题

全量评估跑到 21 分钟仍未完成，而 5 条题只要 15.7 秒。
必须先分清是：
  A. **缓存覆盖不足** —— 大部分查询的候选都没缓存，每次都要真调 API
  B. **某个查询特别贵** —— 候选特别多或特别长，批次被反复折半
  C. **卡死/死循环** —— 根本没在推进

这个脚本用**零 API 调用**的方式回答 A 和 B：
  · 对每条题算出它的候选池，与缓存比对，得出"还需多少次调用"
  · 报出候选池的字符总量分布，找出异常贵的查询

这样不用花钱就能知道全量评估要多久、贵在哪。
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
from rag.retrieve import Retriever                             # noqa: E402
from rag.store import Store                                    # noqa: E402
from run_eval import load_questions                            # noqa: E402


def main() -> int:
    questions = [q for q in load_questions(HERE / "questions.jsonl") if q.answerable]

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "diag-cache.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    cfg = LLMRerankConfig(cache_path=str(HERE.parent / "data" / "llm-score-cache.json"))
    scorer = LLMScorer(cfg)
    cache = scorer._cache  # noqa: SLF001 诊断脚本，直接读

    # 用与 run_eval 完全相同的召回配置
    class DummyRR:
        def rerank(self, q, hits, k):     # noqa: ANN001, ARG002
            return list(hits)

    ts = TwoStageRetriever(retriever, DummyRR(),
                           recall_channels="union", recall_k=50)

    print("=" * 92)
    print("  LLM 打分缓存覆盖诊断（零 API 调用）")
    print("=" * 92)
    print(f"  评估集: {len(questions)} 条可答题")
    print(f"  缓存条数: {len(cache)}")
    print()

    total_needed = 0
    total_have = 0
    worst: list[tuple[int, int, str, int, int]] = []

    for q in questions:
        pool = ts.recall(q.question)
        texts = [h.text for h in pool]
        have = sum(
            1 for t in texts
            if scorer._key(cfg.model, q.question, t) in cache)  # noqa: SLF001
        need = len(texts) - have
        total_needed += need
        total_have += have
        chars = sum(min(len(t), cfg.max_chars) for t in texts)
        worst.append((need, len(texts), q.id, chars, have))

    # 按"还需调用"降序（need 越大越可能慢）
    worst.sort(key=lambda x: -x[0])

    print(f"  已缓存命中: {total_have} 条")
    print(f"  还需真调 API: {total_needed} 条")
    print(f"  按每批 {cfg.batch_size} 个、字符预算 {cfg.char_budget} 估算：")
    # 估算批次数
    est_batches = 0
    for q in questions:
        pool = ts.recall(q.question)
        texts = [t.text for t in pool]
        uncached = [t for t in texts
                    if scorer._key(cfg.model, q.question, t) not in cache]  # noqa: SLF001
        i = 0
        while i < len(uncached):
            chars = 0
            j = i
            while j < len(uncached) and (j - i) < cfg.batch_size:
                if chars + min(len(uncached[j]), cfg.max_chars) > cfg.char_budget and j > i:
                    break
                chars += min(len(uncached[j]), cfg.max_chars)
                j += 1
            est_batches += 1
            i = j
    print(f"    约 {est_batches} 次批量调用")
    print(f"    按每次 1.5 秒、并发 {cfg.concurrency} 估算："
          f" 约 {est_batches * 1.5 / cfg.concurrency / 60:.1f} 分钟")
    print()

    print("  还需调用最多的 12 条题：")
    print(f"  {'id':<7} {'候选':>5} {'已缓存':>6} {'需调用':>6} {'字符总量':>9}")
    print("  " + "-" * 44)
    for need, n, qid, chars, have in worst[:12]:
        print(f"  {qid:<7} {n:>5} {have:>6} {need:>6} {chars:>9}")

    print()
    # 候选池长度分布 —— 解释"为什么不同查询速度差很多"
    lens = []
    for q in questions:
        pool = ts.recall(q.question)
        lens.append(sum(min(len(h.text), cfg.max_chars) for h in pool))
    lens.sort()
    print("  候选池字符总量分布（解释查询间速度差异）:")
    print(f"    最小 {lens[0]}  中位 {lens[len(lens)//2]}  最大 {lens[-1]}")
    print(f"    最大/最小 = {lens[-1]/max(lens[0],1):.1f} 倍")
    print()
    print("=" * 92)
    print("  读法：")
    print("    · '还需真调 API' 很大 → 慢是正常的，等它跑完")
    print("    · 某条题候选字符总量异常大 → 那条题的批次会被折半，特别慢")
    print("    · 若都正常但实际卡住 → 是死循环或网络卡死，需要查代码")
    print("=" * 92)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
