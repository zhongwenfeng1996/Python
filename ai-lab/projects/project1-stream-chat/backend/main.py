#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目一 · 流式多模型对话应用 —— 后端

对应学习计划 §3 第 3 周。验收清单（逐条对应实现位置）：

  [x] 支持 3 个以上模型切换，走统一 OpenAI 兼容接口   -> config.MODEL_REGISTRY
  [x] 流式输出                                        -> /api/chat 的 SSE
  [x] 可中断，且中断后服务端不残留上游请求            -> _relay + CancelledError 处理
  [x] 重新生成 / 编辑后重跑                            -> 前端负责，后端只要求"无状态"
  [x] 显示每轮 token 数与估算成本                      -> cost.py + done 事件
  [x] 主模型首 token 超时 8s 自动切备用模型并明确提示   -> _relay 的降级循环
  [x] 前端手写 fetch + ReadableStream（不用 AI SDK）   -> frontend/index.html

架构（一句话）：**无状态后端 + 事件流**。
  浏览器 --POST /api/chat--> FastAPI --SSE--> 上游模型
  浏览器 <--SSE(meta/delta/usage/done/error)-- FastAPI

为什么后端无状态：对话历史由前端持有并每次全量发上来。这样"重新生成""编辑已发消息
后重跑"都只是前端换个数组重发，后端不需要任何会话存储或清理逻辑。
代价是每次请求重复传输历史（token 成本），换来的是实现复杂度的数量级下降 ——
这个取舍在项目三（需要落库）时会重新评估，届时会写进 ADR。

启动：
    # 离线（无需任何 API Key，用仓库自带的 mock）
    终端 1： py -3 ai-lab/week01/mock_server.py
    终端 2： $env:OPENAI_BASE_URL="http://127.0.0.1:8765/v1"; $env:OPENAI_API_KEY="test"
             .venv\\Scripts\\python -m uvicorn main:app --port 8000 --app-dir backend

    # 真实调用：在 backend/.env 里填 DEEPSEEK_API_KEY / OPENAI_API_KEY 等
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from config import MODELS_BY_ID, ModelSpec, load_settings, usable_models
from cost import (
    Usage,
    estimate_messages_tokens,
    estimate_tokens,
    format_cost,
    usage_from_api,
)
from providers import FirstTokenTimeout, UpstreamError, stream_chat

# ----------------------------------------------------------------------
# 日志：调试 LLM 应用的第一原则是"让错误可见"
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chat")

app = FastAPI(title="流式多模型对话", version="1.0.0")

# 本地开发放开 CORS：前端可能跑在 Vite(5173) 或直接 file:// 打开
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SETTINGS = load_settings()
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ----------------------------------------------------------------------
# SSE 编码
# ----------------------------------------------------------------------
def sse(event: str, data: dict | str) -> str:
    """
    编码一条 SSE 消息。

    格式必须严格：`event: X\\n` + `data: {...}\\n` + 空行。
    少一个空行，前端就收不到这条消息（浏览器会一直等下一行）——
    这是手写 SSE 最常见的坑。
    """
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _candidates(requested: str) -> list[ModelSpec]:
    """
    构造"主模型 + 备用模型"的尝试顺序。

    备用顺序：先同 provider 的其它模型（换了模型但没换网关，最可能成功），
    再其它 provider 的可用模型。这样"主模型限流"时能立刻切走。

    **上限 3 个**：降级不是无限重试。每次降级都要用户多等一个首 token 超时
    （默认 8s），3 个候选最坏情况已经 16s 起。宁可快速失败并说清原因，
    也不要让用户对着转圈等 30 秒 —— 这是"可预期"胜过"尽力而为"的取舍。
    """
    primary = MODELS_BY_ID.get(requested)
    if primary is None:
        return []
    others = [m for m in usable_models(SETTINGS) if m.id != primary.id]
    same_provider = [m for m in others if m.provider == primary.provider]
    rest = [m for m in others if m.provider != primary.provider]
    return [primary, *same_provider, *rest][:3]


# ----------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------
@app.get("/api/models")
async def list_models() -> dict:
    """前端下拉框的数据源。同时告诉前端"当前哪些模型真的能用"。"""
    usable = {m.id for m in usable_models(SETTINGS)}
    return {
        "mode": "mock" if SETTINGS.base_url_override else "live",
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "provider": m.provider,
                "context": m.context,
                "tags": list(m.tags),
                "pricePerMillion": {"in": m.price_in, "out": m.price_out},
                "available": m.id in usable,
            }
            for m in MODELS_BY_ID.values()
        ],
    }


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "mode": "mock" if SETTINGS.base_url_override else "live",
        "usable_models": [m.id for m in usable_models(SETTINGS)],
        "first_token_timeout_s": SETTINGS.first_token_timeout_s,
        "chat_timeout_s": SETTINGS.chat_timeout_s,
    }


@app.post("/api/chat")
async def chat(request: Request) -> StreamingResponse:
    """
    流式对话。请求体：
        {"model": "...", "messages": [...], "temperature": 0.7, "max_tokens": null}

    响应：text/event-stream，依次可能收到
        meta  —— 实际使用的模型、是否降级、降级原因
        delta —— 增量文本
        usage —— 真实用量（若端点支持）
        done  —— finish_reason、token 数、成本、耗时
        error —— 失败原因（HTTP 200 之后的失败只能走事件流，不能再改状态码）
    """
    body = await request.json()
    requested = str(body.get("model") or "")
    messages = body.get("messages") or []
    temperature = float(body.get("temperature", 0.7))
    max_tokens = body.get("max_tokens")
    turn_id = uuid.uuid4().hex[:8]

    if not messages:
        return StreamingResponse(
            _immediate_error("messages 不能为空"), media_type="text/event-stream"
        )

    candidates = _candidates(requested)
    if not candidates:
        return StreamingResponse(
            _immediate_error(f"未知模型：{requested}"),
            media_type="text/event-stream",
        )

    log.info("[%s] 请求 model=%s messages=%d temp=%.1f",
             turn_id, requested, len(messages), temperature)

    return StreamingResponse(
        _relay(request, turn_id, candidates, messages, temperature, max_tokens),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # 让 nginx 之类的反代不要缓冲 SSE
        },
    )


async def _immediate_error(message: str) -> AsyncIterator[str]:
    yield sse("error", {"message": message, "fatal": True})


async def _relay(
    request: Request,
    turn_id: str,
    candidates: list[ModelSpec],
    messages: list[dict],
    temperature: float,
    max_tokens: int | None,
) -> AsyncIterator[str]:
    """
    把上游事件流转成给浏览器的 SSE，并实现降级。

    降级策略（只在"还没吐出任何内容"时降级）：
      首 token 超时 / 连接失败 / 5xx / 429  -> 换下一个模型重试
      已经吐出部分内容后失败                -> 直接报错，不能静默续接
                                              （否则用户会看到两个模型的话拼在一起）

    中断处理：客户端断开时 Starlette 会取消这个生成器，
    `asyncio.CancelledError` 会向上传播，`async with response` 的退出逻辑
    依次关闭上游连接 —— 这就是"中断后不残留后台请求"的实现。
    我们记录一条日志用于验证，但**绝不能吞掉 CancelledError**。
    """
    last_error: str | None = None

    for attempt, spec in enumerate(candidates):
        emitted_any = False
        started = time.time()
        first_token_at: float | None = None
        pieces: list[str] = []
        usage_raw: dict | None = None
        finish_reason: str | None = None
        tool_calls: list[dict] = []

        if attempt > 0:
            log.warning("[%s] 降级到 %s（原因：%s）", turn_id, spec.id, last_error)
            yield sse("meta", {
                "model": spec.id,
                "degraded": True,
                "reason": last_error,
            })
        else:
            yield sse("meta", {"model": spec.id, "degraded": False})

        try:
            async for event in stream_chat(
                spec, SETTINGS, messages,
                temperature=temperature, max_tokens=max_tokens,
            ):
                # 客户端已经走了就别再往上流了（省一次判断，也避免写进已关闭的连接）
                if await request.is_disconnected():
                    log.info("[%s] 客户端断开，停止上游请求", turn_id)
                    return

                if event.kind == "delta":
                    emitted_any = True
                    if first_token_at is None:
                        first_token_at = time.time()
                    pieces.append(event.text)
                    yield sse("delta", {"text": event.text})
                elif event.kind == "usage":
                    usage_raw = event.usage
                elif event.kind == "done":
                    finish_reason = event.finish_reason
                    tool_calls = event.tool_calls

        except asyncio.CancelledError:
            # 客户端断开或主动取消。记录后原样抛出 —— 吞掉它会让上层以为正常结束。
            log.info("[%s] 请求被取消（客户端断开） 已生成 %d 字",
                     turn_id, sum(len(p) for p in pieces))
            raise

        except FirstTokenTimeout as exc:
            last_error = str(exc)
            if emitted_any or attempt == len(candidates) - 1:
                yield sse("error", {"message": last_error, "fatal": True})
                return
            continue      # 还没吐字，可以安全降级

        except UpstreamError as exc:
            last_error = f"{spec.id}: {exc}"
            # 401/403 是配置问题，换个模型也大概率不行，直接报错更省时间
            fatal_auth = exc.status in (401, 403)
            if emitted_any or fatal_auth or attempt == len(candidates) - 1:
                yield sse("error", {"message": last_error, "fatal": True})
                return
            continue

        # ---- 成功走完 ----
        elapsed = time.time() - started
        ttft = (first_token_at - started) if first_token_at else 0.0
        text = "".join(pieces)
        usage = usage_from_api(usage_raw, spec) if usage_raw else None
        if usage is None:
            usage = Usage(
                in_tokens=estimate_messages_tokens(messages),
                out_tokens=estimate_tokens(text),
                estimated=True,
            )

        log.info("[%s] 完成 model=%s ttft=%.2fs 总=%.2fs in=%d out=%d 估算=%s",
                 turn_id, spec.id, ttft, elapsed,
                 usage.in_tokens, usage.out_tokens, usage.estimated)

        payload = {
            "model": spec.id,
            "degraded": attempt > 0,
            "finish_reason": finish_reason,
            "ttft_s": round(ttft, 3),
            "elapsed_s": round(elapsed, 3),
            "usage": {
                "in_tokens": usage.in_tokens,
                "out_tokens": usage.out_tokens,
                "estimated": usage.estimated,
            },
            "cost": format_cost(spec, usage),
            "chars": len(text),
        }
        if tool_calls:
            payload["tool_calls"] = tool_calls
        yield sse("done", payload)
        return

    # 所有候选都试完了
    yield sse("error", {
        "message": last_error or "所有模型均不可用",
        "fatal": True,
    })


# ----------------------------------------------------------------------
# 前端静态托管（生产形态：同一个进程既给页面又给 API）
# ----------------------------------------------------------------------
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
