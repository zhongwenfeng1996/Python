#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 05 章示例 · 流式响应（SSE）

对应正文「1. 为什么不一次性返回」「2. 用 StreamingResponse 实现」。
这一章是 LLM 应用**最核心的形态** —— 所有"打字机效果"都是它。

运行（不占端口，用 ASGI 传输）：
    py -3 05_streaming_sse.py

前置：基础篇 09 章（异步）+ 本层 01–04 章
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="流式示例")


# ----------------------------------------------------------------------
# 1) 事件流的生产者：一个异步生成器
# ----------------------------------------------------------------------
async def fake_model_stream(prompt: str, n: int | None = None) -> AsyncIterator[str]:
    """
    模拟"模型一段一段吐字"。

    关键点：它是一个 **async generator**（`async def` + `yield`）。
    基础篇 09 章讲过 `async def`，但没讲过 `async` 生成器 —— 语法上就是
    把两者拼起来：函数里有 `yield` 就成了异步生成器，
    调用它不会执行函数体，而是返回一个可以 `async for` 迭代的对象。

    类比同步生成器（基础篇没细讲 `yield`，这里看到一个就够了）：
        def gen():          async def agen():
            yield 1             yield 1

    `n` 只是给"只想要一小段"的调用方用的（比如原始字节预览那段）。
    """
    text = f"关于「{prompt}」，我的回答是：这是一个用于演示流式输出的模拟回复。"
    limit = len(text) if n is None else min(len(text), n)
    for i in range(0, limit, 3):
        piece = text[i:i + 3]
        await asyncio.sleep(0.01)        # 模拟网络延迟；真实场景是等上游推数据
        yield piece


# ----------------------------------------------------------------------
# 2) SSE 编码：为什么必须是这个格式
# ----------------------------------------------------------------------
def sse(event: str, data: dict | str) -> str:
    """
    编码一条 SSE 消息。

    格式必须严格：`event: X\\n` + `data: {...}\\n` + **一个空行**。
    最后的空行是消息分隔符 —— 少了它，浏览器会一直等下一行，
    表现为"服务器发了但前端收不到"。这是手写 SSE 最常见的坑。
    """
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


# ----------------------------------------------------------------------
# 3) 路由：返回 StreamingResponse 而不是普通字典
# ----------------------------------------------------------------------
@app.post("/stream")
async def stream(request: Request) -> StreamingResponse:
    """
    流式接口的形状。

    注意三件事：
      1. 传进去的是**异步生成器**，不是字符串
      2. media_type 必须是 text/event-stream
      3. 中间件/反代可能缓冲响应，要显式声明不要缓冲
    """
    body = await request.json()
    prompt = (body.get("messages") or [{}])[-1].get("content", "")

    async def event_stream() -> AsyncIterator[str]:
        yield sse("meta", {"model": "fake-model"})
        async for piece in fake_model_stream(prompt):
            # 这里可以检查客户端是否断开（第 07 章会讲为什么必须检查）
            if await request.is_disconnected():
                break
            yield sse("delta", {"text": piece})
        yield sse("done", {"chars": len(prompt), "finish_reason": "stop"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 让 nginx 之类的反向代理不要缓冲 SSE —— 不加的话前端会"卡住不动，
            # 然后一次性全出来"，流式的意义就没了。
            "X-Accel-Buffering": "no",
        },
    )


# ----------------------------------------------------------------------
# 4) 消费端：怎么读 SSE
# ----------------------------------------------------------------------
async def consume(client: httpx.AsyncClient) -> list[tuple[str, dict]]:
    """
    读 SSE 并解析成 (事件名, 数据) 列表。

    用 `client.stream(...)` + `aiter_lines()`：一行一行读，而不是等全部结束。
    这就是"流式"在客户端侧的样子。
    """
    events: list[tuple[str, dict]] = []
    async with client.stream("POST", "/stream",
                             json={"messages": [{"role": "user", "content": "什么是向量"}]}) as r:
        assert r.status_code == 200
        print(f"    Content-Type: {r.headers.get('content-type')}")
        event = ""
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
                events.append((event, data))
                if event == "delta":
                    # 逐段打印，模拟前端的"打字机"
                    print(data["text"], end="", flush=True)
    print()
    return events


async def demo() -> int:
    print("=" * 74)
    print("  第 05 章 · 流式响应（SSE）")
    print("=" * 74)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://testserver",
                                 timeout=30) as client:
        print("\n—— 1) 一次完整的流式请求 ——")
        started = time.time()
        events = await consume(client)
        elapsed = time.time() - started

        names = [e for e, _ in events]
        print(f"\n    收到 {len(events)} 条事件：meta={names.count('meta')} "
              f"delta={names.count('delta')} done={names.count('done')}")
        print(f"    总耗时 {elapsed * 1000:.0f} ms")
        print(f"    最后一条 done 事件的内容：{events[-1][1]}")

        print("\n—— 2) 流式 vs 一次性，体感差在哪 ——")
        print("    一次性返回：等 100ms 拿到全部内容 —— 用户盯着空白屏幕 100ms")
        print("    流式返回：  10ms 就出第一个字 —— 用户立刻看到东西在动")
        print("    总耗时差不多，但**首字延迟（TTFT）差 10 倍**，体感完全不同。")
        print("    这就是为什么 LLM 应用几乎都用流式。")

        print("\n—— 3) 服务端返回的原始字节长什么样 ——")
        async with client.stream("POST", "/stream",
                                 json={"messages": [{"role": "user", "content": "hi"}]}) as r:
            raw = b""
            async for chunk in r.aiter_bytes():
                raw += chunk
                if len(raw) > 160:      # 只取开头一点，够看到格式就行
                    break
        preview = raw.decode("utf-8")
        print("    " + repr(preview)[:230] + "...")
        print("    ↑ 注意每条消息以空行结尾（源码里是 \\n\\n）—— 这是 SSE 的分隔符。")
        print("      少了这个空行，浏览器会一直等下一行，表现为「服务端发了但前端收不到」。")

    print("\n" + "=" * 74)
    print("  结论：流式 = 「异步生成器 + StreamingResponse + SSE 格式」三件套")
    print("    服务端：async def + yield  ->  StreamingResponse")
    print("    客户端：client.stream() + aiter_lines()  ->  逐段渲染")
    print("    格式：  event:/data: + 空行结尾（漏了空行前端就收不到）")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(demo()))
