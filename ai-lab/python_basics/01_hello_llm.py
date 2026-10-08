#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 你的第一个 Python 程序：调用一次大模型
# ============================================================
# 这个文件不到 50 行，但它是所有 LLM 应用的最小原型。
# 读不懂的语法请对照同目录的 js_to_python.md。
#
# 运行方式
# --------
# 方式 A（没有 API Key，用本地 mock 服务，推荐先这样）：
#     终端 1:  python3 ../week01/mock_server.py
#     终端 2:  OPENAI_BASE_URL=http://127.0.0.1:8765/v1 OPENAI_API_KEY=test python3 01_hello_llm.py
#
# 方式 B（真实调用）：
#     export OPENAI_API_KEY=sk-你的key
#     python3 01_hello_llm.py
#
# 动手改造（做完再进入 02）
# ------------------------
#   1. 把 QUESTION 换成你自己的问题
#   2. 在 messages 里加一条 system 消息，例如
#      {"role": "system", "content": "你是一个毒舌评论员"}，观察输出风格变化
#   3. 再加一行打印总 token 数（usage["total_tokens"]）
# ============================================================

# ---- 1. import：把别人写好的工具箱搬进来 ----
import json             # 处理 JSON（Python 字典 <-> JSON 字符串）
import os               # 读取环境变量
import urllib.request   # 发 HTTP 请求（相当于 JS 的 fetch）

# ---- 2. 变量：不需要 let/const，直接写名字 ----
API_KEY = os.environ.get("OPENAI_API_KEY", "sk-在这里填你的key")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("MODEL", "deepseek-chat")

QUESTION = "用一句话解释什么是大语言模型"

# ---- 3. 字符串：f"" 相当于 JS 的模板字符串，但用 {} 而不是 ${} ----
url = f"{BASE_URL}/chat/completions"

# ---- 4. 字典：相当于 JS 的对象字面量 ----
# 区别：key 必须用引号包起来（JS 里可以省略）
data = {
    "model": MODEL,
    "messages": [
        {"role": "user", "content": QUESTION}
    ],
}

# ---- 5. 编码：Python 严格区分"字符串"和"字节流" ----
# JS 的 fetch 会自动帮你转换，Python 需要手动做这两步
json_text = json.dumps(data, ensure_ascii=False)   # 字典 -> JSON 字符串
body = json_text.encode("utf-8")                   # 字符串 -> 字节流

# ---- 6. 组装请求 ----
request = urllib.request.Request(
    url,
    data=body,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    },
)

# ---- 7. 发送请求并读取响应 ----
# with 语句：离开这个代码块时自动关闭连接（类似 JS 的 try/finally）
with urllib.request.urlopen(request, timeout=60) as response:
    raw = response.read().decode("utf-8")   # 字节流 -> 字符串

# ---- 8. 解析 JSON ----
result = json.loads(raw)                    # JSON 字符串 -> 字典

# ---- 9. 从嵌套结构里取出答案 ----
# JS:     result.choices[0].message.content
# Python: 字典和列表都用中括号
answer = result["choices"][0]["message"]["content"]

print("模型回答：")
print(answer)

# ---- 10. 看看花了多少 token ----
# 这是 LLM 应用和普通 Web 开发最大的不同：每次调用都有成本
usage = result["usage"]
print()
print(f"输入 {usage['prompt_tokens']} tokens，输出 {usage['completion_tokens']} tokens")
