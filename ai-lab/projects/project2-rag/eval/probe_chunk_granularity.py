#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证：把超大块再切细，能不能救回"答案被稀释"的那几条？（零 API 调用）

## 假设

`probe_unreachable.py` 发现剩下的 3 条题有一个共同点：
**含答案的块是"杂烩"** —— 一个块里塞了多个互相独立的事实。

典型例子：`ai-lab/README.md` 的「埋的六个雷」块（618 字符）
装了 6 个独立的数据陷阱。问"哪个陷阱和邮箱去重计数有关"时，
这个块的"整体相关性"被另外 5 个陷阱稀释了。

而块长度分布：
    最小 22   中位 399   75% 563   最大 1010
    **49.4% 的块超过 400 字符**

## 这个脚本怎么验证（零成本）

不改任何代码逻辑，只在**建索引时**把超长块按段落再切一次，
然后用**已有的重排分数缓存**……等等，缓存键含段落原文，
切细后正文变了 → 缓存失效 → 要重新打分。

所以这个脚本做的是**第一步：零成本的静态分析** ——
不带 LLM 重排，只看**两种切块策略下，含答案的块能不能进召回池前 5**
（用稀疏分数，它对块长度敏感度低，适合先看"能不能捞到"）。

如果切细后能捞到，再去花 API 钱验证重排效果。

用法：
    python eval/probe_chunk_granularity.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from rag.chunking import chunk_document                        # noqa: E402
from rag.corpus import load_corpus                              # noqa: E402
from rag.embeddings import get_embedder                         # noqa: E402
from rag.retrieve import Retriever                              # noqa: E402
from rag.store import Store                                     # noqa: E402
from run_eval import load_questions                             # noqa: E402


def split_long(text: str, limit: int) -> list[str]:
    """
    把超长块按"空行/列表项"边界再切，目标每段 <= limit 字符。

    为什么按这些边界而不是硬切字符：
      硬切会把一句话、一个表格行切断，反而制造出更多"没头没尾"的碎片。
      按空行和编号列表项切，能保住语义单元的完整性。
    """
    if len(text) <= limit:
        return [text]
    # 先在"编号列表项"边界切（1. 2. 3. / - ），再按空行
    parts = re.split(r"\n(?=\s*(?:\d+\.|[-*+]\s)|\n)", text)
    out: list[str] = []
    buf = ""
    for p in parts:
        if not p.strip():
            continue
        if len(buf) + len(p) <= limit:
            buf = (buf + "\n" + p) if buf else p
        else:
            if buf:
                out.append(buf)
            # 单段本身超长：硬切兜底
            if len(p) > limit:
                for i in range(0, len(p), limit):
                    out.append(p[i:i + limit])
                buf = ""
            else:
                buf = p
    if buf:
        out.append(buf)
    return out or [text]


def build_index(limit: int):
    docs = load_corpus()
    chunks = []
    for d in docs:
        for c in chunk_document(d.text, d.rel_path, strategy="heading"):
            for k, piece in enumerate(split_long(c.text, limit)):
                # 复制一份、换正文。Chunk 的字段是
                # (text, source_path, heading_path, chunk_index, strategy)
                # —— 踩过：我一开始写了 chunk_id，那个字段不存在。
                chunks.append(type(c)(
                    text=piece,
                    source_path=c.source_path,
                    heading_path=c.heading_path,
                    chunk_index=k,
                    strategy=f"{c.strategy}+split{limit if limit < 100000 else 'off'}",
                ))
    return chunks


def main() -> int:
    questions = [q for q in load_questions(HERE / "questions.jsonl") if q.answerable]
    embedder = get_embedder("local")

    print("=" * 96)
    print("  切块粒度实验 —— 超大块再切细能否救回被稀释的答案（零 API 调用）")
    print("=" * 96)
    print("  判据：含答案的块能否进**稀疏召回**前 5（先看'能不能捞到'）")
    print()

    # 只测那些"当前捞不到"的困难题 + 全体做对照
    hard_ids = {"q037", "q040", "q041"}
    hard = [q for q in questions if q.id in hard_ids]
    print(f"  重点看这 3 条（当前连 148 的池子都捞不到）: "
          f"{[q.id for q in hard]}")
    print()

    print(f"  {'块上限':>7} {'块数':>6} {'全体答案块@5':>13} {'这3条答案块@5':>14}")
    print("  " + "-" * 50)

    for limit in (250, 400, 600, 10**9):
        chunks = build_index(limit)
        store = Store(HERE.parent / "data" / f"gran-{min(limit, 99999)}.db")
        store.build(chunks, embedder, batch_size=64)
        retriever = Retriever(store, embedder)

        def hit_at5(q) -> bool:  # noqa: ANN001
            hits = retriever.search(q.question, k=5, mode="sparse")
            return bool(q.must_contain) and any(q.must_contain in h.text for h in hits)

        n_all = sum(1 for q in questions if hit_at5(q))
        n_hard = sum(1 for q in hard if hit_at5(q))
        label = "不切（原样）" if limit > 100000 else str(limit)
        print(f"  {label:>7} {len(chunks):>6} {n_all/len(questions):>13.4f} "
              f"{n_hard}/{len(hard):>12}")
        store.close()

    print()
    print("=" * 96)
    print("  读法")
    print("=" * 96)
    print("    · 如果切细后'这3条'从 0/3 变成 3/3，说明切块粒度就是根因")
    print("      → 下一步：把重排分数在这些更细的块上重打一遍（要花 API 钱）")
    print("    · 如果全体指标反而下降，说明切太细导致块失去了上下文")
    print("      （小块缺标题语境、指代不明），那要权衡而不是无脑切细")
    print("    · 注意：这个实验只用了**稀疏召回**，与 LLM 重排的结果可能不同。")
    print("      它的作用是**先筛掉不可能的方案**，省下 API 钱。")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
