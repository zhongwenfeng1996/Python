#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探测两个 DeepSeek 模型的输出特性 —— 特别是**推理模型 vs 普通模型**

## 背景（实测发现的）

`deepseek-flash` 在 max_tokens=4 时返回：
    "content": ""                                      <- 空的
    "reasoning_content": "我们需要回答用户中文"          <- 思维链
    "finish_reason": "length"                           <- 被截断

**它是推理模型**：先吐思维链，再吐最终答案。
所以给它极小的 max_tokens，答案还没开始写就截断了。

这解释了之前"36 次调用失败 35 次"的假象 —— 根本不是失败，是预算不够。

## 这个脚本要确定的事

1. 两个模型各自是不是推理模型（看有没有 reasoning_content）
2. 需要多大的 max_tokens 才能拿到稳定的最终答案
3. 打分任务该用哪个模型、多大预算、多少 token 成本

用法：
    python eval/probe_models.py
"""

from __future__ import annotations

import json
import os
import sys
import time
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
         system: str, user: str) -> dict:
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
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode("utf-8"))
            dt = time.time() - t0
            ch = d["choices"][0]
            msg = ch["message"]
            return {
                "ok": True,
                "content": msg.get("content") or "",
                "reasoning": msg.get("reasoning_content") or "",
                "finish": ch.get("finish_reason"),
                "usage": d.get("usage", {}),
                "seconds": round(dt, 2),
            }
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


SYSTEM = ("你是一个检索相关性评分员。判断文档对回答问题的帮助程度，"
          "按 0-2 无关 / 3-5 沾边 / 6-8 含主要信息 / 9-10 直接回答 打分。"
          "只输出一个数字。")
USER = ("问题：章节重排后 def 在第几章教？\n\n"
        "文档内容：\n## 04 · 函数\n本章讲 def 定义函数、参数、返回值与作用域。\n\n"
        "这段文档对回答该问题的帮助程度是几分（只输出数字）：")


def main() -> int:
    env = load_env()
    key = os.environ.get("DEEPSEEK_API_KEY") or env.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("OPENAI_BASE_URL") or env.get(
        "OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    if not key:
        print("❌ 没有 Key")
        return 1

    print("=" * 84)
    print("  DeepSeek 模型输出特性探测")
    print("=" * 84)
    print(f"  base_url: {base}")
    print()

    models = ["deepseek-flash", "deepseek-v4-pro"]
    budgets = [4, 64, 256, 1024]

    for m in models:
        print(f"【{m}】")
        for mt in budgets:
            r = call(m, key, base, mt, SYSTEM, USER)
            if not r["ok"]:
                print(f"  max_tokens={mt:<5} ❌ {r['error']}")
                continue
            has_reason = bool(r["reasoning"])
            content = r["content"].strip()
            u = r["usage"]
            print(f"  max_tokens={mt:<5} finish={r['finish']:<8} "
                  f"推理={'有' if has_reason else '无'}  "
                  f"content={content[:22]!r}")
            print(f"      usage: prompt={u.get('prompt_tokens')} "
                  f"completion={u.get('completion_tokens')} "
                  f"reasoning_tokens={u.get('completion_tokens_details', {}).get('reasoning_tokens', '-')} "
                  f"耗时={r['seconds']}s")
        print()

    print("=" * 84)
    print("  结论要点")
    print("=" * 84)
    print("  · 若某模型一直 finish=length 且 content 为空 → 它是推理模型，")
    print("    必须先给足 reasoning 的预算，再谈输出答案")
    print("  · 打分任务要的是**稳定、便宜、快**：选无推理的模型或给足预算")
    print("  · 重排的调用量很大（每查询 N 个候选），单次成本必须算清楚")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
