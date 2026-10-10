#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断 B 类误弃答：答案在资料里，模型却说没有（零 API 调用）

## 假设

B 类 5 条**全部是 rewrite 类型**，而且分数不低（3~9）。
一个很具体的原因值得先查：**资料被截断了**。

整条链路有两处截断：
  · 重排打分时 `max_chars=400`（省 token，扫描出来的）
  · 生成时 `max_chars=600`

如果 gold 块很长（比如 1000 字符），而**答案在后半段**，
那么送进模型的那 600 字符里根本没有答案 —— 模型说"资料中没有相关信息"
就是**正确的**，问题出在我们截断得太狠。

这个脚本零 API 调用地验证：对每条 B 类题，检查 gold 块的长度，
以及答案关键词是否落在被截断的部分。

用法：
    python eval/probe_truncation.py
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
from rag.llm_rerank import LLMRerankConfig, LLMReranker, LLMScorer  # noqa: E402
from rag.rerank import TwoStageRetriever                       # noqa: E402
from rag.retrieve import Retriever                             # noqa: E402
from rag.store import Store                                    # noqa: E402
from run_eval import load_questions                            # noqa: E402


def main() -> int:
    d = json.loads((HERE.parent / "data" / "generation-results.json")
                   .read_text(encoding="utf-8"))
    recs = {r["id"]: r for r in d["records"]}
    questions = {q.id: q for q in load_questions(HERE / "questions.jsonl")}

    # B 类：可答、弃答、且 gold 在资料里
    b_ids = [r["id"] for r in d["records"]
             if r["answerable"] and r["abstained"] and r["gold_in_context"]]

    docs = load_corpus()
    chunks = []
    for doc in docs:
        chunks.extend(chunk_document(doc.text, doc.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "probe-trunc.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)
    scorer = LLMScorer(LLMRerankConfig(
        cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
        batch_size=10, max_chars=400))
    ts = TwoStageRetriever(retriever, LLMReranker(scorer),
                           recall_channels="union", recall_k=15)

    print("=" * 96)
    print("  B 类误弃答诊断 —— 是不是资料被截断了？")
    print("=" * 96)
    print(f"  B 类共 {len(b_ids)} 条: {b_ids}")
    print()
    print("  两个截断点：重排打分 400 字符 / 生成送料 600 字符")
    print()

    for qid in b_ids:
        q = questions[qid]
        rec = recs[qid]
        hits = ts.search(q.question, k=5)

        print(f"  ── {qid} [{q.qtype}] {q.question[:52]}")
        print(f"     期望答案: {q.answer[:88]}")
        print(f"     must_contain: {q.must_contain!r}")

        # 找到 gold 那一段在资料里的位置与长度
        gold_hits = [h for h in hits if h.source_path == q.gold_doc]
        if not gold_hits:
            print("     ⚠️ 资料里没有 gold（这与 gold_in_context=True 矛盾）")
            print()
            continue
        g = gold_hits[0]
        full_len = len(g.text)
        sent_len = min(full_len, 600)       # 生成时实际送进去的长度
        print(f"     gold 块全长: {full_len} 字符；实际送进模型: {sent_len} 字符")
        print(f"     截断丢失: {full_len - sent_len} 字符"
              + ("  ← 答案可能在丢失部分！" if full_len > sent_len else "  （没截断）"))

        # must_contain 在送进去的部分里吗？
        mc = q.must_contain or ""
        if mc:
            pos_full = g.text.find(mc)
            in_sent = mc in g.text[:sent_len]
            print(f"     must_contain {mc!r} 位置: {pos_full}  "
                  f"-> " + ("✅ 在送进去的部分里" if in_sent
                            else "❌ **被截断丢掉了**"))
        print()

    print("=" * 96)
    print("  若多数条都是「must_contain 被截断丢掉」→ 解法是**加大生成时的截断长度**，")
    print("  而不是改提示词。这是很典型的：看起来像模型能力问题，实际是预处理参数问题。")
    print("=" * 96)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
