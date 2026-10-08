#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 你的第二个 Python 程序：流式输出 + 多轮对话
# ============================================================
# 比 01 多几个概念：函数、while 循环、列表、for 循环。
# 但仍然是"能逐行读懂"的复杂度。
#
# 运行方式：
#     终端 1:  python3 ../week01/mock_server.py
#     终端 2:  OPENAI_BASE_URL=http://127.0.0.1:8765/v1 OPENAI_API_KEY=test python3 02_stream_chat.py
#
# 这个程序最值得记住的一行：
#     messages.append({"role": "assistant", "content": reply})
# 大模型的"记忆"就是这么实现的 —— 它自己不记得任何东西，
# 你每次请求都把全部历史重新发一遍。所以对话越长，每次请求越贵。
#
# 动手改造
# --------
#   1. 加一个命令：输入 clear 时清空 messages（提示：messages = []）
#   2. 统计每轮回复的字符数，最后打印平均值
#   3. 把 stream 改成 False，看会发生什么（体会流式的价值）
# ============================================================

import json
import os
import urllib.request

API_KEY = os.environ.get("OPENAI_API_KEY", "sk-在这里填你的key")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("MODEL", "deepseek-chat")


# ---- 1. 定义函数：def 名字(参数): ----
def ask_model(messages):
    # 三引号包起来的是"文档字符串"，相当于代码里的注释文档
    """
    发一次流式请求，边收边打印，最后返回完整回复文本。
    """
    data = {
        "model": MODEL,
        "messages": messages,
        "stream": True,          # 关键：开启流式
    }

    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )

    full_text = ""               # 空字符串，用来累积回复

    with urllib.request.urlopen(request, timeout=60) as response:
        # ---- 2. 一行一行地读，而不是等全部下载完 ----
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()

            # 服务端会发心跳等无用的行，跳过
            if not line.startswith("data:"):
                continue

            payload = line[5:].strip()      # 去掉开头的 "data:"

            if payload == "[DONE]":         # 约定的结束标记
                break

            chunk = json.loads(payload)

            # ---- 3. 有的 chunk 只带用量信息，没有内容 ----
            if not chunk.get("choices"):
                continue

            delta = chunk["choices"][0].get("delta", {})
            piece = delta.get("content", "")

            if piece:
                # end="" 表示打印后不换行；flush=True 表示立刻显示出来
                print(piece, end="", flush=True)
                full_text = full_text + piece

    return full_text


# ---- 4. 主程序 ----
messages = []          # 空列表，保存对话历史（相当于 JS 的 const messages = []）

print("输入内容开始对话，输入 exit 退出。")
print()

while True:                                   # JS 里写 while (true)
    user_input = input("你: ")                # 等待输入（浏览器里是 prompt()）

    if user_input == "exit":
        break                                 # 跳出循环

    if user_input == "":                      # 空输入就跳过这一轮
        continue

    # ---- 5. 列表 append：往数组尾部加元素（不是 push）----
    messages.append({"role": "user", "content": user_input})

    print("AI: ", end="")
    reply = ask_model(messages)
    print()                                   # 换行

    # 把回复也存进历史 —— 这就是"多轮记忆"的全部实现
    messages.append({"role": "assistant", "content": reply})

print()
print(f"对话结束，共 {len(messages) // 2} 轮")   # // 是整除
