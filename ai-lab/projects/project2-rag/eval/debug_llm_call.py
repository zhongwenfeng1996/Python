#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单次 API 调用诊断 —— 看真实的 HTTP 错误是什么

背景：LLM 重排并发 6 时 36 次调用失败 35 次。而原来的异常处理把失败
静默记成 0 分，所以看不到原因。这个脚本做**一次**调用并把完整错误打出来。

用法：
    python eval/debug_llm_call.py
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

HERE = Path(__file__).resolve().parent


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    p = HERE.parent / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def call(model: str, key: str, base: str, max_tokens: int,
         system: str, user: str) -> None:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(f"{base.rstrip('/')}/chat/completions",
                                 data=json.dumps(body).encode("utf-8"),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {key}")
    print(f"  → model={model}  max_tokens={max_tokens}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8")
            print(f"  HTTP {r.status}")
            print(f"  {raw[:300]}")
            try:
                d = json.loads(raw)
                print(f"  回复内容: {d['choices'][0]['message']['content']!r}")
            except Exception:  # noqa: BLE001
                pass
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTP {e.code}")
        print(f"  {e.read().decode('utf-8', 'replace')[:500]}")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {type(e).__name__}: {e}")
    print()


def main() -> int:
    env = load_env()
    key = os.environ.get("DEEPSEEK_API_KEY") or env.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("OPENAI_BASE_URL") or env.get(
        "OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    if not key:
        print("❌ 没有 Key")
        return 1

    print("=" * 78)
    print("  单次 API 调用诊断")
    print("=" * 78)
    print(f"  base_url: {base}")
    print(f"  key 长度: {len(key)}")
    print()

    sysp = "只输出一个数字。"
    # 试三种：极短输出 / 重排用的真实提示词 / 更长输出
    call("deepseek-flash", key, base, 4, sysp, "1+1 等于几？只输出数字")
    call("deepseek-flash", key, base, 4,
         "你是一个检索相关性评分员。判断文档对回答问题的帮助程度，0-10 分，只输出数字。",
         "问题：怎么定义函数\n\n文档内容：\ndef greet(name):\n    return f'hi {name}'\n\n这段文档对回答该问题的帮助程度是几分（只输出数字）：")
    call("deepseek-flash", key, base, 16, sysp, "从 1 数到 3，用逗号分开")

    print("=" * 78)
    print("  读法：")
    print("    · 如果 max_tokens=4 失败但 max_tokens=16 成功 → 是该模型对极短输出有限制")
    print("    · 如果全部 429 → 是限流，需要降并发/加退避")
    print("    · 如果全部 400 且提示参数错误 → 提示词或参数格式有问题")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
