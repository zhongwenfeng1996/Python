#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测量与**实际生成流程完全一致**的上下文精度（零 API 调用）

## 为什么重做这个测量

上一版 `probe_context_precision.py` 报出"含答案的块进前 5 = 0.8462"，
但接着 `probe_missing_answer.py` 和 `probe_rank_compare.py` 的结果
**互相矛盾**：

    probe_missing_answer : q050 名次 47（在 95 个池子里排）
    probe_rank_compare   : q050 名次 1（含答案的块就是第一名）

两者都没错，但**测的不是同一件事**：
  · missing_answer 在**整个 95 个候选池**里排名次
  · rank_compare 也排整个池子，但……

真正的问题是：**实际生成流程只取重排后的前 15 个**（`--recall-k 15`），
再从中取前 5 送给模型。所以"在 95 个池子里排第 47"这件事
**不影响生成** —— 只要它在前 15 里就行。

而 seed 的一致性也很关键：`LLMReranker.rerank` 用 Python 的
`sorted`（稳定排序），平局按原顺序；我的诊断脚本也用同样的方式，
所以平局时的次序应该一致。

**这个脚本严格复刻生成流程**：
  1. 粗召回 union@50（dense 50 + sparse 50 交错合并）
  2. 取**前 recall_k 个**（默认 15，与实际一致）
  3. 用缓存的 LLM 分数重排这 recall_k 个
  4. 取前 5 → 检查含答案的块在不在里面

这样得到的就是"生成时模型真正看到的东西"。

## 顺带回答一个关键问题

如果 precision@5 不高，那我还能提高 recall_k（多送几段给模型）。
这个脚本直接算出 precision 随 recall_k 的变化，**零 API 调用即可**
（因为缓存里有整个池子的分数）。

用法：
    python eval/measure_context_precision.py
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
    store = Store(HERE.parent / "data" / "measure-ctx.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    cfg = LLMRerankConfig(cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
                          batch_size=10, max_chars=400)
    scorer = LLMScorer(cfg)
    cache = scorer._cache  # noqa: SLF001
    ts = TwoStageRetriever(retriever, _Noop(), recall_channels="union", recall_k=50)

    print("=" * 94)
    print("  上下文精度 —— 严格复刻生成流程（零 API 调用）")
    print("=" * 94)
    print(f"  可答题 {len(questions)} 条")
    print()

    # 预先算好每题的池子与分数（避免重复计算候选池）
    data = []
    for q in questions:
        pool = ts.recall(q.question)
        scores = [cache.get(scorer._key(cfg.model, q.question, h.text))  # noqa: SLF001
                  for h in pool]
        data.append((q, pool, scores))

    print("  ★ 含答案的块进入「送给模型的 k 段」的比例")
    print(f"  {'recall_k':>9} {'precision@k':>12} {'gold 文件@k':>12} {'缺失分数':>9}")
    print("  " + "-" * 50)

    best = {}
    for rk in (5, 10, 15, 20, 30, 50, 80):
        n_chunk = n_doc = n_missing = 0
        for q, pool, scores in data:
            head = pool[:rk]
            hs = scores[:rk]
            order = sorted(range(len(head)),
                           key=lambda i: -(hs[i] if hs[i] is not None else -1))
            top = [head[i] for i in order]
            n_missing += sum(1 for s in hs if s is None)
            if q.must_contain and any(q.must_contain in h.text for h in top[:5]):
                n_chunk += 1
            if any(h.source_path == q.gold_doc for h in top[:5]):
                n_doc += 1
        n = len(data)
        best[rk] = (n_chunk / n, n_doc / n, n_missing)
        print(f"  {rk:>9} {n_chunk/n:>12.4f} {n_doc/n:>12.4f} {n_missing:>9}")

    print()
    print("  说明：'recall_k' 是**粗召回后取多少个送去重排**（实际生成用 15），")
    print("        然后这 k 个里按分数取前 5 送给生成模型。")
    print()

    # ---- 用与实际一致的 recall_k=15，列出没进的题 ----
    rk = 15
    miss = []
    for q, pool, scores in data:
        head = pool[:rk]
        hs = scores[:rk]
        order = sorted(range(len(head)),
                       key=lambda i: -(hs[i] if hs[i] is not None else -1))
        top = [head[i] for i in order[:5]]
        if not (q.must_contain and any(q.must_contain in h.text for h in top)):
            # 目标块在整池里的名次（供对比）
            full_order = sorted(range(len(pool)),
                                key=lambda i: -(scores[i] if scores[i] is not None else -1))
            tgt = next((i for i in full_order
                        if q.must_contain and q.must_contain in pool[i].text), None)
            full_rank = (full_order.index(tgt) + 1) if tgt is not None else 0
            in_head = sum(1 for h in head if q.must_contain and q.must_contain in h.text)
            miss.append((q, full_rank, in_head))

    print(f"  ★ recall_k={rk} 时，含答案块没进前 5 的 {len(miss)} 条：")
    print(f"  {'id':<7} {'整池名次':>8} {'是否在前15':>10}  问题")
    print("  " + "-" * 70)
    for q, fr, inhead in miss:
        print(f"  {q.id:<7} {fr:>8} {('是' if inhead else '否'):>10}  "
              f"[{q.qtype}] {q.question[:40]}")
    print()
    inhead_n = sum(1 for _, _, ih in miss if ih)
    print(f"    其中 {inhead_n} 条**目标块就在前 15 里**（只是没排进前 5）→ 加大送料段数即可")
    print(f"    另 {len(miss)-inhead_n} 条目标块**不在前 15** → 要加大 recall_k")
    print()
    print("=" * 94)
    print("  结论怎么用")
    print("=" * 94)
    print("    · 如果 precision@15 已经接近 1.0，那当前流程没问题，")
    print("      上一版 0.8462 是**测量口径与实际流程不一致**造成的虚低")
    print("    · 如果加大 recall_k 能明显提高 precision，那是**免费的改进**")
    print("      （缓存里有全池分数，实际生产也只是多打几次分）")
    print("=" * 94)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
