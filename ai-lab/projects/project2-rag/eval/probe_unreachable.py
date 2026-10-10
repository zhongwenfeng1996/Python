#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
找出"连最大召回池都找不到答案块"的题 —— 那才是真正要修的地方（零 API 调用）

## 为什么

`probe_deeper_rerank.py` 的实测：

    recall_k=50 时 答案块@5 = 0.9423
    recall_k=90 时 答案块@5 = 0.9423   ← 完全不动

说明 **3 条题的目标块压根不在召回池里**（哪怕池子有 148 个候选）。
这 3 条不是重排能解决的，要看清它们属于哪种情况：

  A. **含 must_contain 的块在语料里存在，但两路召回都没捞到**
     → embedding 太弱 / 切块把答案切散了
  B. **must_contain 只出现在标题里，不在任何块的正文里**
     → 切块时标题只作为 heading_path 保存，没拼进正文，
       而 judged 用的是正文 → 这类题本身设计有问题
  C. **must_contain 跨块边界被切断**
     → 切块策略的问题

区分方法：直接在**全部 490 个块**里找 must_contain，看它落在哪个块、
以及那个块长什么样。

用法：
    python eval/probe_unreachable.py
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


class _Noop:
    def rerank(self, query, hits, top_k):  # noqa: ANN001, ARG002
        return list(hits)


def main() -> int:
    questions = [q for q in load_questions(HERE / "questions.jsonl") if q.answerable]

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "probe-unreach.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    cfg = LLMRerankConfig(cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
                          batch_size=10, max_chars=400)
    scorer = LLMScorer(cfg)
    cache = scorer._cache  # noqa: SLF001

    print("=" * 98)
    print("  找出「最大召回池也找不到答案块」的题（零 API 调用）")
    print("=" * 98)
    print(f"  语料共 {len(chunks)} 块")
    print()

    ts = TwoStageRetriever(retriever, _Noop(), recall_channels="union", recall_k=90)

    for q in questions:
        pool = ts.recall(q.question)
        scores = [cache.get(scorer._key(cfg.model, q.question, h.text))  # noqa: SLF001
                  for h in pool]
        order = sorted(range(len(pool)),
                       key=lambda i: -(scores[i] if scores[i] is not None else -1))
        top = [pool[i] for i in order[:5]]
        if q.must_contain and any(q.must_contain in h.text for h in top):
            continue

        # —— 这条没进 —— 在**全部块**里找 must_contain
        in_all = [c for c in chunks if q.must_contain and q.must_contain in c.text]
        print("-" * 98)
        print(f"  {q.id}  [{q.qtype}]  {q.question}")
        print(f"    池大小 {len(pool)}   must_contain={q.must_contain!r}")
        print(f"    gold_doc: {q.gold_doc}")
        print()
        if not in_all:
            print(f"    ❗ **全部 {len(chunks)} 个块里都找不到 {q.must_contain!r}**")
            # 看看是不是在标题里
            from rag.chunking import chunk_document as cd  # noqa: F401
            hits_title = [c for c in chunks
                          if q.must_contain and q.must_contain in c.heading_path]
            if hits_title:
                print(f"       但在 {len(hits_title)} 个块的 **heading_path** 里找到了:")
                for c in hits_title[:3]:
                    print(f"         {c.source_path} > {c.heading_path[:70]}")
                print("       → 类别 B：must_contain 只在标题里，没拼进正文")
            else:
                # 可能在原文里但被切块切断
                from rag.corpus import load_corpus as lc  # noqa: F401
                for d in docs:
                    if d.rel_path == q.gold_doc and q.must_contain in d.text:
                        p = d.text.find(q.must_contain)
                        print(f"       在 gold 文档原文里找到（位置 {p}）:")
                        print(f"         ...{d.text[max(0,p-80):p+90]}...")
                        print("       → 类别 C：被切块边界切断了")
                        break
                else:
                    print("       → **gold 文档原文里也没有** —— 这条题的数据本身有问题")
        else:
            print(f"    在全语料里找到 {len(in_all)} 个含它的块，但都没进前 5:")
            for c in in_all[:3]:
                print(f"      块长 {len(c.text)}  {c.source_path}")
                print(f"        > {c.heading_path[:70]}")
                print(f"        {c.text[:150].replace(chr(10), ' ')}")
            print("       → 类别 A：块存在但召回没捞到（embedding/切块的问题）")
            # 它在池子里吗？
            in_pool = [h for h in pool if q.must_contain in h.text]
            print(f"    在召回池里的: {len(in_pool)} 个 "
                  f"({'池里有但分数不够' if in_pool else '池里压根没有'})")
        print()

    print("=" * 98)
    print("  三类原因对应的修法完全不同：")
    print("    A 块存在但没捞到 → 换 embedding / 改切块粒度")
    print("    B 只在标题里       → 把 heading_path 也拼进送进模型的文本")
    print("    C 被切块切断       → 改切块策略（重叠或按语义切）")
    print("=" * 98)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
