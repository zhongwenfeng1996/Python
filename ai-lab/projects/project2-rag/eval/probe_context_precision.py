#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断 B 类误弃答的真正原因 —— 分清"文件在"和"答案块在"（零 API 调用）

## 上一版诊断暴露的问题

`gold_in_context` 这个指标只看：

    送给模型的 5 段里，有没有一段的 source_path == gold_doc

但**一份文档切成了多个块**。所以"gold 文件在资料里"**不等于**
"含答案的那一块在资料里"。

实测抓到的矛盾：某题的 `gold_in_context=True`，
但 `must_contain` 在送进去的那段文本里**根本找不到**（位置 -1）——
而校验脚本刚刚确认过这个字符串在文档里存在。
→ 说明答案是**同一文件的另一个块**，而那个块没排进前 5。

## 这个脚本量两个层次

  · `gold_doc_in_ctx`   —— gold **文件**在资料里（现在报告用的是这个，**太松**）
  · `gold_chunk_in_ctx` —— **含 must_contain 的块**在资料里（这才是真正的上限）

后者才是"模型有没有机会答对"的判据。两者之差就是
"文件找对了但块没排上来"这部分损失 —— **它属于重排/切块的问题，不是生成的问题**。

用法：
    python eval/probe_context_precision.py
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
    questions = [q for q in load_questions(HERE / "questions.jsonl") if q.answerable]

    docs = load_corpus()
    chunks = []
    for doc in docs:
        chunks.extend(chunk_document(doc.text, doc.rel_path, strategy="heading"))

    # 每个 gold 文档里，含 must_contain 的块有多少个？（说明块粒度）
    from collections import defaultdict
    chunk_len: dict[str, list[int]] = defaultdict(list)
    for c in chunks:
        chunk_len[c.source_path].append(len(c.text))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "probe-ctx.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)
    scorer = LLMScorer(LLMRerankConfig(
        cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
        batch_size=10, max_chars=400))
    ts = TwoStageRetriever(retriever, LLMReranker(scorer),
                           recall_channels="union", recall_k=15)

    print("=" * 96)
    print("  上下文精度诊断 —— 「文件在资料里」vs「含答案的块在资料里」")
    print("=" * 96)
    print(f"  可答题 {len(questions)} 条；每份文档平均切成 "
          f"{sum(len(v) for v in chunk_len.values())/max(len(chunk_len),1):.1f} 块")
    print()

    n_doc_in = 0
    n_chunk_in = 0
    rows = []
    for q in questions:
        hits = ts.search(q.question, k=5)
        srcs = [h.source_path for h in hits]
        doc_in = q.gold_doc in srcs
        # 含 must_contain 的块在不在？（这是"模型有没有机会答对"的真判据）
        mc = q.must_contain or ""
        chunk_in = any(mc in h.text for h in hits) if mc else doc_in
        if doc_in:
            n_doc_in += 1
        if chunk_in:
            n_chunk_in += 1
        rows.append((q.id, q.qtype, doc_in, chunk_in, recs[q.id]["abstained"],
                     recs[q.id]["cited"], q.question))

    n = len(questions)
    print(f"  ★ gold_doc 在资料里      : {n_doc_in}/{n} = {n_doc_in/n:.4f}"
          f"   ← 报告里一直用的（**太松**）")
    print(f"  ★ 含答案的块在资料里      : {n_chunk_in}/{n} = {n_chunk_in/n:.4f}"
          f"   ← **这才是生成的真实上限**")
    print(f"    两者之差 = {n_doc_in - n_chunk_in} 条：文件找对了，但含答案的块没排进前 5")
    print()

    # ---- 关键交叉表 ----
    print("  ★ 交叉表：含答案的块在不在 × 模型答没答")
    print(f"  {'':<22} {'作答':>6} {'弃答':>6}")
    print("  " + "-" * 36)
    for label, cond in (("含答案块在资料里", True), ("含答案块不在资料里", False)):
        sub = [r for r in rows if r[3] == cond]
        ansd = sum(1 for r in sub if not r[4])
        abd = sum(1 for r in sub if r[4])
        print(f"  {label:<22} {ansd:>6} {abd:>6}")
    print()

    # ---- 弃答的归因 ----
    fa = [r for r in rows if r[4]]
    fa_no_chunk = [r for r in fa if not r[3]]
    fa_has_chunk = [r for r in fa if r[3]]
    print(f"  ★ 弃答共 {len(fa)} 条，归因：")
    print(f"     · 含答案的块压根没进资料 -> {len(fa_no_chunk)} 条"
          f"   **检索/重排的问题**，弃答是对的")
    print(f"     · 含答案的块在资料里却仍弃答 -> {len(fa_has_chunk)} 条"
          f"   **生成层真正的问题**")
    print()
    if fa_has_chunk:
        print("    后者逐条（这些才值得改提示词）:")
        for r in fa_has_chunk:
            print(f"      {r[0]:<6} [{r[1]:<8}] {r[6][:50]}")
        print()
    if fa_no_chunk:
        print("    前者逐条（去修检索，不是修提示词）:")
        for r in fa_no_chunk:
            print(f"      {r[0]:<6} [{r[1]:<8}] {r[6][:50]}")
        print()

    # ---- 引用是否指向含答案的块 ----
    print("  ★ 作答且含答案块在资料里的题：引用有没有指到那一块？")
    good = [r for r in rows if r[3] and not r[4]]
    if good:
        # 需要重新取 hits 才能判断，这里只报数量（细节在逐条抽查里看）
        print(f"    共 {len(good)} 条作答且答案块在资料里")
        print(f"    其中给了引用的: {sum(1 for r in good if r[5])} 条")
    print()

    print("=" * 96)
    print("  这组数字的意义")
    print("=" * 96)
    print("    生成层的上限是「含答案的块在资料里」那个比例，不是「文件在」那个。")
    print("    报告里两个都要写 —— 只写前者会**高估生成层的能力**，")
    print("    而把差额算到生成层头上又会**错怪提示词**。")
    print("=" * 96)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
