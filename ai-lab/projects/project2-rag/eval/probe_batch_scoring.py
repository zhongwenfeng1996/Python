#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证"批量打分"方案 —— 减少 API 调用次数与推理 token 开销

## 为什么要批量

逐条打分（pointwise）：每查询 N 个候选 = N 次调用。
实测每次调用要烧 150-220 个 reasoning token（两个模型都是推理模型），
所以 50 个候选 × 500 token = 25000 token/查询，52 题就是 130 万 token。

批量打分（listwise 的另一种形式）：一次给模型 5~10 个候选，
让它输出每个的分数。调用次数降到 1/5 ~ 1/10，
而且**推理开销被摊薄到多个候选上**。

## 风险（必须验证，不能假设）

  · 一次问多个，模型可能只答前几个 —— 要检查输出条数是否匹配
  · 候选多了，模型的注意力会分散，分数质量可能下降
  · 输出格式必须好解析（要求 JSON）

这个脚本**实测**三件事：
  1. 批量输出能否稳定解析（条数对不对）
  2. 同一次批量里的分数是否与逐条打分一致
  3. 省了多少 token

用法：
    python eval/probe_batch_scoring.py
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
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
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
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# 待打分的候选（人为构造：1 个高度相关 + 若干无关，看模型能否区分）
QUESTION = "章节重排后 def 在第几章教？"
CANDIDATES = [
    "## 04 · 函数\n本章讲 def 定义函数、参数与返回值。章节重排后函数从第 5 章提到第 4 章，因为作用域必须用到 def。",
    "## 07 · 模块与虚拟环境\nimport 的三种形式，以及 pip 与 venv 的用法。",
    "## 02 · 字符串与格式化\nf-string 的写法，以及用 format 拼接。",
    "## 09 · 异步与并发\nasync def 与 asyncio.gather 的用法。",
    "## 05 · 条件、循环与作用域\nPython 只有函数级作用域，循环变量会泄漏到外面。",
]

SYSTEM_BATCH = """你是一个检索相关性评分员。我会给你一个问题，以及若干个候选文档片段。
请为每个片段判断它对回答该问题的帮助程度。

评分标准：
0-2 分：完全无关，或只是碰巧出现了相同的词
3-5 分：提到了相关概念，但没有回答问题所需的具体信息
6-8 分：包含回答问题所需的主要信息
9-10 分：直接、完整地回答了问题

只输出一个 JSON 数组，例如 [8, 2, 0, 5, 3]，元素个数必须与候选个数一致。
不要输出任何解释文字。"""

USER_BATCH = """问题：{question}

{passages}

请为上面 {n} 个片段逐个打分，输出 {n} 个数字的 JSON 数组："""


def main() -> int:
    env = load_env()
    key = os.environ.get("DEEPSEEK_API_KEY") or env.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("OPENAI_BASE_URL") or env.get(
        "OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    if not key:
        print("❌ 没有 Key")
        return 1

    print("=" * 84)
    print("  批量打分方案验证")
    print("=" * 84)
    print(f"  问题: {QUESTION}")
    print(f"  候选数: {len(CANDIDATES)}（第 1 个高度相关，第 5 个部分相关，2-4 无关）")
    print()

    # ---- 方案 A：逐条打分（基线，token 消耗大） ----
    print("【方案 A】逐条打分（现状）")
    a_total = 0
    a_scores = []
    for i, c in enumerate(CANDIDATES, 1):
        user = (f"问题：{QUESTION}\n\n文档内容：\n{c}\n\n"
                f"这段文档对回答该问题的帮助程度是几分（只输出数字）：")
        r = call("deepseek-flash", key, base, 256, SYSTEM_BATCH if False else
                 "你是检索相关性评分员。只输出 0-10 的一个数字。", user)
        if r["ok"]:
            m = re.search(r"\b(10|\d)\b", r["content"])
            s = float(m.group(1)) if m else None
            a_scores.append(s)
            a_total += r["usage"].get("total_tokens", 0)
            print(f"  片段{i}: 分数={s}  tokens={r['usage'].get('total_tokens')}  "
                  f"耗时={r['seconds']}s")
        else:
            print(f"  片段{i}: ❌ {r['error']}")
            a_scores.append(None)
    print(f"  → 合计 {a_total} tokens，{len(CANDIDATES)} 次调用")
    print()

    # ---- 方案 B：一次批量打分 ----
    print("【方案 B】一次批量打分")
    passages = "\n\n".join(f"[{i}] {c}" for i, c in enumerate(CANDIDATES, 1))
    user = USER_BATCH.format(question=QUESTION, passages=passages,
                             n=len(CANDIDATES))
    r = call("deepseek-flash", key, base, 700, SYSTEM_BATCH, user)
    if r["ok"]:
        print(f"  finish={r['finish']}  耗时={r['seconds']}s  "
              f"tokens={r['usage'].get('total_tokens')}")
        print(f"  原始输出: {r['content'][:200]!r}")
        m = re.search(r"\[[^\]]*\]", r["content"])
        b_scores = None
        if m:
            try:
                b_scores = json.loads(m.group(0))
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ JSON 解析失败: {e}")
        if b_scores is not None:
            print(f"  解析成功: {b_scores}  条数={len(b_scores)} "
                  f"{'✅ 匹配' if len(b_scores) == len(CANDIDATES) else '❌ 条数不符'}")
        print()
        print("  两者对比：")
        for i in range(len(CANDIDATES)):
            av = a_scores[i] if i < len(a_scores) else None
            bv = b_scores[i] if b_scores and i < len(b_scores) else None
            mark = "✅" if av == bv else ("≈" if av is not None and bv is not None
                                          and abs(av - bv) <= 1 else "⚠️")
            print(f"    片段{i+1}: 逐条={av}  批量={bv}  {mark}")
        print()
        if b_scores:
            print(f"  Token 对比: 逐条 {a_total}  ->  批量 "
                  f"{r['usage'].get('total_tokens')}  "
                  f"（省 {100*(1 - r['usage'].get('total_tokens', 1)/max(a_total,1)):.0f}%）")
            print(f"  调用次数  : {len(CANDIDATES)}  ->  1（省 "
                  f"{100*(1 - 1/len(CANDIDATES)):.0f}%）")
    else:
        print(f"  ❌ {r['error']}")

    print()
    print("=" * 84)
    print("  判定标准")
    print("=" * 84)
    print("  · 条数匹配 + 排序合理（片段1 分数最高）→ 批量方案可用")
    print("  · 条数不匹配或分数错乱 → 退回逐条，但要把 max_tokens 提到 256+")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
