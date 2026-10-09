#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 02 章示例 · Pydantic 进阶：能直接喂给模型的东西

对应正文「3. 自定义校验」「4. 模型 -> JSON Schema」。
这一章的重点不是"Pydantic 还有什么功能"，而是**哪些能力在 LLM 应用里真的会用**：

  1. field_validator   —— 校验 + 归一化（模型最爱的温度 2.5、前后带空格的输入）
  2. model_json_schema —— 生成 JSON Schema，这正是 function calling 的 parameters
  3. model_dump        —— 对象转回字典（发给 API 之前必须做）
  4. 别名字段          —— 前端 camelCase 与后端 snake_case 的桥

运行：
    py -3 02_pydantic_validate.py
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


# ----------------------------------------------------------------------
# 1) field_validator：校验 + 顺手归一化
# ----------------------------------------------------------------------
class ToolCall(BaseModel):
    """一个工具调用（function calling 的返回值结构）。"""
    name: Literal["search", "query_db", "calc"]
    arguments: dict
    # Field 可以加约束和说明。description 尤其重要 —— 它会进 JSON Schema，
    # 而模型就是靠这段文字理解"这个参数是什么"的。
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="模型对自己这次工具调用正确性的估计，0~1",
    )


class ChatRequest(BaseModel):
    model: str = Field(min_length=1, description="模型 id，例如 deepseek-chat")
    messages: list[dict] = Field(min_length=1, description="至少一条消息")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0, le=32000)

    @field_validator("model")
    @classmethod
    def model_not_blank(cls, v: str) -> str:
        """
        field_validator 比 Field 约束更灵活的地方：可以自己写逻辑。

        注意返回值 —— **它是"归一化"的机会**，不只是"通过/不通过"。
        这里顺手把前后空格去掉，于是 " deepseek-chat " 也能用。
        """
        v = v.strip()
        if not v:
            raise ValueError("model 不能是空白字符串")
        return v

    @field_validator("temperature")
    @classmethod
    def round_temperature(cls, v: float) -> float:
        """温度保留一位小数 —— 省得前端传来 0.7000000001 这种值。"""
        return round(v, 1)


# ----------------------------------------------------------------------
# 2) 带别名的模型：对接前端的 camelCase
# ----------------------------------------------------------------------
class ModelInfo(BaseModel):
    """
    给前端返回的模型信息。

    前端习惯 camelCase，Python 习惯 snake_case。
    别名让两边各写各的，不用互相迁就。
    """
    id: str
    label: str
    price_per_million: dict = Field(
        serialization_alias="pricePerMillion",
        description="每百万 token 的价格",
    )


def demo() -> int:
    print("=" * 74)
    print("  第 02 章 · Pydantic 进阶：能直接喂给模型的东西")
    print("=" * 74)

    # ---- 1. field_validator 的校验 + 归一化 ----
    print("\n—— 1) field_validator：校验 + 归一化 ——")
    cases = [
        ({"model": "  deepseek-chat  ", "messages": [{"role": "user"}],
          "temperature": 0.7000000001}, "空格 + 超长小数"),
        ({"model": "", "messages": [{"role": "user"}]}, "空 model（Field 约束拦）"),
        ({"model": "   ", "messages": [{"role": "user"}]},
         "纯空格 model（validator 拦）"),
        ({"model": "x", "messages": []}, "空 messages"),
        ({"model": "x", "messages": [{"r": 1}], "temperature": 2.5}, "温度超范围"),
        ({"model": "x", "messages": [{"r": 1}], "max_tokens": 0}, "max_tokens 必须 > 0"),
    ]
    for payload, desc in cases:
        try:
            obj = ChatRequest.model_validate(payload)
            print(f"  [通过] {desc:<20} model={obj.model!r} "
                  f"temperature={obj.temperature}")
        except ValidationError as exc:
            first = exc.errors()[0]
            print(f"  [拦住] {desc:<20} {'/'.join(str(x) for x in first['loc'])}: "
                  f"{first['msg']}")

    # ---- 2. model_json_schema：这就是 function calling 的 parameters ----
    print("\n—— 2) model_json_schema：工具参数定义的来源 ——")
    schema = ToolCall.model_json_schema()
    print(json.dumps(schema, ensure_ascii=False, indent=2))
    print("\n  ★ 上面这段 JSON 可以直接放进 function calling 的 tools[].function.parameters")
    print("    这就是「为什么要用 Pydantic 定义工具」的答案：")
    print("    你写一次类型，schema 自动生成，不会和代码脱节。")

    # ---- 3. 校验失败 -> 把结构化错误喂回模型 ----
    print("\n—— 3) 校验失败时，怎么把错误喂回模型重试 ——")
    bad_output = {"name": "search_web", "arguments": "不是字典", "confidence": 3}
    try:
        ToolCall.model_validate(bad_output)
    except ValidationError as exc:
        # 关键：不要用 str(exc)（一大段带 URL 的文本，费 token），
        # 而是挑出"哪个字段、错在哪"，拼成一句模型看得懂的话。
        lines = []
        for e in exc.errors():
            loc = ".".join(str(x) for x in e["loc"])
            lines.append(f"- 字段 {loc}：{e['msg']}")
        retry_prompt = "你上次的输出有错，请修正：\n" + "\n".join(lines)
        print("    喂回模型的提示词（自己拼的，简短且结构化）：")
        for line in retry_prompt.splitlines():
            print(f"      {line}")
        print(f"\n    对比 str(exc) 的长度：{len(str(exc))} 字符")
        print(f"    拼出来的长度：        {len(retry_prompt)} 字符  ← 省 token")

    # ---- 4. 别名：前端 camelCase <-> 后端 snake_case ----
    print("\n—— 4) 别名字段：对接前端命名习惯 ——")
    info = ModelInfo(id="deepseek-chat", label="DeepSeek", 
                     price_per_million={"in": 0.27, "out": 1.10})
    print(f"    Python 侧访问：info.price_per_million = {info.price_per_million}")
    dumped = info.model_dump(by_alias=True)
    print(f"    发给前端（by_alias=True）：")
    print(f"      {json.dumps(dumped, ensure_ascii=False)}")
    dumped_no_alias = info.model_dump()
    print(f"    不加 by_alias 的话：")
    print(f"      {json.dumps(dumped_no_alias, ensure_ascii=False)}")
    print("    ↑ 前端拿到 price_per_million 会一脸问号 —— 所以序列化时要记得转别名。")

    # ---- 5. model_dump：发给 API 之前必须做 ----
    print("\n—— 5) model_dump：对象转回字典 ——")
    req = ChatRequest(model="deepseek-chat", messages=[{"role": "user", "content": "hi"}])
    payload = req.model_dump(exclude_none=True)
    print(f"    model_dump(exclude_none=True) = {payload}")
    print("    ↑ exclude_none 很重要：发给 OpenAI 兼容端点时，")
    print("      max_tokens=null 有些端点会报错，干脆不传这个键。")

    print("\n" + "=" * 74)
    print("  结论：Pydantic 在 LLM 应用里的四个真实用途")
    print("    1. 校验请求（模型传进来的参数不可信）")
    print("    2. 生成工具 schema（function calling 的 parameters）")
    print("    3. 结构化错误 -> 喂回模型让它自我修正")
    print("    4. 对象 <-> JSON 的双向转换（含命名风格适配）")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(demo())
