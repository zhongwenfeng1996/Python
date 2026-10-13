#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化"代码块依赖块外定义"的严重程度 —— 决定该修哪些

## 为什么要先量化

审计发现 42 个块引用了块外定义的名字。但**不能一律当缺陷**：

  · 有的名字**上文刚讲过、且离得很近**（两三行前），读者看得见 —— 可接受
  · 有的名字**从来没定义过**（比如 `response`），读者只能猜 —— 真缺陷
  · 有的名字是**行业通称**（`response`、`prompt`），配合正文说明可接受

所以按"这个名字在本文档里有没有被定义过"来分档，
再把结果按**缺陷程度**排序，就能知道该优先修哪些。

## 分档规则

  A. **从来没定义过**（整个文件里都没有 `name =` / `def name` / `class name`
     / `import name`）→ 最严重。读者完全无法自己补出来。
  B. **定义过，但在另一个块里** → 中等。需要往上翻。
  C. **行业通称** → 轻。`response` / `prompt` / `text` 这类，
     只要正文交代了来源就可以接受。

用法：
    python tools/measure_undefined.py
    python tools/measure_undefined.py --chapter 01
"""

from __future__ import annotations

import argparse
import ast
import builtins
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BUILTINS = set(dir(builtins))

#: 行业通称 + 本教程里作为"上一段留下的上下文"的名字。
#: 收进来的理由：它们在 LLM 应用语境里就是"某个众所周知的东西"，
#: 而且正文通常交代了来源。**收得越少越好**（收多了会掩盖真问题）。
COMMON_CONTEXT = {
    "response",     # 调用 API 后的响应字典（第 01 章讲过它的来源）
    "prompt",       # 送给模型的提示词
    "messages",     # 对话消息列表
    "text",         # 待处理的文本
    "data",         # 通用数据
    "result",       # 通用结果
    "usage",        # token 用量
    "request",      # 请求对象 / 请求模块
    "user",         # 用户对象
    "items",        # 通用列表
    "raw",          # 原始字符串
    "model_output", # 模型的原始输出
    "answer",       # 答案
}

CONTINUATION_MARK = "续上文"


def split_blocks(md: str):
    out, in_block, lang, start, buf = [], False, "", 0, []
    for i, line in enumerate(md.splitlines(), 1):
        if line.strip().startswith("```"):
            if in_block:
                out.append((start, lang, "\n".join(buf)))
                in_block, buf = False, []
            else:
                in_block, lang, start, buf = True, line.strip()[3:].strip(), i + 1, []
            continue
        if in_block:
            buf.append(line)
    return out


ERROR_HINTS = ("找出问题", "有什么问题", "哪有问题", "报错", "会报", "❌",
               "不推荐", "反例", "错例", "陷阱", "为什么错", "失败的写法")


def is_error_demo(src: str) -> bool:
    return any(h in "\n".join(src.splitlines()[:6]) for h in ERROR_HINTS)


def names_in(src: str) -> set[str]:
    """块里用到的、非内建的名字。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    defined, used = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
                    defined.add(arg.arg)
                if a.vararg:
                    defined.add(a.vararg.arg)
                if a.kwarg:
                    defined.add(a.kwarg.arg)
        elif isinstance(node, ast.Name):
            (defined if isinstance(node.ctx, ast.Store) else used).add(node.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                defined.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                defined.add(a.asname or a.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, ast.comprehension):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    defined.add(n.id)
    return {n for n in used - defined - BUILTINS if len(n) > 2}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", default="")
    args = ap.parse_args()

    files = sorted(DOCS.glob("*.md"))
    if args.chapter:
        files = [f for f in files if f.name.startswith(args.chapter)]

    print("=" * 92)
    print("  代码块依赖块外定义 —— 按严重程度分档")
    print("=" * 92)
    print()
    print("  A = 整个文件里都没定义过（读者无法自己补）  ← 最该修")
    print("  B = 定义过，但在别的块里（要往上翻）")
    print("  C = 行业通称（正文交代过就可接受）")
    print()

    tot = {"A": 0, "B": 0, "C": 0}
    a_cases: list[str] = []

    for f in files:
        md = f.read_text(encoding="utf-8")
        # 全文里"定义过"的名字（粗粒度：出现在赋值/def/class/import 左边）
        whole = set(re.findall(r"^\s*(\w+)\s*=", md, re.M))
        whole |= set(re.findall(r"^\s*def\s+(\w+)", md, re.M))
        whole |= set(re.findall(r"^\s*class\s+(\w+)", md, re.M))
        whole |= set(re.findall(r"^\s*import\s+(\w+)", md, re.M))
        whole |= set(re.findall(r"^\s*from\s+\w+\s+import\s+([\w, ]+)", md, re.M))
        whole = {x.strip() for part in whole for x in part.split(",")}

        rows = []
        for start, lang, src in split_blocks(md):
            if lang not in ("python", "py"):
                continue
            if is_error_demo(src) or CONTINUATION_MARK in src:
                continue
            try:
                compile(src, f, "exec")
            except SyntaxError:
                continue
            unknown = names_in(src)
            if not unknown:
                continue
            cat = {"A": [], "B": [], "C": []}
            for n in sorted(unknown):
                if n in COMMON_CONTEXT:
                    cat["C"].append(n)
                elif n in whole:
                    cat["B"].append(n)
                else:
                    cat["A"].append(n)
            if cat["A"] or cat["B"]:
                rows.append((start, cat))

        if not rows:
            continue
        print(f"  ── {f.name}")
        for start, cat in rows:
            parts = []
            for k in ("A", "B", "C"):
                if cat[k]:
                    parts.append(f"{k}:{'/'.join(cat[k])}")
                    tot[k] += 1 if k != "C" else 0
            print(f"       第 {start:>4} 行   " + "   ".join(parts))
            if cat["A"]:
                a_cases.append(f"{f.name}:{start}  A = {cat['A']}")
        print()

    print("=" * 92)
    print(f"  含 A 类名字的块：{tot['A']} 个   ·   含 B 类的块：{tot['B']} 个")
    print()
    if a_cases:
        print("  ★ 优先修这些（A 类：文件里从没定义过）:")
        for x in a_cases:
            print(f"      {x}")
        print()
    print("  修法不必大改：在块的开头加一行注释交代来源 + 最小可运行的前置，")
    print("  比空口说'上下文里有'有用得多。")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
