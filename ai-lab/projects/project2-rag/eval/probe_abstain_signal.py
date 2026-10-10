#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
弃答判据的可行性探测 —— "用重排分数判弃答"到底行不行？

## 为什么要先探这个

冒烟测试暴露了一个疑点：

    不可答题 q013「rerank 和向量检索的区别是什么？」
        模型正确弃答了，但**检索最高分是 8**
    陷阱题「Transformer 注意力复杂度」
        弃答，最高分 0
    陷阱题「PyTorch vs TensorFlow」
        弃答，最高分 1

如果可答题的分数分布和不可答题**重叠**，那"分数阈值判弃答"就不可行 ——
阈值定高了会误弃答（把能答的也说不知道），定低了就拦不住。

q013 分数高的原因可以解释：它的话题（rerank、向量检索）**在这个仓库里到处都是**，
所以检索到的段落"话题相关"，但**都不包含答案**。
也就是说：

> 重排分数衡量的是"**这段和问题相不相关**"，
> 不是"**这段能不能回答问题**"。这是两件事。

## 这个脚本要量什么

用**零生成调用**的方式（只用缓存的重排分数）统计：
  · 可答题的 top-1 分数分布
  · 不可答题的 top-1 分数分布
  · 两者是否可分（如果分布基本重合，就说明这条路走不通）

用法：
    python eval/probe_abstain_signal.py
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
from rag.llm_rerank import LLMRerankConfig, LLMReranker, LLMScorer  # noqa: E402
from rag.rerank import TwoStageRetriever                       # noqa: E402
from rag.retrieve import Retriever                             # noqa: E402
from rag.store import Store                                    # noqa: E402
from run_eval import load_questions                            # noqa: E402


def describe(name: str, vals: list[float]) -> None:
    if not vals:
        print(f"  {name}: （无样本）")
        return
    s = sorted(vals)
    n = len(s)
    print(f"  {name}: n={n}  最小 {s[0]:.0f}  中位 {s[n//2]:.0f}  最大 {s[-1]:.0f}"
          f"   平均 {sum(s)/n:.2f}")


def main() -> int:
    questions = load_questions(HERE / "questions.jsonl")
    answerable = [q for q in questions if q.answerable]
    unanswerable = [q for q in questions if not q.answerable]

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "probe-abstain.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    scorer = LLMScorer(LLMRerankConfig(
        cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
        batch_size=10, max_chars=400))
    ts = TwoStageRetriever(retriever, LLMReranker(scorer),
                           recall_channels="union", recall_k=15)

    print("=" * 90)
    print("  弃答判据探测 —— 重排分数能不能区分「可答」与「不可答」")
    print("=" * 90)
    print(f"  可答 {len(answerable)} 条   不可答 {len(unanswerable)} 条")
    print()
    print("  ⚠️ 只统计 top-1 分数；用已缓存的重排分数，**零生成调用**")
    print()

    ans_scores: list[float] = []
    ans_top1_recall: list[int] = []      # gold 是否在 top-5（1/0）
    unans_scores: list[float] = []
    # 额外的不可答题（陷阱题）—— 扩充不可答样本
    traps = [
        "Transformer 的注意力机制时间复杂度是多少？",
        "PyTorch 和 TensorFlow 哪个更适合生产环境？",
        "这个项目每月服务器成本是多少钱？",
        "作者最喜欢哪个编程语言，为什么？",
        "团队里一共有几个工程师？",
    ]

    print("  【可答题】逐条 top-1 分数")
    for q in answerable:
        hits = ts.search(q.question, k=5)
        top = max((h.score for h in hits), default=0.0)
        ans_scores.append(top)
        gold_in = 1 if any(h.source_path == q.gold_doc for h in hits) else 0
        ans_top1_recall.append(gold_in)
    describe("可答题  top-1 分数", ans_scores)
    hit_rate = sum(ans_top1_recall) / max(len(ans_top1_recall), 1)
    print(f"    （gold 在 top-5 的比例：{hit_rate:.4f}）")
    print()

    print("  【不可答题】逐条 top-1 分数")
    for q in unanswerable:
        hits = ts.search(q.question, k=5)
        top = max((h.score for h in hits), default=0.0)
        unans_scores.append(top)
        print(f"    {q.id}  分数 {top:.0f}   {q.question[:44]}")
    print()

    print("  【陷阱题】逐条 top-1 分数（扩充不可答样本）")
    for tq in traps:
        hits = ts.search(tq, k=5)
        top = max((h.score for h in hits), default=0.0)
        unans_scores.append(top)
        print(f"    ----  分数 {top:.0f}   {tq[:44]}")
    print()

    describe("不可答  top-1 分数", unans_scores)
    print()

    # ---- 可分性分析 ----
    print("  " + "-" * 86)
    print("  可分性：如果可答题的最低分 < 不可答题的最高分，就存在重叠区")
    a_min = min(ans_scores) if ans_scores else 0
    u_max = max(unans_scores) if unans_scores else 0
    print(f"    可答题最低分: {a_min:.0f}")
    print(f"    不可答最高分: {u_max:.0f}")
    print()
    if a_min < u_max:
        print(f"    ❌ **存在重叠区 [{a_min:.0f}, {u_max:.0f}]** ——")
        print("       任何阈值都会同时犯两类错误：")
        print(f"         阈值 > {u_max:.0f}：会把分数在 [{a_min:.0f}, {u_max:.0f}] 的可答题误弃答")
        print(f"         阈值 ≤ {u_max:.0f}：会漏掉分数 {u_max:.0f} 的不可答题")
    else:
        print(f"    ✅ 完全可分 —— 阈值可取 [{u_max:.0f}, {a_min:.0f}] 之间任意值")

    print()
    # 每个候选阈值下的两个错误率
    print("  各阈值下的表现（零生成调用，只看分数）：")
    print(f"  {'阈值':>6} {'误弃答(可答被拒)':>18} {'漏弃答(不可答被答)':>20}")
    print("  " + "-" * 50)
    for th in (0, 2, 3, 4, 5, 6, 7, 8, 9):
        if th == 0:
            fa = 0.0
            miss = 1.0
        else:
            fa = sum(1 for s in ans_scores if s < th) / max(len(ans_scores), 1)
            miss = sum(1 for s in unans_scores if s >= th) / max(len(unans_scores), 1)
        print(f"  {th:>6} {fa:>17.1%} {miss:>19.1%}")

    print()
    print("=" * 90)
    print("  结论怎么用：")
    print("    · 若存在重叠区 → **不要用分数阈值做弃答**，")
    print("      改成交给生成模型判断（提示词里已经要求了），")
    print("      分数阈值只作为「明显无关」的省调用快路径（低阈值）")
    print("    · 这正是本脚本存在的意义：**先证明判据能不能用，再造它**")
    print("=" * 90)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
