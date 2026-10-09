#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 04 章示例 · FastAPI 最小可运行服务

对应正文「1. 一个能跑的服务」「2. 路由、请求、响应」。
**这一章开始，代码不再是脚本，而是"服务"** —— 跑起来之后要用另一个终端请求它。

运行（两种方式任选）：

  A. 用 TestClient，不占端口、不需要另开终端（推荐先这样）：
       py -3 04_fastapi_minimal.py

  B. 真起一个服务，然后自己用浏览器/curl 请求：
       py -3 -m uvicorn 04_fastapi_minimal:app --port 8000
       浏览器打开 http://127.0.0.1:8000/docs   ← FastAPI 自动生成的接口文档

前置：基础篇 01–10 章 + 本层 01–03 章（Pydantic、自定义异常）
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


def make_client(app: FastAPI) -> httpx.AsyncClient:
    """
    造一个"在进程内直接打这个 app"的 HTTP 客户端，不占端口。

    为什么不用 `fastapi.testclient.TestClient`：
    它当前版本会抛 StarletteDeprecationWarning（"Using httpx with
    starlette.testclient is deprecated; install httpx2 instead"）。
    警告本身不影响运行，但**不该在教材里教一个已经标废弃的 API** ——
    读者照抄会在自己的项目里看到一堆警告，然后来查这是怎么回事。

    改用 `httpx.ASGITransport`：把 ASGI 应用直接包成"传输层"，
    httpx 照常发请求，只不过不经过网络。

    ⚠️ 一个必踩的坑：**ASGITransport 只能配 AsyncClient**。
    写成 `httpx.Client(transport=httpx.ASGITransport(app))` 会报
        AttributeError: 'ASGITransport' object has no attribute 'handle_request'
    因为它只实现了 `handle_async_request`。
    所以本文件后面的请求都要 `await`，入口要用 `asyncio.run()`。
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )

# ----------------------------------------------------------------------
# 1) 创建应用：一个 FastAPI 实例就是"一个服务"
# ----------------------------------------------------------------------
app = FastAPI(
    title="最小示例服务",
    version="1.0.0",
    description="第 04 章的示例：几个最常用的路由形态",
)


# ----------------------------------------------------------------------
# 2) 请求体：用 Pydantic 定义（第 01、02 章的收获在这里兑现）
# ----------------------------------------------------------------------
class ChatBody(BaseModel):
    model: str = Field(default="deepseek-chat", min_length=1)
    messages: list[dict[str, Any]] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class ChatReply(BaseModel):
    model: str
    content: str
    temperature: float


# ----------------------------------------------------------------------
# 3) 四种最常用的路由形态
# ----------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    """最简单：无参数，返回一个字典。"""
    return {"ok": True, "service": "minimal-demo"}


@app.get("/models")
async def list_models(limit: int = 3) -> dict:
    """
    查询参数：函数签名里的 `limit: int = 3` 会被 FastAPI 自动当作 URL 查询参数
    `/models?limit=2`。类型不对会自动返回 422，不用自己校验。
    """
    all_models = ["deepseek-chat", "qwen-plus", "gpt-4o-mini", "glm-4-flash"]
    return {"total": len(all_models), "items": all_models[:limit]}


@app.post("/chat")
async def chat(body: ChatBody) -> ChatReply:
    """
    请求体：参数声明成 Pydantic 模型，FastAPI 自动：
      1. 从请求体读 JSON
      2. 按模型校验（不合法 -> 自动返回 422 + 结构化错误）
      3. 把结果作为 `body` 传进来（已经是 ChatBody 对象，不是 dict）
    """
    last = body.messages[-1]
    return ChatReply(
        model=body.model,
        content=f"（示例回复）你说的是：{last.get('content', '')}",
        temperature=body.temperature,
    )


@app.post("/chat/raw")
async def chat_raw(request: Request) -> dict:
    """
    手动读请求体：**项目一的 main.py 就是这么写的**（`await request.json()`）。

    为什么项目一这么写、而不是用 Pydantic 模型？
    因为它的请求体形状要看前端传什么，作者选了"少一层抽象"。
    代价是没有自动校验 —— 这其实是项目一可以改进的地方，
    第 06 章的练习会让你把它补上。

    这里两种都演示一遍，你会看到 Pydantic 版本省掉了多少手写代码。
    """
    body = await request.json()
    if not body.get("messages"):
        # 手动校验（对比上面 FastAPI 自动返回 422）
        raise HTTPException(status_code=400, detail="messages 不能为空")
    return {"received_keys": sorted(body.keys())}


@app.get("/error-demo/{code}")
async def error_demo(code: int) -> dict:
    """
    路径参数：`/error-demo/404` 里的 404 会被自动转成 int。
    这里演示怎么主动返回错误状态码。
    """
    if code not in (400, 401, 403, 404, 500):
        raise HTTPException(status_code=400, detail=f"不支持的演示码：{code}")
    raise HTTPException(status_code=code, detail=f"这是一次 {code} 的演示")


# ----------------------------------------------------------------------
# 4) 在同一个进程里请求这个服务（第 06 章会展开讲测试）
# ----------------------------------------------------------------------
async def demo() -> int:
    print("=" * 74)
    print("  第 04 章 · FastAPI 最小可运行服务")
    print("=" * 74)

    async with make_client(app) as client:
        print("\n—— 1) GET，无参数 ——")
        r = await client.get("/health")
        print(f"    GET /health  -> {r.status_code} {r.json()}")

        print("\n—— 2) 查询参数 ——")
        r = await client.get("/models")
        print(f"    GET /models          -> {r.json()}")
        r = await client.get("/models", params={"limit": 2})
        print(f"    GET /models?limit=2  -> {r.json()}")
        r = await client.get("/models", params={"limit": "abc"})
        print(f"    GET /models?limit=abc-> {r.status_code}（自动 422，不用自己校验）")
        print(f"      错误详情：{r.json()['detail'][0]['msg']}")

        print("\n—— 3) POST + Pydantic 请求体 ——")
        good = {"model": "deepseek-chat",
                "messages": [{"role": "user", "content": "你好"}]}
        r = await client.post("/chat", json=good)
        print(f"    合法请求 -> {r.status_code} {r.json()}")

        r = await client.post("/chat", json={"messages": []})
        print(f"    空 messages -> {r.status_code}（FastAPI 自动 422）")
        d = r.json()["detail"][0]
        print(f"      {d['loc']} : {d['msg']}")

        r = await client.post("/chat", json={"model": "x", "messages": [{"r": 1}],
                                             "temperature": 9})
        print(f"    温度超范围 -> {r.status_code}")
        d = r.json()["detail"][0]
        print(f"      {d['loc']} : {d['msg']}")

        print("\n—— 4) 手动读 body（项目一的写法）——")
        r = await client.post("/chat/raw", json=good)
        print(f"    POST /chat/raw -> {r.status_code} {r.json()}")
        r = await client.post("/chat/raw", json={})
        print(f"    空 body -> {r.status_code} {r.json()}")
        print("    ↑ 手写校验只能返回自己拼的错误；")
        print("      Pydantic 版本会自动给出「哪个字段、错在哪」的结构化信息。")

        print("\n—— 5) 主动返回错误状态码 ——")
        for code in (400, 401, 500):
            r = await client.get(f"/error-demo/{code}")
            print(f"    GET /error-demo/{code} -> {r.status_code} {r.json()}")

        print("\n—— 6) 自动生成的接口文档 ——")
        print("    真起服务后（uvicorn），打开 http://127.0.0.1:8000/docs")
        print("    你会看到所有接口、可以直接在页面上试 —— 这是 FastAPI 的白送功能，")
        print("    它靠的就是你写的类型注解和 Pydantic 模型。")
        schema = (await client.get("/openapi.json")).json()
        print(f"    /openapi.json 里登记了 {len(schema['paths'])} 个路径：")
        for path in schema["paths"]:
            methods = ",".join(m.upper() for m in schema["paths"][path])
            print(f"      {methods:<10} {path}")

    print("\n" + "=" * 74)
    print("  结论：FastAPI 的核心是「把类型注解变成运行时行为」")
    print("    - 参数类型  -> 自动解析 + 校验（错了返回 422）")
    print("    - 返回类型  -> 自动序列化 + 生成文档")
    print("    - async def -> 支持并发处理请求（所以客户端也要 await）")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8000)
    else:
        import asyncio
        raise SystemExit(asyncio.run(demo()))
