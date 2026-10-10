#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
逐题对比"被排在前面的块"和"含答案的块"——看清 LLM 到底在给什么打分

## 为什么必须看这个

前面的诊断只给出了名次，没给出**理由**。要判断问题在哪，
必须看到：排在前 5 的那些块**长什么样**，以及含答案的块**差在哪**。

看的时候要回答三个具体问题：

  1. 排前面的块是不是**话题相关但不回答问题的**？
     （如果是 → LLM 把"话题相关"当成了"能回答"，这是它的判分粒度问题）
  2. 含答案的块是不是**答案只占很小一部分**、其余是别的内容？
     （如果是 → 块切得太粗，答案被稀释；该改切块而不是改重排）
  3. 含答案的块的 must_contain 是不是**只在标题里**，正文没展开？
     （如果是 → 题目本身依赖标题信息，而标题没进候选的正文）

## 为什么这个诊断值得做

它决定"下一步该修什么"：
  · 问题在判分粒度 → 改提示词（要求"能否直接回答问题"而不是"是否相关"）
  · 问题在块太粗   → 改切块策略（节内再细分）
  · 问题在标题信息 → 把 heading_path 也拼进送进模型的文本

三种修法完全不同，不能靠猜。

用法：
    python eval/probe_rank_compare.py q050 q051 q020
"""

from __future__ import annotations

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

DEFAULT_IDS = ["q050", "q051", "q020"]


class _Noop:
    def rerank(self, query, hits, top_k):  # noqa: ANN001, ARG002
        return list(hits)


def main() -> int:
    ids = sys.argv[1:] or DEFAULT_IDS
    questions = {q.id: q for q in load_questions(HERE / "questions.jsonl")}

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "probe-rank.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)
    ts = TwoStageRetriever(retriever, _Noop(), recall_channels="union", recall_k=50)

    cfg = LLMRerankConfig(cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
                          batch_size=10, max_chars=400)
    scorer = LLMScorer(cfg)
    cache = scorer._cache  # noqa: SLF001

    for qid in ids:
        q = questions.get(qid)
        if q is None:
            print(f"  ⚠️ 找不到 {qid}")
            continue

        pool = ts.recall(q.question)
        scores = [cache.get(scorer._key(cfg.model, q.question, h.text))  # noqa: SLF001
                  for h in pool]
        order = sorted(range(len(pool)),
                       key=lambda i: -(scores[i] if scores[i] is not None else -1))
        tgt = next((i for i in order
                    if q.must_contain and q.must_contain in pool[i].text), None)

        print("=" * 100)
        print(f"  {qid}   {q.question}")
        print(f"  期望答案: {q.answer[:80]}")
        print(f"  must_contain: {q.must_contain!r}   gold: {q.gold_doc}")
        print(f"  缓存分数缺失: {sum(1 for s in scores if s is None)}/{len(pool)}")
        print()

        print("  【排在前 5 的块】")
        for r, i in enumerate(order[:5], 1):
            h = pool[i]
            has = "✅含 may_contain" if (q.must_contain and q.must_contain in h.text) else ""
            print(f"    #{r} 分数={scores[i]:.0f}  长度={len(h.text)}  "
                  f"{h.source_path}")
            print(f"      标题: {h.heading_path[:70]}")
            print(f"      正文: {h.text[:200].replace(chr(10), ' ')}")
            if has:
                print(f"      {has}")
            print()

        if tgt is not None:
            rank = order.index(tgt) + 1
            h = pool[tgt]
            pos = h.text.find(q.must_contain)
            print(f"  【含答案的块】名次 {rank}   分数 {scores[tgt]:.0f}   "
                  f"长度 {len(h.text)}")
            print(f"      {h.source_path}  >  {h.heading_path[:60]}")
            print(f"      must_contain 在块内位置: {pos} / {len(h.text)}")
            print(f"      正文: {h.text[:260].replace(chr(10), ' ')}")
            print(f"      ...（答案附近）: "
                  f"{h.text[max(0, pos-60):pos+90].replace(chr(10), ' ')}")
        else:
            print("  【含答案的块】不在召回池里")
        print()

    print("=" * 100)
    print("  看的时候问三个问题：")
    print("    1. 排前面的块是「话题相关但不回答问题」，还是「其实也能回答」？")
    print("    2. 含答案的块里，答案占多大比例？其余是不是无关内容（块太粗）？")
    print("    3. must_contain 是不是只在标题里、正文没展开？")
    print("=" * 100)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
