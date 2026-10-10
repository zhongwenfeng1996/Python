#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证：加深重排（对更大的池子打分）能不能把漏掉的答案块捞回来

## 背景：我上一版诊断口径错了

关键事实：`LLMReranker` 拿到**整个召回池**、对全部候选打分、
然后取前 5。所以"重排后取前 5"这个操作**等价于 recall@5**。

我上一版 `measure_context_precision.py` 是：
  先取池子前 15 → 再重排 → 取前 5
这等于**在打分前就把靠后的候选扔了** —— 与生产流程不一致，
于是算出虚低的 0.8269。

正确的理解：
  · 重排对全池打分 → 取前 5 → precision@5 ≡ recall@5 = **0.9808**
  · 生成层真正"看不到答案"的情况，只有**召回池里根本没有那个块**

## 那还剩多少可改？

`union(dense@50, sparse@50)` 的池子约 95 个；`recall-k 50` 时
`LLMReranker` 其实也只拿到 95 个（池子就那么大）。
但 `recall_k` 控制了**池子大小**：
  · recall_k=15 → 池子 15~30 个
  · recall_k=50 → 池子最多 100 个
  · 实际生成用的是 `--recall-k 15`

所以真正要问的是：**把 recall_k 从 15 提到 50，能多捞回几条？**

这个脚本用**已缓存的分数**（零 API 调用）就能算出来 ——
因为缓存里有全池每个块的分数，只是实际流程没用上。

⚠️ 但有一个前提：缓存里的分数是"整池都打过分"的结果。
   如果实际生产只用 recall_k=15，那 15 以外的分数**本来不会被算**，
   所以"提到 50"是**真实成本**（每查询多打 35 次分），不是免费的。

用法：
    python eval/probe_deeper_rerank.py
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
    store = Store(HERE.parent / "data" / "probe-deep.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    cfg = LLMRerankConfig(cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
                          batch_size=10, max_chars=400)
    scorer = LLMScorer(cfg)
    cache = scorer._cache  # noqa: SLF001

    print("=" * 96)
    print("  加深重排 / 加大候选池 —— 能多捞回几条？（零 API 调用）")
    print("=" * 96)
    print("  说明：正确流程是「对召回池全部打分 → 取前 5」，")
    print("        recall_k 决定**池子多大**（也决定打分次数 = 成本）。")
    print()

    print(f"  {'recall_k':>9} {'池均大小':>9} {'答案块@5':>10} {'gold文件@5':>11} "
          f"{'每查询打分':>10} {'相对成本':>9}")
    print("  " + "-" * 70)

    rows = []
    base_cost = None
    for rk in (15, 20, 30, 50, 90):
        n_chunk = n_doc = 0
        cost = 0
        for q in questions:
            # 池子 = union(dense@rk, sparse@rk) 交错合并
            ts = TwoStageRetriever(retriever, _Noop(),
                                   recall_channels="union", recall_k=rk)
            pool = ts.recall(q.question)
            cost += len(pool)
            scores = [cache.get(scorer._key(cfg.model, q.question, h.text))  # noqa: SLF001
                      for h in pool]
            order = sorted(range(len(pool)),
                           key=lambda i: -(scores[i] if scores[i] is not None else -1))
            top = [pool[i] for i in order[:5]]
            if q.must_contain and any(q.must_contain in h.text for h in top):
                n_chunk += 1
            if any(h.source_path == q.gold_doc for h in top):
                n_doc += 1
        n = len(questions)
        if base_cost is None:
            base_cost = cost
        rows.append((rk, cost / n, n_chunk / n, n_doc / n, cost))
        print(f"  {rk:>9} {cost/n:>9.0f} {n_chunk/n:>10.4f} {n_doc/n:>11.4f} "
              f"{cost/n:>10.0f} {cost/base_cost:>8.1f}x")

    print()
    print("=" * 96)
    print("  读法")
    print("=" * 96)
    print("    · 如果 recall_k 从 15 提到 50 能把「答案块@5」明显拉高，")
    print("      那这是**用成本换效果**的明确选择 —— 而且成本可控（打分次数翻几倍）")
    print("    · 如果提到 90 也不动，说明池子里根本没有目标块，")
    print("      要修的是 embedding 或切块，不是重排")
    print("    · ⚠️ 一个方法论提醒：我之前两次都栽在**测量口径与生产不一致**上")
    print("      （union 的'位置名次'、'先截断前15再打分'）。")
    print("      这次的口径是严格按 `LLMReranker.rerank` 的实际行为写的：")
    print("      池子全量打分 → 排序 → 取前 5。")
    print("=" * 96)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
