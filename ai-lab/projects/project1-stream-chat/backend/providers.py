#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenAI 兼容端点的流式调用封装。

只做一件事：把"一个 messages + 一个模型"变成一个异步事件流。
上层（路由层）负责降级、SSE 编码、取消传播，这里不掺业务逻辑。

三个容易踩的坑，都在这里处理掉：

1. **错误信息必须读出来**。httpx 抛 HTTPStatusError 时默认只给状态码，
   而 401 和 429 的处理方式完全不同。必须把响应体读出来一起抛，
   否则你会在日志里看到 "Client error '401 Unauthorized'" 然后不知道 key 错在哪。

2. **首 token 超时要单独处理**。整个请求 60s 超时是合理的（长回答本来就慢），
   但"60 秒才开始吐第一个字"用户体验是灾难。所以要用一个更短的
   first-token 超时来触发降级，而不是靠总超时。

3. **usage 可能没有**。不是所有兼容端点都支持 `stream_options.include_usage`。
   不支持时会返回 400，所以要能自动降级重试一次（去掉该参数）。
   这就是 `week01/README.md` 里承诺的"程序会自动回退到估算"——
   在本项目里真正实现了。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from config import ModelSpec, Settings


class UpstreamError(RuntimeError):
    """上游模型服务返回的错误（带状态码与响应体，方便定位）。"""

    def __init__(self, status: int | None, message: str, body: str = "") -> None:
        self.status = status
        self.body = body[:1000]
        detail = f"HTTP {status} — {message}" if status else message
        if self.body:
            detail += f" | 响应体: {self.body}"
        super().__init__(detail)


class FirstTokenTimeout(TimeoutError):
    """首 token 未在约定时间内到达 —— 降级信号，不是致命错误。"""


@dataclass
class StreamEvent:
    """流式过程中的一个事件。"""

    kind: str                      # meta | delta | usage | done
    text: str = ""                 # delta 的增量文本
    usage: dict | None = None      # usage 原始对象
    finish_reason: str | None = None
    tool_calls: list[dict] = field(default_factory=list)


def _build_payload(spec: ModelSpec, messages: list[dict], *,
                   temperature: float, max_tokens: int | None,
                   include_usage: bool) -> dict:
    payload: dict = {
        "model": spec.model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if include_usage and spec.supports_usage:
        # 让服务端在最后一个 chunk 里带上 usage
        payload["stream_options"] = {"include_usage": True}
    return payload


async def stream_chat(
    spec: ModelSpec,
    settings: Settings,
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    first_token_timeout: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    调用一个 OpenAI 兼容端点并逐块产出事件。

    抛出的异常：
      - UpstreamError      —— 4xx/5xx、连接失败
      - FirstTokenTimeout  —— 首 token 超时（调用方应据此降级）
      - asyncio.CancelledError —— 客户端断开时由上层取消传播上来，**不要吞掉它**
    """
    key = settings.key_for(spec)
    if not key and not settings.base_url_override:
        raise UpstreamError(None, f"模型 {spec.id} 缺少 API Key（provider={spec.provider}）")

    timeout = first_token_timeout or settings.first_token_timeout_s
    base_url = settings.base_url_for(spec)
    url = base_url.rstrip("/") + "/chat/completions"

    own_client = client is None
    if own_client:
        # 分阶段超时：连接 10s，读取给足（长回答本来就慢），总量由 chat_timeout 兜底
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.chat_timeout_s, connect=10.0)
        )

    try:
        # 先试带 include_usage，被 400 拒绝就去掉重试一次（兼容性兜底）
        for include_usage in (True, False):
            payload = _build_payload(spec, messages, temperature=temperature,
                                     max_tokens=max_tokens,
                                     include_usage=include_usage)
            try:
                async for event in _stream_once(client, url, key, payload, timeout):
                    yield event
                return
            except UpstreamError as exc:
                # 只有"因为 stream_options 不被支持"才值得去掉参数重试
                if include_usage and exc.status in (400, 422) and "stream_options" in exc.body:
                    continue
                raise
    finally:
        if own_client and client is not None:
            await client.aclose()


async def _stream_once(
    client: httpx.AsyncClient,
    url: str,
    key: str,
    payload: dict,
    first_token_timeout: float,
) -> AsyncIterator[StreamEvent]:
    """发一次请求并解析 SSE。"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        request = client.build_request("POST", url, json=payload, headers=headers)
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        raise UpstreamError(None, f"连接失败：{type(exc).__name__}: {exc}") from exc

    # 注意：`await client.send(..., stream=True)` 返回的 Response **不能**用
    # `async with` 包（那是 `client.stream()` 的用法）。必须显式 aclose()，
    # 否则连接会泄漏；而在"客户端中断"场景下，正是这个 aclose() 关闭了上游连接。
    try:
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", "ignore")
            raise UpstreamError(response.status_code, response.reason_phrase, body)

        got_first_token = False
        usage_obj: dict | None = None
        finish_reason: str | None = None
        tool_calls: dict[int, dict] = {}

        lines = response.aiter_lines()
        while True:
            # 首 token 之前用更短的超时；拿到第一个 token 后就交给总超时
            budget = first_token_timeout if not got_first_token else None
            try:
                if budget is None:
                    raw_line = await lines.__anext__()
                else:
                    raw_line = await asyncio.wait_for(lines.__anext__(), timeout=budget)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError as exc:
                raise FirstTokenTimeout(
                    f"首 token 超过 {first_token_timeout:.1f}s 未到达"
                ) from exc

            line = raw_line.strip()
            if not line or not line.startswith("data:"):
                continue          # 空行分隔符、SSE 注释行（心跳）都跳过
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue          # 无法解析的保活行，忽略而不是崩

            if chunk.get("usage"):
                usage_obj = chunk["usage"]

            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

                piece = delta.get("content")
                if piece:
                    got_first_token = True
                    yield StreamEvent(kind="delta", text=piece)

                # 工具调用的增量拼接：name 只在第一块出现，arguments 分多块累加。
                # 这是流式解析里最容易写错的地方 —— 直接覆盖会丢掉前半截 JSON。
                for tc in delta.get("tool_calls") or []:
                    index = tc.get("index", 0)
                    slot = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]

        if usage_obj:
            yield StreamEvent(kind="usage", usage=usage_obj)
        if tool_calls:
            yield StreamEvent(kind="done", finish_reason=finish_reason,
                              tool_calls=[tool_calls[i] for i in sorted(tool_calls)])
        else:
            yield StreamEvent(kind="done", finish_reason=finish_reason)
    finally:
        # 无论正常结束、异常、还是被取消（客户端断开），都要关掉上游连接。
        # "中断后不残留后台请求"这条验收项，最终就靠这一行兜住。
        await response.aclose()
