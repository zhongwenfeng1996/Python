#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完整 RAG 问答服务 —— 把项目二的检索链路做成一个能演示的 Web 应用（ADR-013）

## 这个服务做什么

它不是一个新项目，而是**把已经验证过的部件接到一个可演示的界面上**：

    问题
     ↓  混合召回（union of dense@50 + sparse@50）
     ↓  LLM 语义重排（对全池打分 → 取前 5）
     ↓  带编号引用生成（流式）
     ↓  机械校验（引用编号 + 作用域）
    答：带引用的回答，或者"资料中没有相关信息"

每个部件都在项目二里有量化指标与 ADR：
  · recall@5 0.9423~0.9808（含答案的块进前 5）
  · hit@1  0.7885（LLM 重排）
  · 引用合法率 1.0000（零编造编号）
  · 对抗集拒答 16/16
  · 跨文档混淆由机械校验拦住

## 为什么不重新实现检索

**复用 project2-rag 的模块**（用 sys.path 指过去），而不是拷一份。
理由：拷一份就会分叉 —— 改了评估里的检索逻辑，Web 服务还是旧的，
两边数字对不上。这个项目上我已经因为"同一逻辑两份实现"吃过一次亏
（diagnose_rerank.py 里的 union 忘了同步，ADR-008）。

## 为什么首字延迟能低

生成之前的事情都是**同步、毫秒级**的（分词、召回、重排用的是
缓存的 LLM 打分或本地算分）。所以可以先推 `sources` 事件
把出处亮给用户，再开始流式推正文 —— 而不是等全文生成完。

## 接口

    GET  /              前端页面
    GET  /api/health    服务状态（语料规模、模型、阈值）
    POST /api/ask       SSE 流式问答

SSE 事件序列：

    event: meta     {"question": "...", "top_score": 10}
    event: sources  [{index, path, heading, score, preview}, ...]
    event: delta    {"text": "增"}      （多次）
    event: done     {"abstained": false, "cited": [1,2],
                     "scope_ok": true, "elapsed_s": 1.2}
    event: error    {"message": "..."}

## 启动

    python backend/main.py            # 默认 127.0.0.1:8000
    python backend/main.py --port 8010
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ---- 把 project2-rag 加进 import 路径（复用而不是拷贝） ----
HERE = Path(__file__).resolve().parent
PROJECT2 = HERE.parent.parent / "project2-rag"
if not (PROJECT2 / "rag").exists():
    raise SystemExit(f"❌ 找不到 project2-rag：{PROJECT2}")
sys.path.insert(0, str(PROJECT2))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from fastapi import FastAPI, Request                # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field               # noqa: E402

from rag.chunking import chunk_document             # noqa: E402
from rag.corpus import load_corpus                  # noqa: E402
from rag.embeddings import get_embedder             # noqa: E402
from rag.generate import ABSTAIN_PHRASE, GenerateConfig, Generator  # noqa: E402
from rag.llm_rerank import LLMRerankConfig, LLMReranker, LLMScorer  # noqa: E402
from rag.rerank import TwoStageRetriever            # noqa: E402
from rag.retrieve import Retriever                  # noqa: E402
from rag.store import Store                         # noqa: E402

FRONTEND_DIR = HERE.parent / "frontend"
DATA_DIR = HERE.parent / "data"

app = FastAPI(title="RAG 知识库问答", version="1.0")

#: 全局组件（首次请求时惰性构建 —— 建索引要几秒，不该卡住服务启动）
_STATE: dict = {}

#: 检索用的线程池。
#:
#: ## 为什么必须并发 + 为什么必须放线程池
#:
#: 检索段里最贵的一步是**重排**：recall_k=50 时池子约 86 个候选，
#: 每批 10 个 → 9 次 LLM 打分调用。串行在网页上要几十秒 ——
#: 首字延迟的体验直接没了。
#:
#: 但整条检索链路是**同步**的（`TwoStageRetriever.search` 内部用
#: `asyncio.run` 跑打分），而 FastAPI 的 async 端点里跑同步代码会**阻塞
#: 事件循环**，把并发能力废掉。所以放线程池：
#:
#:   · 不阻塞事件循环（其他请求仍能被接受）
#:   · 每个请求一个线程，多个请求天然并行
#:
#: 池子大小 4：够几个用户同时用，又不会把打分并发推得太高触发限流
#: （打分本身的并发上限由 LLMScorer 的信号量控制）。
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rag-retrieve")


# ======================================================================
# 惰性初始化
# ======================================================================
def _ensure_ready(recall_k: int = 50) -> dict:
    """
    第一次请求时建好索引与各部件，之后复用。

    为什么惰性：建索引（490 块向量化 + 倒排）要几秒。
    放在 import 时做，会让"服务启动"和"能用"之间有一段时间差，
    排查问题时容易误判成服务没起来。
    """
    if _STATE:
        return _STATE

    t0 = time.time()
    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    store = Store(DATA_DIR / "web.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    # 打分缓存与评估共用一份 —— 评估跑过的问题在网页上会**秒回**
    #
    # ⚠️ 并发调高到 8 是**为了交互体验**，与评估脚本（3）不同。
    #    评估要的是"可复现"，网页要的是"别让用户等"。
    #    实测这批打分没有限流问题（probe_rate_limit.py：并发 3 时
    #    每批 0.8~1.4 秒、零 429），所以提高并发是安全的。
    #    代价：如果服务端偶发限流，单次请求会退避重试（慢一点但不会错）。
    scorer = LLMScorer(LLMRerankConfig(
        cache_path=str(PROJECT2 / "data" / "llm-score-cache.json"),
        batch_size=10, max_chars=400, concurrency=8))

    _STATE.update({
        "docs": docs,
        "chunks": chunks,
        "store": store,
        "retriever": retriever,
        "scorer": scorer,
        # recall_k=50 是实测的成本/效果拐点（ADR-010）：
        # 15 时含答案块@5 只有 0.8462，50 时 0.9423，90 不再提升。
        "two_stage": TwoStageRetriever(
            retriever, LLMReranker(scorer),
            recall_channels="union", recall_k=recall_k),
        "gen": Generator(GenerateConfig(abstain_threshold=3.0, max_passages=5)),
        "build_s": round(time.time() - t0, 2),
    })
    return _STATE


def sse(event: str, data) -> str:
    """
    编码一条 SSE 消息。

    格式必须严格：`event:` 行 + `data:` 行 + **一个空行**。
    少那个空行，浏览器会一直等下一行 —— 这是手写 SSE 最常见的坑
    （项目一的 ADR-009 记过同一个问题）。
    """
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


# ======================================================================
# 请求模型
# ======================================================================
class AskRequest(BaseModel):
    """用 Pydantic 校验请求 —— 与项目二后端篇教的用法一致。"""

    question: str = Field(min_length=1, max_length=500)
    #: 想让**所有**候选进入资料（给"看看检索结果"用），一般不用改
    max_passages: int = Field(default=5, ge=1, le=10)


# ======================================================================
# 路由
# ======================================================================
@app.get("/api/health")
async def health() -> dict:
    """服务状态。前端启动时调一次，用于显示语料规模与当前配置。"""
    st = _ensure_ready()
    return {
        "ok": True,
        "docs": len(st["docs"]),
        "chunks": len(st["chunks"]),
        "build_s": st["build_s"],
        "model": st["gen"].cfg.model,
        "recall_k": st["two_stage"].recall_k,
        "max_passages": st["gen"].cfg.max_passages,
        "abstain_threshold": st["gen"].cfg.abstain_threshold,
        "abstain_phrase": ABSTAIN_PHRASE,
        "cache_entries": len(st["scorer"]._cache),  # noqa: SLF001
    }


@app.post("/api/ask")
async def ask(req: AskRequest, request: Request) -> StreamingResponse:
    """
    SSE 流式问答。

    ## 事件顺序（时间上分成两段）

    第一段是**同步**的（毫秒级）：召回 + 重排 + prepare。
    做完就能确定"要引用哪几段"，于是先推 `sources`。

    第二段是**异步**的：流式生成。逐字推 `delta`，最后推 `done`。

    这样用户看到的是：问题一发出去，出处立刻出现，然后正文逐字冒出来。
    而不是"转圈 3 秒，一次性蹦出一整段"。
    """

    async def gen():
        st = _ensure_ready()
        t0 = time.time()
        q = req.question.strip()

        # ---- 第一段：召回 + 重排 ----
        #
        # ⚠️ 放**线程池**而不是直接 await —— 这段是同步的（内部用 asyncio.run
        #    跑打分），在 async 端点里直接调会阻塞事件循环，
        #    把"能同时服务多个用户"这个能力废掉。
        #
        # 这一步是首字延迟的主要来源：重排要打 9 次 LLM 打分。
        # 命中缓存时是毫秒级（评估跑过的问题在网页上秒回）。
        loop = asyncio.get_running_loop()
        hits = await loop.run_in_executor(
            _POOL, lambda: st["two_stage"].search(q, k=req.max_passages))

        prepared = st["gen"].prepare(q, hits)

        yield sse("meta", {
            "question": q,
            "top_score": prepared.top_score,
            "n_passages": len(prepared.passages),
        })

        # 先推出处 —— 这是"可溯源"的关键体验
        yield sse("sources", [
            {
                "index": i,
                "path": h.source_path,
                "heading": h.heading_path,
                "score": round(h.score, 1),
                "chars": len(h.text),
                "preview": h.text[:160].replace("\n", " "),
            }
            for i, h in enumerate(prepared.passages, 1)
        ])

        # ---- 第二段：流式生成 ----
        acc = ""
        try:
            async for kind, text in st["gen"].astream(prepared):
                if await request.is_disconnected():
                    # 客户端断开就停 —— 否则会白白把一次生成跑完（花钱）
                    return
                if kind == "delta":
                    acc += text
                    yield sse("delta", {"text": text})
                elif kind == "final":
                    acc = text
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})
            return

        # ---- 机械校验（与非流式路径同一套规则）----
        ans = st["gen"].finish(prepared, acc)

        # 如果被作用域校验强制弃答，前面的 delta 已经把错误内容发出去了，
        # 所以这里要**显式告诉前端"请把正文替换成弃答语"** ——
        # 不能只在 done 里标一个 flag，那样用户看到的还是编的内容。
        yield sse("done", {
            "final_text": ans.text,
            "abstained": ans.abstained,
            "abstained_by_scope": ans.abstained_by_scope,
            "raw_text": ans.raw_text,          # 被拦下的原始回答（前端可折叠展示）
            "cited": ans.cited,
            "invalid_citations": ans.invalid_citations,
            "scope_ok": ans.scope.ok if ans.scope else True,
            "scope_violations": (
                [f"[{v.citation_index}] {v.reason}" for v in ans.scope.violations]
                if ans.scope else []),
            "elapsed_s": round(time.time() - t0, 2),
        })

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        # 关掉所有缓冲 —— 否则 SSE 会被攒着一起发，流式就白做了
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


def main() -> int:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--recall-k", type=int, default=50)
    args = ap.parse_args()

    print("=" * 74)
    print("  RAG 知识库问答 · 完整链路演示")
    print("=" * 74)
    print(f"  语料      : {PROJECT2}")
    print(f"  召回池    : union(dense@{args.recall_k}, sparse@{args.recall_k})")
    print(f"  重排      : LLM 语义重排（复用评估的分数缓存）")
    print(f"  生成      : deepseek-flash，流式，带编号引用")
    print(f"  机械校验  : 引用编号范围 + 作用域（跨文档混淆）")
    print()
    print(f"  打开 http://{args.host}:{args.port}")
    print("=" * 74)
    print()

    # 先建好索引再起服务 —— 让第一个用户不用等建索引
    st = _ensure_ready(args.recall_k)
    print(f"  索引就绪：{len(st['docs'])} 篇文档 / {len(st['chunks'])} 块 "
          f"（{st['build_s']}s）")
    print(f"  打分缓存：{len(st['scorer']._cache)} 条")  # noqa: SLF001
    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
