#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端冒烟测试：建索引 → 三种检索通道 → 人工看结果

这个脚本**不做评估**（评估是 run_eval.py 的事），它只回答一个问题：
**检索出来的东西看起来对吗？**

先人工看几条，再去算指标 —— 因为如果检索结果一看就不对，
指标再漂亮也可能是评估集错了。

用法：
    & G:\\转型\\.venv\\Scripts\\python.exe ai-lab\\projects\\project2-rag\\smoke_retrieve.py
    & ... smoke_retrieve.py --strategy paragraph
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rag.chunking import STRATEGIES, chunk_document      # noqa: E402
from rag.corpus import load_corpus                        # noqa: E402
from rag.embeddings import get_embedder                   # noqa: E402
from rag.retrieve import Retriever                        # noqa: E402
from rag.store import Store                               # noqa: E402

# 用真实问题测 —— 这些问题的答案我这边的文档里确实有
PROBES = [
    ("章节重排后 def 在第几章教？", "04-函数.md"),
    ("SSE 每条消息为什么必须以空行结尾？", None),
    ("日志为什么是块缓冲的，怎么解决？", None),
    ("PowerShell 改了执行策略为什么还是被拦？", "windows-quickstart.md"),
    ("首 token 超时降级最多尝试几个模型？", None),
    ("MCP Server 为什么不暴露 read_file？", None),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="heading", choices=list(STRATEGIES))
    ap.add_argument("--db", default="")
    ap.add_argument("--k", type=int, default=3)
    args = ap.parse_args()

    db = Path(args.db) if args.db else HERE / "data" / f"smoke-{args.strategy}.db"
    embedder = get_embedder(prefer="local")

    print("=" * 78)
    print("  端到端冒烟测试 · 建索引 + 三种检索")
    print("=" * 78)
    print(f"  embedder : {embedder.signature}")
    print(f"  策略     : {args.strategy}")

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy=args.strategy))
    print(f"  语料     : {len(docs)} 篇 -> {len(chunks)} 块")

    t0 = time.time()
    store = Store(db)
    store.build(chunks, embedder, batch_size=64, progress=True)
    build_s = time.time() - t0
    print(f"  建索引   : {build_s:.2f}s（{len(chunks) / max(build_s, 1e-9):.0f} 块/秒）")

    retriever = Retriever(store, embedder)
    print(f"  载入内存 : {retriever._n} 块，{retriever.dim} 维")  # noqa: SLF001

    modes = ("dense", "sparse", "hybrid")
    for question, expect in PROBES:
        print()
        print("─" * 78)
        print(f"  Q: {question}")
        if expect:
            print(f"     （期望命中含 {expect!r} 的文档）")
        for mode in modes:
            t = time.time()
            hits = retriever.search(question, k=args.k, mode=mode)
            ms = (time.time() - t) * 1000
            print(f"  [{mode:>6}] {ms:6.1f}ms")
            for h in hits:
                mark = ""
                if expect and expect in h.source_path:
                    mark = "  ⬅ 命中期望文档"
                print(f"      {h.score:7.4f}  {h.source_path}")
                print(f"               {h.heading_path[:60]}")
                if mark:
                    print(f"              {mark}")

    store.close()
    print()
    print("=" * 78)
    print(f"  索引文件：{db}  （{db.stat().st_size / 1024:.1f} KB）")
    print("  人工确认：上面的结果看起来相关吗？如果一眼就不对，先修检索再算指标。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
