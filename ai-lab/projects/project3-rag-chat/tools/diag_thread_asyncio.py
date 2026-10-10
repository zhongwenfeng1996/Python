#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定位：`run_in_executor` 里跑 `asyncio.run` 会不会死锁

## 怀疑点

后端 `main.py` 的 `/api/ask` 里做了这件事：

    loop = asyncio.get_running_loop()
    hits = await loop.run_in_executor(_POOL, lambda: two_stage.search(q, k=5))

而 `TwoStageRetriever.search` 内部会调 `LLMReranker.rerank`，
后者用 `asyncio.run(...)` 来跑 `httpx.AsyncClient`。

**问题**：`asyncio.run` 会新建一个事件循环。
在 **Windows** 上，`asyncio.run` 在新线程里默认用 `ProactorEventLoop`；
而 uvicorn 自己的事件循环也在跑。两者不冲突（不同线程不同 loop），
但有两种情况会死锁：

  1. `httpx` 的连接池 / DNS 解析被全局锁串行化
  2. 新事件循环在非主线程里初始化时卡住

这个脚本**不起 HTTP**，只在"主线程"和"线程池"两种环境下
各跑一次同样的检索，对比耗时。如果线程池里明显更慢或卡住，
就说明是这个原因。

用法：
    python tools/diag_thread_asyncio.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def main() -> int:
    import main as be  # noqa: PLC0415

    Q = "项目一现在有多少条测试通过？"

    print("=" * 82)
    print("  诊断：线程池里跑 asyncio.run 是否会卡")
    print("=" * 82)
    st = be._ensure_ready(50)  # noqa: SLF001
    print("  索引就绪")
    print()

    # ---- A. 主线程直接跑 ----
    t0 = time.perf_counter()
    hits = st["two_stage"].search(Q, k=5)
    ta = time.perf_counter() - t0
    print(f"  A) 主线程直接调 search()        : {ta:>7.2f}s  ({len(hits)} 段)")

    # ---- B. 线程池里跑（模拟 run_in_executor）----
    pool = ThreadPoolExecutor(max_workers=1)
    t0 = time.perf_counter()
    fut = pool.submit(st["two_stage"].search, Q, 5)
    try:
        hits_b = fut.result(timeout=60)
        tb = time.perf_counter() - t0
        print(f"  B) 线程池里调 search()          : {tb:>7.2f}s  ({len(hits_b)} 段)")
    except Exception as e:  # noqa: BLE001
        tb = time.perf_counter() - t0
        print(f"  B) 线程池里调 search()          : ❌ 卡住/失败 {tb:.2f}s  {e}")
    pool.shutdown(wait=False)

    # ---- C. 在真实的事件循环里模拟端点写法 ----
    async def endpoint_like():
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=2) as p:
            t = time.perf_counter()
            r = await loop.run_in_executor(p, lambda: st["two_stage"].search(Q, 5))
            return time.perf_counter() - t, len(r)

    t0 = time.perf_counter()
    try:
        tc, n_c = asyncio.run(asyncio.wait_for(endpoint_like(), timeout=60))
        print(f"  C) 事件循环里 run_in_executor  : {tc:>7.2f}s  ({n_c} 段)")
    except Exception as e:  # noqa: BLE001
        print(f"  C) 事件循环里 run_in_executor  : ❌ {type(e).__name__}: {e}")

    print()
    print("=" * 82)
    print("  结论")
    print("=" * 82)
    print("    · 如果 A 快而 B/C 卡 → 确认是 asyncio.run 在线程里的问题，")
    print("      修法：给检索段一个**纯同步**入口（不要在重排里用 asyncio.run），")
    print("      或者在端点里干脆用同步 def（交给 FastAPI 自己的线程池）")
    print("    · 如果三者都快 → 问题在别处（uvicorn 配置 / SSE 编码）")
    print("=" * 82)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
