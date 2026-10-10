#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成层冒烟验证 —— 少量问题，人工看回答质量

## 要验证的四件事

1. **引用对不对**：`[n]` 是否真的指向资料里那一段，有没有编造编号
2. **弃答会不会发生**：拿一条**不可答题**试 —— 它必须说"资料中没有相关信息"
3. **会不会混入先验知识**：故意问一个"文档里没有但模型一定知道"的问题
4. **成本**：一次生成花多少 token、多少秒

第 2 和第 3 点是重点。一个从不弃答的 RAG 在生产里是负资产 ——
它会用模型自己的知识把"文档里没有"的问题答得很像样，用户无法分辨。

用法：
    python eval/smoke_generate.py
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
from rag.generate import ABSTAIN_PHRASE, GenerateConfig, Generator  # noqa: E402
from rag.llm_rerank import LLMRerankConfig, LLMReranker, LLMScorer  # noqa: E402
from rag.rerank import TwoStageRetriever                       # noqa: E402
from rag.retrieve import Retriever                             # noqa: E402
from rag.store import Store                                    # noqa: E402
from run_eval import load_questions                            # noqa: E402

# 第 3 类：文档里没有、但模型一定知道的"陷阱题"
# 用来验证它会不会拿先验知识硬答
TRAP_QUESTIONS = [
    "Transformer 的注意力机制时间复杂度是多少？",
    "PyTorch 和 TensorFlow 哪个更适合生产环境？",
]


def main() -> int:
    questions = load_questions(HERE / "questions.jsonl")
    answerable = [q for q in questions if q.answerable]
    unanswerable = [q for q in questions if not q.answerable]

    # 各类型各取一条 + 一条不可答 + 两条陷阱
    pick = []
    for t in ("rewrite", "fact", "cross"):
        for q in answerable:
            if q.qtype == t:
                pick.append(q)
                break

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "smoke-gen.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    scorer = LLMScorer(LLMRerankConfig(
        cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
        batch_size=10, max_chars=400))
    two_stage = TwoStageRetriever(retriever, LLMReranker(scorer),
                                 recall_channels="union", recall_k=15)
    gen = Generator(GenerateConfig(max_passages=5, max_chars=600))

    print("=" * 88)
    print("  生成层冒烟 · 带引用 + 不足则弃答")
    print("=" * 88)
    print(f"  模型: {gen.cfg.model}   送 5 段资料   每段最多 {gen.cfg.max_chars} 字符")
    print(f"  弃答阈值: {gen.cfg.abstain_threshold}（0 = 不按分数弃答，全交给模型判断）")
    print()

    cases: list[tuple[str, str, str]] = []
    for q in pick:
        cases.append((q.id, "可答-" + q.qtype, q.question))
    if unanswerable:
        cases.append((unanswerable[0].id, "**不可答**", unanswerable[0].question))
    for i, tq in enumerate(TRAP_QUESTIONS, 1):
        cases.append((f"trap{i}", "**陷阱题**", tq))

    total_tokens = 0
    for qid, kind, question in cases:
        hits = two_stage.search(question, k=5)
        ans = gen.generate(question, hits)
        total_tokens += ans.usage.get("total_tokens", 0)

        flag = "🚫 弃答" if ans.abstained else ("⚠️ 有非法引用" if ans.invalid_citations else "💬 作答")
        print(f"  ── [{qid}] {kind}  {question[:46]}")
        print(f"     状态: {flag}   引用: {ans.cited or '（无）'}"
              f"   最高分: {ans.top_score:.0f}   tokens: {ans.usage.get('total_tokens')}"
              f"   耗时: {ans.elapsed_s}s")
        if ans.invalid_citations:
            print(f"     ❌ 非法引用编号: {ans.invalid_citations}"
                  f"（资料只有 1~{len(ans.passages)}）")
        # 回答正文（截断显示）
        body = ans.text.replace("\n", " ")
        print(f"     回答: {body[:150]}{'…' if len(body) > 150 else ''}")
        # 列出来源（人工核对引用是否对得上）
        print("     资料编号 -> 来源:")
        for i, h in enumerate(ans.passages, 1):
            mark = "← 被引用" if i in ans.cited else ""
            print(f"       [{i}] {h.source_path} {mark}")
        print()

    print("─" * 88)
    print(f"  合计 tokens: {total_tokens}   生成调用: {gen.calls}")
    print()
    print("  判定标准：")
    print(f"    · 不可答题必须弃答（回答里出现「{ABSTAIN_PHRASE}」）")
    print("    · 陷阱题也应该弃答 —— 如果它答了 Transformer 的复杂度，")
    print("      说明模型在用先验知识，提示词需要加强")
    print("    · 可答题的引用编号必须都落在 1~5，且指向的来源与论断相符")
    print("=" * 88)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
