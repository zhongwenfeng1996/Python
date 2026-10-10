#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
限流测试 —— 判断"慢"是工作量问题还是被服务端限制了

## 背景

全量评估（4076 条待打分 ≈ 429 次批量调用）跑了 21 分钟毫无进展。
按 1.5 秒/次、并发 3 算是 3.6 分钟。差这么多，怀疑是：
  A. 并发 3 触发限流 → 大量 429 → 退避重试 → 时间全花在等待
  B. 单次调用实际比 1.5 秒慢很多（推理模型波动大）
  C. 代码里有死循环

这个脚本用**少量调用**（约 12 次）区分这三种：
  · 逐个报每次调用的耗时与结果
  · 如果出现 429 或大量重试 → 是限流
  · 如果每次都很慢但成功 → 是 B
  · 如果卡在某一次不动 → 是 C

用法：
    python eval/probe_rate_limit.py
    python eval/probe_rate_limit.py --concurrency 1   # 对比不同并发
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from rag.chunking import chunk_document                       # noqa: E402
from rag.corpus import load_corpus                             # noqa: E402
from rag.llm_rerank import LLMRerankConfig, LLMScorer          # noqa: E402


async def run_test(concurrency: int, n_passages: int) -> None:
    import httpx

    docs = load_corpus()
    chunks = []
    for d in docs[:4]:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))
    texts = [c.text for c in chunks[:n_passages]]
    question = "章节任意一个问题：这个项目怎么做检索评估？"

    cfg = LLMRerankConfig(concurrency=concurrency, cache_path="")  # 不缓存，强制真调
    scorer = LLMScorer(cfg)

    print(f"  并发={concurrency}  候选={n_passages}  "
          f"batch_size={cfg.batch_size}  char_budget={cfg.char_budget}  "
          f"max_tokens={cfg.max_tokens}")

    # 手动分批，逐批计时
    batches: list[tuple[int, list[str]]] = []
    i = 0
    while i < len(texts):
        chars, j = 0, i
        while j < len(texts) and (j - i) < cfg.batch_size:
            if chars + min(len(texts[j]), cfg.max_chars) > cfg.char_budget and j > i:
                break
            chars += min(len(texts[j]), cfg.max_chars)
            j += 1
        batches.append((i, texts[i:j]))
        i = j

    print(f"  切成 {len(batches)} 批: "
          f"{[len(b) for _, b in batches]}")
    print()

    sem = asyncio.Semaphore(max(1, concurrency))
    t_start = time.time()

    async with httpx.AsyncClient() as client:
        async def one(idx: int, chunk: list[str]) -> tuple[int, float, str]:
            async with sem:
                t0 = time.time()
                try:
                    scores = await scorer._score_batch(client, question, chunk)  # noqa: SLF001
                    return idx, time.time() - t0, f"✅ {len(scores)} 个: {scores[:4]}"
                except Exception as e:  # noqa: BLE001
                    return idx, time.time() - t0, f"❌ {type(e).__name__}: {str(e)[:90]}"

        tasks = [one(k, c) for k, (_, c) in enumerate(batches)]
        for coro in asyncio.as_completed(tasks):
            idx, dt, msg = await coro
            print(f"    批{idx}: {dt:>6.2f}s  {msg}")

    total = time.time() - t_start
    st = scorer.stats
    print()
    print(f"  总耗时 {total:.1f}s  实际调用 {st['cache_miss']}  错误 {st['errors']}")
    if st["last_errors"]:
        print("  最后的错误:")
        for e in st["last_errors"]:
            print(f"    {e[:120]}")
    avg = total / max(len(batches), 1)
    print(f"  平均每批 {avg:.2f}s")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--n", type=int, default=20, help="候选数")
    args = ap.parse_args()

    print("=" * 84)
    print("  限流 / 耗时测试（不写缓存，每次都是真调）")
    print("=" * 84)
    asyncio.run(run_test(args.concurrency, args.n))
    print("=" * 84)
    print("  读法：")
    print("    · 平均每批 >5s 且无错误 → 推理模型本身慢，慢是正常的")
    print("    · 出现 429 错误 → 限流，需要降并发")
    print("    · 某一批远超其它 → 那批的输入触发了超长推理，要缩小 char_budget")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
