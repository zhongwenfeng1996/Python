#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断：含答案的块为什么没进前 5 —— 是召回没找到，还是重排没排上来？

## 为什么要分清

"含答案的块进了前 5" 现在是 **44/52 = 0.8462**，8 条没进。
两种可能，修法完全不同：

  A. **召回池里就没有**（union@50+50 的池子也没捞到）
     → 检索的问题：embedding 太弱或查询词完全不匹配
  B. **召回池里有，但重排没把它排进前 5**
     → 重排的问题：LLM 打分把答案块评低了（比如答案在长块的后半段被截断）

判据：看 gold 答案块在**整个召回池**里的名次。
  · 池里没有 → A
  · 池里有、名次 > 5 → B（还能看到它被排到第几）

## 这个脚本零 API 调用

它用**已缓存的重排分数**重排整个池子，所以：
  · 不花钱
  · 但要注意：缓存的分数是**用 max_chars=400 截断的打分**，
    所以"截断导致答案块被评低"这个原因**在这个诊断里无法区分** ——
    那需要重新打分（下一节用更长的截断再测）。

用法：
    python eval/probe_missing_answer.py
"""

from __future__ import annotations

import sys
from collections import Counter
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
    store = Store(HERE.parent / "data" / "probe-missing.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    cfg = LLMRerankConfig(cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
                          batch_size=10, max_chars=400)
    scorer = LLMScorer(cfg)
    cache = scorer._cache  # noqa: SLF001

    ts = TwoStageRetriever(retriever, _Noop(), recall_channels="union", recall_k=50)

    print("=" * 100)
    print("  诊断：含答案的块为什么没进前 5（零 API 调用）")
    print("=" * 100)
    print(f"  可答题 {len(questions)} 条；召回池 = union(dense@50, sparse@50)")
    print()

    # 也看块长度分布 —— 验证"答案在长块后半段被截断"这个假设
    lens = sorted(len(c.text) for c in chunks)
    n = len(lens)
    print("  块长度分布（heading 切块，490 块）:")
    print(f"    最小 {lens[0]}  25% {lens[n//4]}  中位 {lens[n//2]}  "
          f"75% {lens[3*n//4]}  最大 {lens[-1]}")
    over = sum(1 for x in lens if x > 400)
    print(f"    超过 400 字符（会被重排打分截断）的块: {over} 个 = {over/n:.1%}")
    print()

    no_pool = []       # A 类：池子里根本没有
    in_pool_late = []  # B 类：池里有，但名次 > 5
    ok = 0

    for q in questions:
        pool = ts.recall(q.question)
        # 找含 must_contain 的那个块
        target = None
        for i, h in enumerate(pool):
            if q.must_contain and q.must_contain in h.text:
                target = (i, h)
                break
        if target is None:
            # 池里没有含答案的块；退一步看 gold 文件在不在池里
            no_pool.append((q, "池里没有含答案的块"))
            continue

        idx, h = target
        # 用缓存的分数重排整个池子，看目标块排第几
        scored = []
        for j, hh in enumerate(pool):
            s = cache.get(scorer._key(cfg.model, q.question, hh.text))  # noqa: SLF001
            scored.append((s if s is not None else -1.0, j))
        scored.sort(key=lambda x: -x[0])
        rank = next(r for r, (_, j) in enumerate(scored, 1) if j == idx)

        if rank <= 5:
            ok += 1
        else:
            in_pool_late.append((q, rank, len(pool), len(h.text),
                                 idx, h.text[:0]))

    total = len(questions)
    print("  ★ 结果")
    print(f"    含答案块进入前 5        : {ok}/{total} = {ok/total:.4f}")
    print(f"    B 类 · 池里有但没排上前 5: {len(in_pool_late)} 条")
    print(f"    A 类 · 池里压根没有      : {len(no_pool)} 条")
    print()

    if in_pool_late:
        print("  ★ B 类逐条（重排的问题 —— 提升空间在这里）")
        print(f"  {'id':<7} {'池内名次':>8} {'池大小':>6} {'块长度':>6}  问题")
        print("  " + "-" * 76)
        for q, rank, psize, clen, idx, _ in sorted(in_pool_late, key=lambda x: x[1]):
            print(f"  {q.id:<7} {rank:>8} {psize:>6} {clen:>6}  "
                  f"[{q.qtype}] {q.question[:38]}")
        print()
        ranks = [r for _, r, _, _, _, _ in in_pool_late]
        print(f"    名次分布: {sorted(ranks)}")
        print(f"    中位名次: {sorted(ranks)[len(ranks)//2]}")
        # 这些块的共同特征？
        avg_len = sum(c for _, _, _, c, _, _ in in_pool_late) / len(in_pool_late)
        print(f"    平均块长度: {avg_len:.0f} 字符"
              f"（全部块中位 {lens[n//2]}）")
        long_ratio = sum(1 for _, _, _, c, _, _ in in_pool_late if c > 400) / len(in_pool_late)
        print(f"    其中超过 400 字符（被打分截断）: {long_ratio:.0%}")
        print()

    if no_pool:
        print("  ★ A 类逐条（召回的问题）")
        for q, why in no_pool:
            print(f"    {q.id:<7} [{q.qtype}] {q.question[:44]}")
            print(f"            gold={q.gold_doc}  must_contain={q.must_contain!r}")
        print()

    print("=" * 100)
    print("  读法与下一步")
    print("=" * 100)
    print("    · B 类多 → 修重排：加大候选池、或打分时不截断（答案在长块后半段会被丢掉）")
    print("    · A 类多 → 修召回：embedding 太弱，或查询词与文档完全不重叠")
    print("    · 关键数字：如果 B 类的块普遍很长，那'打分截断 400 字符'就是主因，")
    print("      而且它**可以零成本验证**：把 max_chars 调大、只重打这些块")
    print("=" * 100)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
