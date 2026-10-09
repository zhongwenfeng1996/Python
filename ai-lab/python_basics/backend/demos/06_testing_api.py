#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 06 章示例 · 测试 FastAPI 接口

对应正文「1. 为什么要测接口」「2. 夹具」「3. 异步测试」。

**这个文件是 pytest 的测试文件，不是脚本** —— 不要直接 `py -3` 运行，
而是用 pytest：

    py -3 -m pytest 06_testing_api.py -v

前置：基础篇 01–10 章 + 本层 01–05 章

自测：
    这些用例应该全部 PASS。故意把某个断言改错，看报错信息长什么样。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# 被测对象：一个最小的 app
# ----------------------------------------------------------------------
class ChatBody(BaseModel):
    messages: list[dict] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


def create_app() -> FastAPI:
    """
    把 app 的创建包成函数，而不是模块级全局变量。

    为什么：测试需要"每次拿到一个干净的应用"。
    模块级全局 app 会在测试之间共享状态（比如计数器、缓存），
    于是测试顺序会影响结果 —— 那种 bug 极难查。
    """
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/chat")
    async def chat(body: ChatBody) -> dict:
        last = body.messages[-1]
        return {"reply": f"你说的是：{last.get('content', '')}",
                "temperature": body.temperature}

    @app.post("/stream")
    async def stream(request: Request) -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            for piece in ("你", "好", "呀"):
                yield f"event: delta\ndata: {{\"text\": \"{piece}\"}}\n\n"
            yield "event: done\ndata: {\"chars\": 3}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


# ----------------------------------------------------------------------
# 夹具：测试里最值得学的一节
# ----------------------------------------------------------------------
@pytest.fixture
def app() -> FastAPI:
    """每个测试函数拿到一个全新的 app。"""
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """
    异步夹具：返回一个"进程内打这个 app"的客户端。

    三个要点：
      1. pytest 的夹具用 `yield` 分成"准备"和"清理"两半
         （yield 之前是 setup，之后是 teardown）
      2. 这是**异步**夹具，需要 pytest-asyncio；项目根有 pytest.ini 配了
         `asyncio_mode = auto`，所以不用给每个测试加 @pytest.mark.asyncio
      3. 用 `async with` 保证客户端被关闭 —— 夹具的 teardown 也要做资源释放

    为什么用 ASGITransport 而不是真起一个服务：
      快、不占端口、不会有端口冲突（本机 8901 就被别的程序占过）。
      代价是它不经过真实 socket —— 所以"客户端断开后上游有没有停"这类
      测试**不能**用它，必须起真进程（项目一的 test_api.py 就是那么做的）。
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://testserver") as c:
        yield c


# ----------------------------------------------------------------------
# 测试用例
# ----------------------------------------------------------------------
async def test_health(client: httpx.AsyncClient) -> None:
    """最简单的冒烟测试：服务活着吗。"""
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_chat_happy_path(client: httpx.AsyncClient) -> None:
    r = await client.post("/chat", json={
        "messages": [{"role": "user", "content": "你好"}],
    })
    assert r.status_code == 200
    body = r.json()
    assert "你好" in body["reply"]
    # 默认值也要断言 —— 否则"默认值被改坏"这种回归没人会发现
    assert body["temperature"] == 0.7


@pytest.mark.parametrize("payload,expected_loc", [
    ({"messages": []}, ["body", "messages"]),
    ({"messages": [{"r": 1}], "temperature": 9}, ["body", "temperature"]),
    ({}, ["body", "messages"]),
])
async def test_chat_validation(client: httpx.AsyncClient, payload: dict,
                               expected_loc: list) -> None:
    """
    参数化测试：**同一段逻辑、多组输入**。

    这是 pytest 最省事的地方 —— 三组非法输入写成数据，
    而不是复制三遍测试函数。项目一的 test_api.py 里
    "8 个注入载荷"就是用这个写法，一个函数变 8 条用例。
    """
    r = await client.post("/chat", json=payload)
    assert r.status_code == 422, f"应该被校验拦住：{payload}"
    locs = [e["loc"] for e in r.json()["detail"]]
    assert expected_loc in locs, f"期望错误定位 {expected_loc}，实际 {locs}"


async def test_stream_events(client: httpx.AsyncClient) -> None:
    """
    测流式接口：**要读事件流，不能只看状态码**。

    常见错误：只断言 `r.status_code == 200` 就以为测过了。
    但流式接口最容易坏的恰恰是内容 —— 少个空行、字段名写错，状态码都还是 200。
    """
    events: list[tuple[str, dict]] = []
    async with client.stream("POST", "/stream", json={}) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        event = ""
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                import json as _json
                events.append((event, _json.loads(line[5:].strip())))

    names = [e for e, _ in events]
    assert names.count("delta") == 3, f"应该有 3 段增量，实际 {names}"
    assert names[-1] == "done", "最后一条必须是 done"
    text = "".join(d["text"] for e, d in events if e == "delta")
    assert text == "你好呀"


def test_app_isolation() -> None:
    """
    验证"每次创建全新 app"这件事本身。

    这不是测业务，而是测**夹具的正确性**。
    如果 app 是模块级全局变量，两次 create_app() 会返回同一个对象，
    测试之间就会互相污染。
    """
    a, b = create_app(), create_app()
    assert a is not b, "create_app() 必须每次返回新对象"


if __name__ == "__main__":
    print(__doc__)
    print("这个文件要用 pytest 跑：")
    print("    py -3 -m pytest 06_testing_api.py -v")
