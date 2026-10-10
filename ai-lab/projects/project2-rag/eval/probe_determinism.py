#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
稳定性测试：同一道题跑 N 次，看弃答行为是否一致

## 为什么必须测

对抗集里那一条「项目一的测试用例文件叫什么名字？」出现了：
  · 一次运行 → ✅ 弃答
  · 另一次运行 → ❌ 硬答（引用了项目二的 `tests/test_api.py`）

**同一道题、同样的 `temperature=0`、同样的资料，两次结果不同。**

这不是代码 bug，是**模型输出的固有随机性**。后果很重要：

  · **单次跑一个对抗集，得出的"拒答率"不可靠** ——
    它可能只是这一次抽样恰好撞上了好/坏结果
  · 所以要么跑多次取平均，要么明确标注"这是单次观测"

这和生成层那两个不同的端到端数字（0.9615 / 0.9423）是同一个根源。

## 这个脚本做什么

对指定的题跑 N 次，报出弃答次数与硬答时的具体输出。
这样能区分"偶发"和"必然"：

  · 始终弃答 → 这条已经安全
  · 时好时坏 → 提示词的作用是"降低概率"，不是"消除"
  · 始终硬答 → 提示词无效，需要改别的（比如把来源路径也写进正文）

用法：
    python eval/probe_determinism.py
    python eval/probe_determinism.py --n 5
"""

from __future__ import annotations

import argparse
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
from rag.generate import GenerateConfig, Generator             # noqa: E402
from rag.llm_rerank import LLMRerankConfig, LLMReranker, LLMScorer  # noqa: E402
from rag.rerank import TwoStageRetriever                       # noqa: E402
from rag.retrieve import Retriever                             # noqa: E402
from rag.store import Store                                    # noqa: E402

# 重点观察的题（都是已知会出问题的）
CASES = [
    ("跨项目", "项目一的测试用例文件叫什么名字？"),
    ("可答-对照", "项目一现在有多少条测试通过？"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4)
    args = ap.parse_args()

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "probe-det.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    scorer = LLMScorer(LLMRerankConfig(
        cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
        batch_size=10, max_chars=400))
    ts = TwoStageRetriever(retriever, LLMReranker(scorer),
                           recall_channels="union", recall_k=50)
    gen = Generator(GenerateConfig(abstain_threshold=3.0, max_passages=5))

    print("=" * 96)
    print(f"  稳定性测试 —— 每题跑 {args.n} 次，看行为是否一致")
    print("=" * 96)
    print(f"  模型 {gen.cfg.model}   temperature={gen.cfg.temperature}")
    print()

    for kind, q in CASES:
        hits = ts.search(q, k=5)
        top = max((h.score for h in hits), default=0.0)
        results = [gen.generate(q, hits) for _ in range(args.n)]
        n_ab = sum(1 for a in results if a.abstained)
        print(f"  ── [{kind}] {q}")
        print(f"     检索最高分 {top:.0f}（资料固定不变）")
        print(f"     {args.n} 次里弃答 {n_ab} 次、作答 {args.n - n_ab} 次")
        if 0 < n_ab < args.n:
            print(f"     ⚠️ **不稳定** —— 同一输入两次不同结果")
        elif n_ab == args.n:
            print(f"     ✅ 稳定弃答")
        else:
            print(f"     ❌ 稳定作答")
        # 列出每次的输出摘要
        for i, a in enumerate(results, 1):
            tag = "弃答" if a.abstained else "作答"
            print(f"       第{i}次 {tag}: {a.text[:80].replace(chr(10), ' ')}")
        print()

    print("=" * 96)
    print("  为什么这件事重要")
    print("=" * 96)
    print("    · 单次跑对抗集得出的'拒答率'是**一次抽样**，不是稳定指标")
    print("      → 报告里该写'单次观测 N/16'，或跑多次给区间")
    print("    · 提示词的作用是**降低幻觉概率**，不是'消除'")
    print("      → 想彻底消除要靠机械校验（比如引用必须逐字支持论断）")
    print("    · 这也解释了为什么端到端指标是区间（0.94~0.96）而不是单点")
    print("=" * 96)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
