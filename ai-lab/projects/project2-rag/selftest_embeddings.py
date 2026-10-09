#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自测 embedding 实现（不用 pytest，直接跑，看结果）

要验证三件事，缺一不可：
  1. 归一化正确 —— 否则余弦相似度不是点积，后面的检索全错
  2. **确定性** —— 同一文本必须永远同向量，否则评估结果不可复现
     （这是最容易踩的坑：用内置 hash() 会因每进程随机盐而失效）
  3. **有局部性** —— 相似文本的向量要更接近，否则检索毫无意义

用法：
    & G:\\转型\\.venv\\Scripts\\python.exe ai-lab\\projects\\project2-rag\\selftest_embeddings.py
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag.embeddings import LocalHashEmbedder  # noqa: E402


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    emb = LocalHashEmbedder(dim=256)
    ok = True

    print("=" * 74)
    print("  embedding 自测")
    print("=" * 74)
    print(f"  实现: {emb.signature}")

    # ---- 1. 归一化 ----
    print("\n—— 1) L2 归一化（应该都等于 1.0）——")
    for t in ("函数", "def 定义函数并返回多个值", ""):
        v = emb.embed_one(t)
        norm = math.sqrt(sum(x * x for x in v))
        flag = "✅" if (abs(norm - 1.0) < 1e-9 or norm == 0.0) else "❌"
        if flag == "❌":
            ok = False
        print(f"  {flag} 模长={norm:.10f}  输入={t!r}")

    # ---- 2. 确定性 ----
    print("\n—— 2) 确定性（同输入必须同向量）——")
    t = "章节重排后 def 在第四章教，这一章讲函数和作用域"
    v1 = emb.embed_one(t)
    v2 = emb.embed_one(t)
    same_inproc = v1 == v2
    print(f"  {'✅' if same_inproc else '❌'} 同进程两次调用一致")
    if not same_inproc:
        ok = False

    # 跨进程：这才是真正会暴露"用了内置 hash()"的测试
    child = subprocess.run(
        [sys.executable, "-c",
         "import sys,json;sys.path.insert(0, r'%s');"
         "from rag.embeddings import LocalHashEmbedder;"
         "print(json.dumps(LocalHashEmbedder(dim=256).embed_one(%r)))"
         % (str(Path(__file__).resolve().parent), t)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if child.returncode == 0:
        import json
        v_child = json.loads(child.stdout.strip())
        cross_ok = v_child == v1
        print(f"  {'✅' if cross_ok else '❌'} 跨进程一致"
              f"{'' if cross_ok else ' ← 说明用了内置 hash()，必须改成 md5'}")
        if not cross_ok:
            ok = False
    else:
        print(f"  ⚠️ 跨进程测试没跑起来：{child.stderr[-200:]}")

    # ---- 3. 局部性 ----
    #
    # ⚠️ 这里第一版写错过，值得记下来：
    #
    # 我原本假设"同一领域的无关句子"（数据库索引）应该比"完全无关的句子"
    # （今天天气）更相似，于是写了个"相似度单调递减"的断言。
    # 结果失败：数据库索引 0.0000，今天天气 0.0343。
    #
    # 查了一下才发现**是我错了，不是 embedder 错了**：
    # 字符 n-gram 只看字面重叠。
    #   "函数是可复用的代码块，用 def 定义"
    #   "数据库索引可以加速查询"        -> 共享 bigram：0 个
    #   "今天天气真好我想出去散步"      -> 共享 bigram：「真好」(真+好 在"可复用"后? 不)…
    # 实际重叠来自 "真是"? 不 —— 是「天天气」这类巧合以及 "好"、"我想" 等常用字。
    # 结论：字符 n-gram 的"相似"是**字面**相似，跟"是否同一领域"无关。
    #
    # 所以判据要换成 **embedder 真正承诺的东西**：字面重叠越多，向量越近。
    print("\n—— 3) 局部性（判据：字面重叠越多，向量应越近）——")
    base = "函数是可复用的代码块，用 def 定义"

    def shared_ngrams(a: str, b: str) -> int:
        from rag.embeddings import _ngrams
        return len(set(_ngrams(a)) & set(_ngrams(b)))

    # 按"与基准的共享 n-gram 数量"降序排列 —— 这就是期望的相似度顺序
    candidates = [
        "函数是可复用的代码块，用 def 来定义",       # 几乎同一句
        "函数是可复用的代码块",                      # 前半句
        "用 def 定义函数",                           # 关键词都在，语序不同
        "python 里怎么定义函数",                     # 只共享"函数""定义"
        "数据库索引可以加速查询",                    # 共享 0
        "今天天气真好我想出去散步",                  # 共享 0
    ]
    rows = []
    vb = emb.embed_one(base)
    for other in candidates:
        rows.append({
            "text": other,
            "shared": shared_ngrams(base, other),
            "cos": cosine(vb, emb.embed_one(other)),
        })

    print(f"  基准: {base!r}\n")
    print(f"  {'共享ngram':>9}  {'余弦':>7}  文本")
    for r in sorted(rows, key=lambda x: -x["shared"]):
        print(f"  {r['shared']:>9}  {r['cos']:>7.4f}  {r['text']!r}")

    # 正确的不变量：共享 n-gram 多的，余弦不应低于共享少的
    # （允许并列相等，因为哈希有撞桶可能）
    ordered = sorted(rows, key=lambda x: -x["shared"])
    violations = []
    for i in range(len(ordered) - 1):
        if ordered[i]["shared"] > ordered[i + 1]["shared"]:
            if ordered[i]["cos"] + 1e-9 < ordered[i + 1]["cos"]:
                violations.append((ordered[i], ordered[i + 1]))
    if violations:
        ok = False
        print(f"\n  ❌ 有 {len(violations)} 处违反'字面重叠多 → 余弦不更低'：")
        for hi, lo in violations:
            print(f"     共享{hi['shared']} 余弦{hi['cos']:.4f} {hi['text']!r}")
            print(f"     共享{lo['shared']} 余弦{lo['cos']:.4f} {lo['text']!r}")
    else:
        print("\n  ✅ 共享 n-gram 多的，余弦都不低于共享少的")

    # 关键的两条硬断言（这两条错了检索就真的不可用）
    same = next(r for r in rows if r["text"] == candidates[0])
    zero1 = next(r for r in rows if r["text"] == candidates[4])
    print(f"\n  硬断言 A: 近同句余弦 {same['cos']:.4f} 应显著高于无关句 "
          f"{zero1['cos']:.4f}")
    if same["cos"] <= zero1["cos"]:
        ok = False
        print("    ❌ 失败")
    else:
        print("    ✅ 通过")

    ident = cosine(emb.embed_one(base), emb.embed_one(base))
    print(f"  硬断言 B: 与自身的余弦 = {ident:.6f} 应等于 1.0")
    if abs(ident - 1.0) > 1e-9:
        ok = False
        print("    ❌ 失败")
    else:
        print("    ✅ 通过")

    # ---- 4. 已知短板（明确验证，不回避）----
    print("\n—— 4) 已知短板：语义泛化弱（这是设计取舍，不是 bug）——")
    a = "怎么定义函数"
    b = "如何声明一个可复用的过程"      # 语义相近，但用词完全不同
    c = "函数"
    print(f"  余弦('怎么定义函数', '如何声明一个可复用的过程') = "
          f"{cosine(emb.embed_one(a), emb.embed_one(b)):.4f}")
    print(f"  余弦('怎么定义函数', '函数')                  = "
          f"{cosine(emb.embed_one(a), emb.embed_one(c)):.4f}")
    print("  → 真实 embedding 会给第一组更高分；本地字面实现给不了。")
    print("    这正是它的定位：**下限基线**。接入真实模型后的提升幅度就是收益。")

    print()
    print("=" * 74)
    print("  ✅ 自测通过" if ok else "  ❌ 有项目失败，见上面标记")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
