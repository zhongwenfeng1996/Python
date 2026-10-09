#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打印一次真实的 API 响应原文，用于把教程里的 response 结构写成"事实"而不是"记忆"。

用法（配合 mock 服务）：
    终端 1: python3 week01/mock_server.py
    终端 2: python3 week01/show_response.py

配了真实 Key 时也可以直接跑，会看到真实供应商的响应（字段可能更多）。
"""

from __future__ import annotations

import json
import os
import urllib.request

API_KEY = os.environ.get("OPENAI_API_KEY", "test")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("MODEL", "deepseek-chat")

data = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "用一句话解释什么是向量"}],
}
body = json.dumps(data, ensure_ascii=False).encode("utf-8")
request = urllib.request.Request(
    f"{BASE_URL}/chat/completions",
    data=body,
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {API_KEY}"},
)

with urllib.request.urlopen(request, timeout=60) as response:
    raw = response.read().decode("utf-8")

print("=" * 70)
print("HTTP 响应的原始文本（这就是 response 背后的东西）")
print("=" * 70)
print(raw[:400] + ("..." if len(raw) > 400 else ""))
print()

parsed = json.loads(raw)
print("=" * 70)
print("json.loads 之后，它就是一个嵌套的字典（缩进打印看得更清楚）")
print("=" * 70)
print(json.dumps(parsed, ensure_ascii=False, indent=2)[:1200])
print()

print("=" * 70)
print("怎么一层层取出内容")
print("=" * 70)
print("response 的顶层键：", list(parsed.keys()))
print()
print('response["choices"]              -> %s' % type(parsed["choices"]).__name__)
print('response["choices"][0]           -> %s' % type(parsed["choices"][0]).__name__)
print('  .keys() =', list(parsed["choices"][0].keys()))
print('response["choices"][0]["message"] -> %s'
      % type(parsed["choices"][0]["message"]).__name__)
print('  .keys() =', list(parsed["choices"][0]["message"].keys()))
print()
print('最终答案：response["choices"][0]["message"]["content"]')
print("        =", repr(parsed["choices"][0]["message"]["content"])[:80])
print()
print('token 统计：response["usage"]')
print("        =", parsed.get("usage"))
