#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分段计时：定位 /api/ask 到底慢在哪一段（不起 HTTP）

## 为什么要单独测

端到端验收里 /api/ask 超过 180 秒没返回。但"慢"可能在三处：
  1. 建索引（惰性初始化）
  2. **重排**（recall_k=50 → 约 86 个候选 → 9 次 LLM 打分）
  3. 生成（流式）

不分开量就只能猜。这个脚本**直接调后端的各个部件**，
把每段耗时打出来，且**不发 HTTP** —— 排除掉网络/SSE 编码的干扰。

用法：
    python tools/time_stages.py
    python tools/time_stages.py --recall-k 15
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recall-k", type=int, default=50)
    ap.add_argument("--question", default="项目一现在有多少条测试通过？")
    args = ap.parse_args()

    # 用 backend 里那套惰性初始化，保证口径一致
    import main as backend_main  # noqa: PLC0415

    print("=" * 82)
    print("  分段计时（不起 HTTP）")
    print("=" * 82)

    t0 = time.perf_counter()
    st = backend_main._ensure_ready(args.recall_k)  # noqa: SLF001
    t_init = time.perf_counter() - t0
    print(f"  1) 建索引 + 装配          : {t_init:>7.2f}s "
          f"（{len(st['chunks'])} 块）")

    q = args.question
    # ---- 只召回（不重排）----
    t0 = time.perf_counter()
    pool = st["two_stage"].recall(q)
    t_recall = time.perf_counter() - t0
    print(f"  2) 粗召回（union）        : {t_recall:>7.2f}s （池子 {len(pool)} 个）")

    # ---- 逐条打分（重排的实质）----
    texts = [h.text for h in pool]
    t0 = time.perf_counter()
    scores = st["scorer"].score_many_sync(q, texts)
    t_score = time.perf_counter() - t0
    stt = st["scorer"].stats
    print(f"  3) 重排打分（{len(texts)} 个候选）: {t_score:>7.2f}s "
          f"（缓存命中 {stt['cache_hit']} / 真调 {stt['cache_miss']} "
          f"/ 错误 {stt['errors']}）")

    # ---- 完整检索（召回 + 重排 + 取前 5）----
    t0 = time.perf_counter()
    hits = st["two_stage"].search(q, k=5)
    t_search = time.perf_counter() - t0
    print(f"  4) 完整检索（含重排）      : {t_search:>7.2f}s")

    # ---- prepare ----
    t0 = time.perf_counter()
    prepared = st["gen"].prepare(q, hits)
    t_prep = time.perf_counter() - t0
    print(f"  5) prepare（判弃答/拼资料）: {t_prep:>7.2f}s "
          f"（送 {len(prepared.passages)} 段，最高分 {prepared.top_score:.0f}）")

    print()
    print(f"  首字之前的纯计算合计      : "
          f"{t_init + t_recall + t_score + t_search + t_prep:>7.2f}s")
    print(f"    （其中建索引只算一次：{t_init:.2f}s）")
    print(f"  以后每次请求的检索段        : {t_search:>7.2f}s"
          f"（缓存命中时会显著更低）")
    print()
    print("  直接生成一次（非流式）测总时长:")
    t0 = time.perf_counter()
    ans = st["gen"].generate(q, hits)
    t_gen = time.perf_counter() - t0
    print(f"  6) 生成                   : {t_gen:>7.2f}s "
          f"（{ans.usage.get('total_tokens')} tokens）")
    print(f"     回答: {ans.text[:70].replace(chr(10), ' ')}")
    print()
    print("=" * 82)
    print("  读法：")
    print("    · 如果第 3 步（打分）占绝大部分 → 重排是瓶颈，")
    print("      要么提高并发、要么降低 recall_k、要么先把缓存预热")
    print("    · 如果第 1 步很大 → 只是首次启动慢，之后不受影响")
    print("=" * 82)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
