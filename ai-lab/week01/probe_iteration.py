#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实验：urllib 迭代响应时，每次拿到的到底是什么？

结论用于教程里的说明，所以必须实测 —— 不能凭印象写。
需要 mock 服务在跑（不需要真实 Key）：

    终端 1: python3 week01/mock_server.py --delay 0 --slow-first-token 0.02
    终端 2: python3 week01/probe_iteration.py
"""

from __future__ import annotations

import json
import os
import urllib.request

API_KEY = os.environ.get("OPENAI_API_KEY", "test")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8765/v1")

data = {
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": True,
}
body = json.dumps(data, ensure_ascii=False).encode("utf-8")
request = urllib.request.Request(
    f"{BASE_URL}/chat/completions",
    data=body,
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {API_KEY}"},
)

with urllib.request.urlopen(request, timeout=60) as response:
    print("=" * 74)
    print("问题 1：迭代 response 时，每次拿到的类型是什么？")
    print("=" * 74)
    for i, raw_line in enumerate(response):
        print(f"  第 {i} 行  类型={type(raw_line).__name__:<6} repr={raw_line!r}")
        if i >= 2:
            break

print()
print("=" * 74)
print("问题 2：每次拿到的是「完整一行」还是「任意一块」？")
print("=" * 74)
print("  上一节打印的每一条 repr 都以 \\n 结尾 → 是**整行**（按行切分，不是任意分块）")
print("  但要知道：这是 urllib 自动帮我们做的。socket 层面拿到的是任意分块；")
print("  urllib 把它包装成可按行迭代的文件对象（http.client.HTTPResponse 继承 io.BufferedIOBase）。")

print()
print("=" * 74)
print("问题 3：bytes 和 str 混着比会怎样？")
print("=" * 74)
b_line = b"data: [DONE]"
print(f"  b'data: [DONE]'.strip() == 'data: [DONE]'  ->  {b_line.strip() == 'data: [DONE]'}")
print(f"  b'data: [DONE]' == 'data: [DONE]'           ->  {b_line == 'data: [DONE]'}")
print("  ↑ 不报错，静默返回 False —— 这就是那个隐蔽的 bug")
print()
try:
    b_line.startswith("data:")
    print("  b'...'.startswith('data:')  ->  没报错？")
except TypeError as exc:
    print(f"  b'...'.startswith('data:')  ->  TypeError: {exc}")
print("  ↑ startswith 在 bytes 上不接受 str，会抛错（这个至少能发现）")
print()
print("  正确写法：raw_line.strip() == b'data: [DONE]'  或者先 decode 再按字符串处理")
