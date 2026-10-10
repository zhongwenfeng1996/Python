#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抽查单条题的生成失败原因 —— 看模型看到的资料、它的回答、以及答案在哪

## 为什么需要这个

`run_generation.py` 只给出汇总指标。要修某一条具体的失败题，
必须看到**模型实际收到的那 5 段资料**，以及**含答案的那一段长什么样**。

这样能区分：
  · 答案段在资料里，但被其他段"淹没"（排序问题）
  · 答案段在资料里，但答案埋在长段中间（模型没读到）
  · 答案段在资料里，但表述与问题差太远（语义鸿沟）

用法：
    python eval/inspect_case.py q017
    python eval/inspect_case.py q017 --recall-k 50
"""

from __future__ import annotations

import argparse
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
from rag.generate import GenerateConfig, Generator             # noqa: E402
from rag.llm_rerank import LLMRerankConfig, LLMReranker, LLMScorer  # noqa: E402
from rag.rerank import TwoStageRetriever                       # noqa: E402
from rag.retrieve import Retriever                             # noqa: E402
from rag.store import Store                                    # noqa: E402
from run_eval import load_questions                            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("qid")
    ap.add_argument("--recall-k", type=int, default=50)
    ap.add_argument("--max-passages", type=int, default=5)
    ap.add_argument("--no-generate", action="store_true",
                    help="只看检索，不调生成模型（省钱）")
    args = ap.parse_args()

    q = next((x for x in load_questions(HERE / "questions.jsonl")
              if x.id == args.qid), None)
    if q is None:
        print(f"❌ 找不到 {args.qid}")
        return 1

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "inspect.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    scorer = LLMScorer(LLMRerankConfig(
        cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
        batch_size=10, max_chars=400))
    ts = TwoStageRetriever(retriever, LLMReranker(scorer),
                           recall_channels="union", recall_k=args.recall_k)

    print("=" * 100)
    print(f"  抽查 {q.id}   [{q.qtype}]   可答={q.answerable}")
    print("=" * 100)
    print(f"  问题      : {q.question}")
    print(f"  期望答案  : {q.answer}")
    print(f"  must_contain: {q.must_contain!r}")
    print(f"  gold_doc  : {q.gold_doc}")
    print()

    hits = ts.search(q.question, k=args.max_passages)
    print(f"  【送给模型的 {len(hits)} 段资料】（已按 LLM 重排分降序）")
    for i, h in enumerate(hits, 1):
        has = ""
        if q.must_contain and q.must_contain in h.text:
            has = f"   ⬅ **含 must_contain（位置 {h.text.find(q.must_contain)}）**"
        print(f"    [{i}] 分数={h.score:.0f}  长度={len(h.text)}  {h.source_path}{has}")
        print(f"        > {h.heading_path[:78]}")
        print(f"        {h.text[:220].replace(chr(10), ' ')}")
        print()

    # 含答案的块在不在？在整池里排第几？
    pool = ts.recall(q.question)
    scores = [0.0] * len(pool)   # 占位，实际用 hits 的顺序即可
    tgt_in_hits = next((i for i, h in enumerate(hits, 1)
                        if q.must_contain and q.must_contain in h.text), None)
    print(f"  【含答案的块】"
          + (f"在送出的资料里，是第 {tgt_in_hits} 段" if tgt_in_hits
             else "**不在送出的资料里**"))
    print()

    if not args.no_generate:
        gen = Generator(GenerateConfig(
            abstain_threshold=3.0, max_passages=args.max_passages))
        a = gen.generate(q.question, hits)
        print("  【模型的实际输出】")
        print(f"    状态: {'🚫 弃答' if a.abstained else '💬 作答'}   "
              f"引用={a.cited}   最高分={a.top_score:.0f}   "
              f"tokens={a.usage.get('total_tokens')}")
        print(f"    ── 回答 ──")
        for line in a.text.split("\n"):
            print(f"    {line}")
        print()

    print("=" * 100)
    print("  读法")
    print("=" * 100)
    print("    · 含答案的块在资料里、模型仍弃答 → 生成层问题（提示词或材料组织）")
    print("    · 含答案的块不在资料里 → 检索问题（该加大 recall_k 或换 embedding）")
    print("    · 看答案在块内/块内的位置：如果埋在长段中间，可考虑把该段拆出来优先送")
    print("=" * 100)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
