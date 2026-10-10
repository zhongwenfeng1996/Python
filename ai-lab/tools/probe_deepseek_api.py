#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探测 DeepSeek API 的可用能力 —— 重点是**有没有 embedding 接口**

## 为什么要先探

RAG 需要的是 **embedding**（把文本变成向量）。
但 DeepSeek 是以对话模型出名的，**它的 OpenAI 兼容端点不一定提供 /embeddings**。
如果不先确认就改代码，会白折腾一圈。

参考（本机实测）：
  · DeepSeek 官方文档只列了 chat/completions、models 等端点
  · embedding 需要另外的模型（如通义 text-embedding-v3、智谱 embedding-3、
    或 OpenAI text-embedding-3-small）

所以这个脚本依次试：
  1. /models            —— 列出可用模型（确认 Key 有效）
  2. /embeddings        —— 试一下，看它认不认
  3. 一个极小的 chat 调用 —— 确认 Key 真的能推理

用法（从仓库根）：
    python ai-lab/tools/probe_deepseek_api.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ENV_CANDIDATES = [
    Path(r"G:\转型\ai-lab\projects\project2-rag\.env"),
    Path(r"G:\转型\.env"),
]


def load_key() -> tuple[str, str]:
    """按优先级找 Key：环境变量 -> .env 文件。返回 (key, 来源描述)。"""
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key, "环境变量 DEEPSEEK_API_KEY"
    for p in ENV_CANDIDATES:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY") and v.strip():
                return v.strip(), str(p)
    return "", "（没找到）"


def post(url: str, key: str, body: dict, timeout: int = 60) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"网络错误: {e}"


def get(url: str, key: str, timeout: int = 60) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"网络错误: {e}"


def main() -> int:
    key, src = load_key()
    if not key:
        print("❌ 没找到 API Key。请放到 ai-lab/projects/project2-rag/.env：")
        print("     DEEPSEEK_API_KEY=sk-...")
        return 1

    print("=" * 78)
    print("  DeepSeek API 能力探测")
    print("=" * 78)
    print(f"  Key 来源 : {src}")
    print(f"  Key 前缀 : {key[:8]}...  长度 {len(key)}")
    print()

    base = "https://api.deepseek.com/v1"

    # ---- 1. /models ----
    print("【1】GET /models —— 确认 Key 有效、看有哪些模型")
    code, body = get(f"{base}/models", key)
    print(f"  HTTP {code}")
    if code == 200:
        try:
            models = [m["id"] for m in json.loads(body).get("data", [])]
            print(f"  可用模型: {models}")
        except Exception:  # noqa: BLE001
            print(f"  {body[:300]}")
    else:
        print(f"  {body[:400]}")
    print()

    # ---- 2. /embeddings ----
    print("【2】POST /embeddings —— **关键**：DeepSeek 提不提供 embedding")
    code, body = post(f"{base}/embeddings", key,
                      {"model": "deepseek-embedding", "input": ["测试"]})
    print(f"  HTTP {code}")
    print(f"  {body[:400]}")
    if code == 200:
        print("  ✅ **这个端点可用** —— 可以直接用它做 RAG 的向量化")
    else:
        print("  ⚠️ 不可用（符合预期 —— DeepSeek 主打对话模型，通常不提供 embedding）")
    print()

    # ---- 3. 极小 chat 调用 ----
    print("【3】POST /chat/completions —— 确认真的能推理（这也是降级链的候选模型）")
    code, body = post(f"{base}/chat/completions", key,
                      {"model": "deepseek-chat",
                       "messages": [{"role": "user", "content": "只回复两个字：可以"}],
                       "max_tokens": 10, "temperature": 0})
    print(f"  HTTP {code}")
    if code == 200:
        try:
            d = json.loads(body)
            print(f"  回复: {d['choices'][0]['message']['content']!r}")
            print(f"  usage: {d.get('usage')}")
            print("  ✅ 对话可用 → 可用于生成层（ADR-006）与 LLM 重排")
        except Exception:  # noqa: BLE001
            print(f"  {body[:300]}")
    else:
        print(f"  {body[:400]}")
    print()

    print("=" * 78)
    print("  结论与下一步")
    print("=" * 78)
    print("  如果 /embeddings 不可用（大概率），RAG 的向量化需要另选来源：")
    print()
    print("    A. 通义 text-embedding-v3      —— DashScope，有免费额度")
    print("    B. 智谱 embedding-3            —— bigmodel.cn，有免费额度")
    print("    C. OpenAI text-embedding-3     —— 本机网络不可达")
    print("    D. 本地 sentence-transformers  —— 需 torch + 模型（BAAI/bge-m3 等）")
    print()
    print("  而 DeepSeek 的 key 仍然非常有用：")
    print("    · **生成层**（ADR-006，带引用回答）—— 这是必须要的")
    print("    · **LLM 重排**（用对话模型给候选打分）—— 可作为神经网络重排的替代")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
