#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拒答能力专项测试 —— 用一个**对抗性**问题集测"没材料时会不会说瞎话"

## 为什么要单独做这个

评估集里只有 **4 条**不可答题，样本太少。
而"没材料却硬答"是 RAG 最危险的失败模式：
它不会报错，只会一本正经地编 —— 用户无法分辨。

所以在评估集之外单独建一个**对抗性**问题集，
覆盖几种不同的"无从答起"：

  1. **完全离题**：问 Transformer 复杂度、PyTorch vs TF
     （对这个仓库毫无关系，检索分数应该很低）
  2. **话题相关但没答案**：问"这个仓库用了多少 GPU"、"每月成本"
     （检索会命中相关话题，但资料里没有数字）
  3. **看似可以推但资料没写**：问"作者最喜欢哪个框架"、"团队几个人"
  4. **需要外部知识**：问某个库的最新版本号
  5. **答案在文档里但被明确排除**：问 project2 自己的设计（语料排除了它）
     —— 这条最难，因为话题完全相关

第 5 类特别值得测：它检验系统**会不会从"相关但被排除"的资料里硬凑答案**。

## 指标

  · **拒答率** = 正确说"资料中没有相关信息"的比例（越高越好）
  · **硬答率** = 1 - 拒答率（这是幻觉风险的直接度量）
  · 顺带看硬答的**分数分布** —— 如果硬答发生在高分区间，
    说明"分数高"并不能保证"答得出"，这印证了 probe_abstain_signal 的结论

用法：
    python eval/test_refusal.py
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

# 对抗性问题集：(类别, 问题, 为什么应该弃答)
ADVERSARIAL: list[tuple[str, str, str]] = [
    # 1. 完全离题 —— 与仓库毫无关系
    ("离题", "Transformer 自注意力的时间和空间复杂度分别是多少？",
     "通用 ML 知识，语料里没有"),
    ("离题", "Python 的 GIL 在 3.13 里被移除了吗？",
     "通用语言知识"),
    # 2. 话题相关但资料里没有具体数字
    ("相关无答案", "这个项目生产环境跑了多少台服务器？",
     "仓库是本地项目，没有部署规模"),
    ("相关无答案", "作者为这个仓库一共付了多少 API 费用？",
     "没有费用记录"),
    ("相关无答案", "这个仓库的测试覆盖率具体是多少百分比？",
     "文档没写覆盖率数字"),
    # 3. 看似可以推、但资料没写
    ("不可推", "作者认为三种切块策略里哪个最优雅？",
     "主观判断，文档只有指标没有偏好"),
    ("不可推", "如果把这个项目改成多人协作，作者会怎么分工？",
     "假设性问题，文档没有"),
    ("不可推", "为什么作者选择用 Windows 而不是 Linux 开发？",
     "文档说了环境但没解释这个选择"),
    # 4. 需要外部时效性知识
    ("外部知识", "FastAPI 最新稳定版本号是多少？",
     "版本会变，语料是快照"),
    ("外部知识", "DeepSeek 新发布的模型叫什么名字？",
     "语料里只有 deepseek-flash / v4-pro"),
    # 5. 话题完全相关但被排除（最难）
    ("相关但排除", "项目二的重排模块一共写了多少行代码？",
     "project2 自己的文档被语料排除了"),
    ("相关但排除", "这个 RAG 项目的评估集有几条不可答题？",
     "同上 —— 答案在 project2-rag/eval 里，但那被排除了"),
]


def main() -> int:
    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "refusal.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    scorer = LLMScorer(LLMRerankConfig(
        cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
        batch_size=10, max_chars=400))
    ts = TwoStageRetriever(retriever, LLMReranker(scorer),
                           recall_channels="union", recall_k=15)
    gen = Generator(GenerateConfig(abstain_threshold=3.0, max_passages=5))

    print("=" * 96)
    print("  拒答能力专项测试（对抗性问题集）")
    print("=" * 96)
    print(f"  {len(ADVERSARIAL)} 条全部应该弃答")
    print(f"  弃答短语: 「{ABSTAIN_PHRASE}」")
    print()

    results = []
    for i, (kind, q, why) in enumerate(ADVERSARIAL, 1):
        hits = ts.search(q, k=5)
        a = gen.generate(q, hits)
        results.append((kind, q, a))
        mark = "✅ 弃答" if a.abstained else "❌ **硬答**"
        print(f"  [{i:>2}/{len(ADVERSARIAL)}] {mark}  [{kind}]  分={a.top_score:.0f}")
        print(f"      问: {q}")
        print(f"      理由: {why}")
        if a.abstained:
            print(f"      答: {a.text[:60]}")
        else:
            print(f"      ⚠️ 硬答内容: {a.text[:150]}")
            print(f"      引用: {a.cited} -> "
                  f"{[h.source_path for h in a.cited_sources]}")
        print()

    n_ok = sum(1 for _, _, a in results if a.abstained)
    n = len(results)
    print("─" * 96)
    print(f"  ★ 拒答率（正确说不知道）: {n_ok}/{n} = {n_ok/n:.4f}")
    print(f"    硬答率（幻觉风险）      : {n - n_ok}/{n} = {(n-n_ok)/n:.4f}")
    print()

    # 按类别看
    from collections import defaultdict
    by: dict[str, list] = defaultdict(list)
    for kind, q, a in results:
        by[kind].append(a)
    print(f"  {'类别':<12} {'条数':>5} {'拒答':>5} {'硬答':>5}")
    print("  " + "-" * 30)
    for k in sorted(by):
        xs = by[k]
        ok = sum(1 for a in xs if a.abstained)
        print(f"  {k:<12} {len(xs):>5} {ok:>5} {len(xs)-ok:>5}")
    print()

    # 硬答时的分数分布（验证"分数高 != 答得出"）
    hard = [a for _, _, a in results if not a.abstained]
    if hard:
        print(f"  硬答的 {len(hard)} 条，检索最高分: "
              f"{sorted(round(a.top_score) for a in hard)}")
        print("  （如果这些分数不低，说明**分数高并不能保证资料里有答案** ——")
        print("    这正好印证 eval/probe_abstain_signal.py 的结论）")
    print()
    print("=" * 96)
    print("  注：这个集合是我**为了测试而构造**的，不是独立写下的评估集。")
    print("      它的作用是暴露失败模式、驱动改进，")
    print("      **不能当作无偏的准确率估计**（真实的不可答问题分布我并不知道）。")
    print("=" * 96)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
