#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
找出"批量打分"的可用参数组合 —— 批次大小 × 候选截断长度 × max_tokens

## 为什么必须实测

踩了两次同一个坑：
  1. max_tokens=4  → 推理模型连思维链都没写完，content 永远空
  2. max_tokens=900 + 10 个候选 × 800 字符 → **又不够**，content 还是空

原因：推理模型的 token 消耗 = 输入提示 + **大量推理** + 少量答案。
输入越大，推理也越长。所以"批量"不是越大越好 —— 有一个天花板。

这个脚本用**真实的候选文本**扫一遍参数网格，找出：
  · 哪些组合能稳定拿到完整结果
  · 每个组合的 token 成本

用法：
    python eval/probe_batch_limits.py
"""

from __future__ import annotations

import json
import os
import re
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
sys.path.insert(0, str(HERE.parent))


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


SYSTEM = """你是一个检索相关性评分员。我会给你一个问题，以及若干个候选文档片段。
请为每个片段判断它对回答该问题的帮助程度。

评分标准：
0-2 分：完全无关，或者只是碰巧出现了相同的词
3-5 分：提到了相关概念，但没有回答问题所需的具体信息
6-8 分：包含回答问题所需的主要信息
9-10 分：直接、完整地回答了问题

只输出一个 JSON 数组，例如 [8, 2, 0, 5, 3]，元素个数必须与候选个数一致。
不要输出任何解释文字。"""

USER = """问题：{question}

{passages}

请为上面 {n} 个片段逐个打分，输出 {n} 个数字的 JSON 数组："""


def call(model: str, key: str, base: str, max_tokens: int,
         user: str) -> dict:
    body = {"model": model,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": user}],
            "temperature": 0.0, "max_tokens": max_tokens}
    req = urllib.request.Request(f"{base.rstrip('/')}/chat/completions",
                                 data=json.dumps(body).encode("utf-8"),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {key}")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read().decode("utf-8"))
            ch = d["choices"][0]
            return {"ok": True,
                    "content": (ch["message"].get("content") or "").strip(),
                    "finish": ch.get("finish_reason"),
                    "usage": d.get("usage", {}),
                    "seconds": round(time.time() - t0, 2)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:150]}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main() -> int:
    # 用**真实语料**的块，而不是编造的短文 —— 真实长度才有参考价值
    from rag.chunking import chunk_document
    from rag.corpus import load_corpus

    docs = load_corpus()
    chunks = []
    for d in docs[:6]:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))
    real = [c.text for c in chunks[:20]]
    if len(real) < 20:
        print("  语料块不够，稍等")
        return 1

    question = "章节重排后 def 在第几章教？"

    env = load_env()
    key = os.environ.get("DEEPSEEK_API_KEY") or env.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("OPENAI_BASE_URL") or env.get(
        "OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    if not key:
        print("❌ 没有 Key")
        return 1

    print("=" * 92)
    print("  批量打分参数网格 — 找可用组合")
    print("=" * 92)
    print(f"  用真实语料块（平均 {sum(len(c) for c in real)//len(real)} 字符）")
    print()
    print(f"  {'批大小':>6} {'截断':>6} {'max_tok':>8} | {'结果':>6} {'条数':>5} "
          f"{'prompt':>7} {'compl':>6} {'推理':>6} {'耗时':>6} | 样例")
    print("  " + "-" * 88)

    for batch in (5, 10, 15):
        for max_chars in (400, 800):
            for max_tokens in (1500, 2500):
                picked = [c[:max_chars] for c in real[:batch]]
                passages = "\n\n".join(
                    f"[{i}] {p}" for i, p in enumerate(picked, 1))
                r = call("deepseek-flash", key, base, max_tokens,
                         USER.format(question=question, passages=passages, n=batch))
                if not r["ok"]:
                    print(f"  {batch:>6} {max_chars:>6} {max_tokens:>8} | ❌ {r['error'][:60]}")
                    continue
                m = re.search(r"\[[^\]]*\]", r["content"])
                cnt = -1
                sample = r["content"][:24]
                if m:
                    try:
                        cnt = len(json.loads(m.group(0)))
                    except Exception:  # noqa: BLE001
                        cnt = -2
                u = r["usage"]
                rt = u.get("completion_tokens_details", {}).get("reasoning_tokens", "-")
                flag = "✅" if cnt == batch else "❌"
                print(f"  {batch:>6} {max_chars:>6} {max_tokens:>8} | "
                      f"{flag:>6} {cnt:>5} {u.get('prompt_tokens', 0):>7} "
                      f"{u.get('completion_tokens', 0):>6} {str(rt):>6} "
                      f"{r['seconds']:>6} | {sample!r}")

    print()
    print("=" * 92)
    print("  读法：")
    print("    · 找**条数匹配（✅）**的组合，再选 token 最省的那个")
    print("    · 推理 token 会随输入增长 —— 这不是 bug，是推理模型的行为")
    print("    · 若所有大批次都失败，就把 batch_size 降下来，用次数换稳定性")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
