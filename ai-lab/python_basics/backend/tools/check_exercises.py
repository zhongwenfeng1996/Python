#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
练习区结构检查 —— 防止"答案泄漏到题干"和"半成品代码当提示"

## 为什么加这个检查（踩过的真实问题）

读者反馈"有些代码敲下来会报错、或者看着像答案又不是"。查下去发现两类：

  A. **答案泄漏到题干**：练习 1 问"用推导式统计条数"，
     下面却直接给了 `men_len = len([...])`。
     —— 这个还出现过**两个副本**，我第一次只删掉了一个。

  B. **半成品代码当提示**：某题的"代码"本身语法是坏的
     （`try` 少了冒号、tab 与空格混用），读者既不能跑、
     也不确定是不是要填空。

两类都源于同一个模糊：**练习区的代码块到底是"题干""答案"还是"提示"？**

## 本教程的约定（本脚本强制检查）

在每个 `## 练习` 区里：

  · **题干**只放"输入数据"或"待改的错代码"，**不放解法**
  · **答案**一律放进 `<details><summary>参考答案</summary>` 里
  · 代码块**不能**出现在 `<details>` 里还带坏语法
    （答案要能直接跑；故意错例应放在题干里并写明"找出问题"）

判据（可机械执行）：

  1. **`## 练习` 到文末之间**，每个 ```python 块要么在 `<details>` 内，
     要么**不含赋值给新变量的多行解法**（启发式：块里出现
     `for`/`while`/`if` 且块长 > 3 行 → 疑似解法）
  2. `<details>` 内的代码块**必须语法正确**（答案要能跑）
  3. 题干区的代码块**要么语法正确、要么**紧邻的文字里写了"找出问题/报错"

用法：
    python tools/check_exercises.py
    python tools/check_exercises.py --show   # 列出每个练习区的块与判定
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

EXERCISE_HINT = "找出问题"
SOLUTION_HINTS = ("def ", "for ", "while ", "if ", "return ")

#: 显式标记"这题故意给了完整答案" —— 由作者写在题干附近。
#:
#: ## 为什么用显式标记，而不是把判据放宽
#:
#: 第一版靠"题目文字里有没有'有什么问题'"来判断块是不是故意错例，
#: 结果漏掉了 `01/3`（它的题干写的是"下面这段判断在生产代码里可能有问题"，
#: 不含那几个词）。更麻烦的是真实泄漏（`01/1` 把输出写在三引号里）
#: 和这种故意示例在结构上长得一样，靠词表区分迟早出错。
#:
#: 所以：**故意的情况由作者显式标注**，没标注的一律按问题报。
#: 这个取舍和 scope_check 的原则一致（宁可漏判也不能误判，
#: 但这里反过来 —— 教学文档里"漏掉一个真泄漏"比"多报一个假阳性"更糟，
#: 所以宁可让作者多写一行标记）。
INTENTIONAL_MARK = "<!-- 这题刻意给答案 -->"


def sections(md: str):
    """把文档切成 [(小节标题, 该节文本)]。"""
    out = []
    title = "(开头)"
    buf: list[str] = []
    for line in md.splitlines():
        if line.startswith("## "):
            out.append((title, "\n".join(buf)))
            title, buf = line[3:].strip(), []
        else:
            buf.append(line)
    out.append((title, "\n".join(buf)))
    return out


def blocks(text: str):
    """产出 (起始行偏移, 语言, 代码, 是否在 details 内)。"""
    out, in_block, lang, buf = [], False, "", []
    details_depth = 0
    for line in text.splitlines():
        low = line.strip().lower()
        if low.startswith("<details"):
            details_depth += 1
        elif low.startswith("</details"):
            details_depth = max(0, details_depth - 1)
        if line.strip().startswith("```"):
            if in_block:
                out.append((lang, "\n".join(buf), details_depth > 0))
                in_block, buf = False, []
            else:
                in_block, lang, buf = True, line.strip()[3:].strip(), []
            continue
        if in_block:
            buf.append(line)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    print("=" * 90)
    print("  练习区结构检查 —— 答案有没有泄漏到题干 / 答案能不能跑")
    print("=" * 90)
    print()

    problems: list[str] = []
    for f in sorted(DOCS.glob("*.md")):
        md = f.read_text(encoding="utf-8")
        ex = [(t, body) for t, body in sections(md) if t.startswith("练习")]
        if not ex:
            continue
        n_q = n_a = 0
        for title, body in ex:
            for lang, src, in_details in blocks(body):
                if lang not in ("python", "py"):
                    continue
                lines = [x for x in src.splitlines() if x.strip()]
                # ① details 内的答案必须语法正确
                if in_details:
                    n_a += 1
                    try:
                        compile(src, f.name, "exec")
                    except SyntaxError as e:
                        problems.append(
                            f"{f.name} [{title}] 参考答案语法错：{e.msg}")
                    continue
                n_q += 1
                # 题目显式声明"刻意给答案" → 放过
                idx0 = body.find(src)
                before = body[max(0, idx0 - 260): idx0]
                if INTENTIONAL_MARK in before:
                    continue
                # ② 题干区：块若疑似"多行解法"就报警
                looks_solution = (
                    len(lines) >= 4
                    and any(h in src for h in SOLUTION_HINTS)
                    and not any(h in src for h in ("在这里写", "你的代码",
                                                   "TODO", "待补"))
                )
                if looks_solution:
                    # 排除"待改的错代码"：附近文字应说明
                    around = before
                    if EXERCISE_HINT not in around and "有什么问题" not in around \
                            and "可能有问题" not in around and "为什么" not in around:
                        problems.append(
                            f"{f.name} [{title}] 题干区疑似**直接给了答案**"
                            f"（{len(lines)} 行、含 {[h for h in SOLUTION_HINTS if h in src]}）"
                            f"\n          首行：{lines[0][:70]}"
                            f"\n          若这是刻意的，请在题干上方加 {INTENTIONAL_MARK}")
                # ③ 题干区的坏语法必须紧邻"找出问题"
                try:
                    compile(src, f.name, "exec")
                except SyntaxError as e:
                    idx = body.find(src)
                    around = body[max(0, idx - 220): idx]
                    if not any(h in around for h in
                               (EXERCISE_HINT, "有什么问题", "会报错", "故意")):
                        problems.append(
                            f"{f.name} [{title}] 题干区代码语法错：{e.msg}"
                            f"\n          首行：{lines[0][:70] if lines else ''}")
        if args.show:
            print(f"  {f.name:<32} 题干块 {n_q:>2}  答案块 {n_a:>2}")

    print("=" * 90)
    if problems:
        print(f"  发现 {len(problems)} 个问题：")
        print()
        for p in problems:
            print(f"   ❌ {p}")
    else:
        print("  ✅ 练习区结构正常：题干没有给答案，参考答案语法正确")
    print()
    print("  约定：")
    print("    · 题干只放「输入数据」或「待改的错代码」")
    print("    · 解法一律放进 <details><summary>参考答案</summary>")
    print("    · 参考答案要能直接跑（它是给人对照的）")
    print("=" * 90)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
