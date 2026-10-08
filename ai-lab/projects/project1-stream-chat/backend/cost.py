#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
token 估算与成本计算。

为什么需要"估算"而不能只用 API 返回的 usage：
  1. 不是所有 OpenAI 兼容端点都支持 `stream_options.include_usage`
     （尤其是各家国产模型和自建网关），流式请求里常常拿不到 usage。
  2. 首 token 到达时就要给出成本预估，那时还没有 usage。
  3. 对国产模型，"中文 1 字 ≈ 1 token"这个粗略规则误差在 20% 以内，
     足够支撑"哪个模型贵 10 倍"这类决策。

估算规则（顺序很重要）：
  - CJK 字符：约 1 token / 字
  - 其余（英文、数字、标点）：约 1 token / 4 字符

真实项目里应当用 tiktoken / 供应商的 tokenizer。这里手写是为了让"中文比英文贵得多"
这件事有体感 —— 这也是 `week01/chat.py` 里那个 estimate_tokens 的放大版。
"""

from __future__ import annotations

from dataclasses import dataclass

from config import ModelSpec


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(1, other // 4) if other else cjk


def estimate_messages_tokens(messages: list[dict]) -> int:
    """
    估算 messages 的总输入 token。

    每条消息有约 4 token 的结构开销（role、分隔符），这是 OpenAI 文档里提过的
    经验值。忽略它会让"多轮对话"的成本被系统性低估。
    """
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            # 多模态内容：只统计文本部分
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        total += estimate_tokens(str(content)) + 4
    return total


@dataclass(frozen=True)
class Usage:
    """一轮对话的实际或估算用量。"""

    in_tokens: int
    out_tokens: int
    estimated: bool          # True = 估算值，False = 来自 API 的 usage

    @property
    def total(self) -> int:
        return self.in_tokens + self.out_tokens


def cost_usd(spec: ModelSpec, usage: Usage) -> float:
    """按模型单价折算美元成本。"""
    return (usage.in_tokens / 1_000_000 * spec.price_in
            + usage.out_tokens / 1_000_000 * spec.price_out)


def usage_from_api(raw: dict, spec: ModelSpec) -> Usage | None:
    """
    从 API 返回的 usage 对象构造 Usage；字段缺失或全为 0 时返回 None。

    为什么"全为 0"也要当缺失：部分兼容端点会返回一个结构完整但数值为 0 的
    usage 对象（懒得实现）。如果直接采信，成本看板会显示 0，
    比"估算"更误导人。
    """
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens") or raw.get("input_tokens") or 0
    completion = raw.get("completion_tokens") or raw.get("output_tokens") or 0
    if not prompt and not completion:
        return None
    return Usage(in_tokens=int(prompt), out_tokens=int(completion), estimated=False)


def format_cost(spec: ModelSpec, usage: Usage) -> dict:
    """给前端的成本展示结构。"""
    usd = cost_usd(spec, usage)
    return {
        "usd": round(usd, 6),
        # 人民币按 7.1 折算，只用于"这轮大概花了多少钱"的体感，不用于对账
        "cny": round(usd * 7.1, 4),
        "estimated": usage.estimated,
        "price_per_million": {"in": spec.price_in, "out": spec.price_out},
    }
