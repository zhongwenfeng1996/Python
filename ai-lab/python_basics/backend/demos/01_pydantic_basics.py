#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 01 章示例 · Pydantic 基础

对应正文「1. 为什么要 Pydantic」「2. 定义第一个模型」。
直接运行，会打印每一步的结果：

    py -3 01_pydantic_basics.py

前置：基础篇 01–10 章 + 08 章（类与对象）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError


# ----------------------------------------------------------------------
# 1) 不用 Pydantic 的痛苦：手写校验很快就失控
# ----------------------------------------------------------------------
def validate_by_hand(data: dict) -> dict:
    """手写校验。看起来还行 —— 但这是最简版，真实需求会立刻爆炸。"""
    if not isinstance(data, dict):
        raise ValueError("必须是字典")
    if "model" not in data:
        raise ValueError("缺少 model")
    if not isinstance(data["model"], str):
        raise ValueError("model 必须是字符串")
    if "messages" not in data:
        raise ValueError("缺少 messages")
    if not isinstance(data["messages"], list):
        raise ValueError("messages 必须是列表")
    for i, m in enumerate(data["messages"]):
        if not isinstance(m, dict):
            raise ValueError(f"messages[{i}] 必须是字典")
        if "role" not in m:
            raise ValueError(f"messages[{i}] 缺少 role")
        if m["role"] not in ("system", "user", "assistant"):
            raise ValueError(f"messages[{i}].role 必须是 system/user/assistant")
        if "content" not in m:
            raise ValueError(f"messages[{i}] 缺少 content")
        if not isinstance(m["content"], str):
            raise ValueError(f"messages[{i}].content 必须是字符串")
    return data


# ----------------------------------------------------------------------
# 2) 用 Pydantic：同样的规则，声明式表达
# ----------------------------------------------------------------------
class Message(BaseModel):
    """
    一条对话消息。

    `Literal[...]` 表示"只能取这几个值之一" —— 相当于把白名单也写进了类型里。
    （Literal 是标准库 typing 的东西，基础篇没讲过，这里看到就知道意思。）
    """
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """
    一次对话请求。

    注意这里**没有写任何校验代码** —— 类型注解本身就是规则。
    Pydantic 会在构造对象时自动检查。
    """
    model: str
    messages: list[Message]
    temperature: float = 0.7      # 有默认值 = 可选字段


def demo() -> int:
    print("=" * 74)
    print("  第 01 章 · Pydantic 基础")
    print("=" * 74)

    # ---- 1. 手写校验能跑，但很脆 ----
    print("\n—— 1) 手写校验 vs Pydantic ——")
    good = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "你好"}],
    }
    print("  手写校验通过:", validate_by_hand(good)["model"])

    # ---- 2. Pydantic 把字典变成有类型的对象 ----
    print("\n—— 2) 用 Pydantic 解析 ——")
    req = ChatRequest.model_validate(good)
    print(f"  req.model        = {req.model!r}       ← 现在是属性访问，不是 req['model']")
    print(f"  req.temperature  = {req.temperature}    ← 没传，用了默认值")
    print(f"  req.messages     = {req.messages}")
    print(f"  第一条消息的内容   = {req.messages[0].content!r}")
    print(f"  类型               = {type(req.messages[0]).__name__}（不是 dict！）")

    # ---- 3. 校验失败长什么样 ----
    print("\n—— 3) 校验失败会抛 ValidationError ——")
    bad_cases = [
        ({"messages": [{"role": "user", "content": "hi"}]}, "缺 model"),
        ({"model": "x", "messages": "不是列表"}, "messages 类型错"),
        ({"model": "x", "messages": [{"role": "boss", "content": "hi"}]}, "角色不在白名单"),
        ({"model": "x", "messages": [{"role": "user"}]}, "消息缺 content"),
    ]
    for payload, desc in bad_cases:
        try:
            ChatRequest.model_validate(payload)
            print(f"  [意外通过] {desc}")
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(x) for x in first["loc"])
            print(f"  [拦住了] {desc:<18} → loc={loc!r} msg={first['msg']!r}")

    # ---- 4. 关键：错误信息是结构化的，不是一大段字符串 ----
    print("\n—— 4) 错误信息是结构化的（这点对喂给模型很重要）——")
    try:
        ChatRequest.model_validate({"model": 123, "messages": [{"role": "user", "content": 5}]})
    except ValidationError as exc:
        print(f"    .errors() 返回 {len(exc.errors())} 条，每条是一个字典：")
        for e in exc.errors():
            print(f"      {e}")
        print("\n    对比 str(exc)（默认的字符串形式，很长）：")
        print(f"      {str(exc)[:150]}...")

    print("\n" + "=" * 74)
    print("  结论：类型注解就是校验规则；失败时给的是结构化数据，不是字符串。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(demo())
