#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描代码块里的三引号字符串 —— 找"拿三引号当正文/注释块"的坏用法

## 为什么要单独查这个

`08` 章出过一个问题：正文被三引号字符串包进了代码块里，
结果 Markdown 标题、讲解文字全变成了 Python 字符串字面量。
读者既读不到讲解，也看不清代码结构。

更隐蔽的是：三引号字符串作为字面量，语法是合法的 ——
`compile()` 不报错，所以"语法错"检查抓不到它。

## 判据

三引号字符串分两种，要区分开：

  · **合法 docstring**：紧跟 def/class 之后，描述函数/类 —— 好的
  · **坏用法**：三引号字符串里包的是"给人看的正文"（含 Markdown 标记、
    完整句子、`##` 标题等），它本该在代码块**外面** —— 坏的

坏用法的特征：三引号里的内容含 Markdown 标记（`**`、`##`、`>`、`|`、
`- `）或中文字符占比高且是完整句子。

用法：
    python tools/scan_triple_quotes.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

#: 三引号内容里出现这些 **Markdown 结构标记** → 疑似"正文被包进代码"。
#:
#: ⚠️ 第一版把 `。`、`；`、`（`、`第`、`章`、`注意`、`为什么` 也算了进去，
#:    结果把**合法的函数 docstring**（含中文句号）和**多行 prompt 模板**
#:    （含"请根据资料回答问题"）全误报了 —— 20 处里 18 处是假阳性。
#:
#:    关键区分：docstring / prompt 是**给人或模型看的自然语言**，
#:    但不会出现 Markdown 结构（`##` 标题、`**加粗**`、`> 引用`、`| 表格`）。
#:    而"正文被包进代码"的特征恰恰是**这些 Markdown 结构**混在代码里。
#:
#: ⚠️ 不要放 `1. `、`- ` 这类列表标记 —— 多行 prompt 里给模型列规则
#:    （"1. 先给结论 2. 涉及数字说明口径"）用编号太常见了，会误报。
#:    只认**只在 Markdown 正文里出现、prompt 里几乎不会写**的结构。
MD_HINTS = ("## ", "**", "> ", "| ", "- [", "<details", "</summary",
            "```", "http://", "https://")


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


def triple_content(src: str):
    """抽出三引号字符串的内容（含位置）。返回 (内容, 是否未闭合)。"""
    hits = []
    # 非贪婪匹配成对的三引号
    for m in re.finditer(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', src):
        body = m.group(0)[3:-3]
        hits.append((body, False))
    # 剩下的孤立三引号 = 未闭合
    stripped = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "", src)
    if '"""' in stripped or "'''" in stripped:
        hits.append((stripped, True))
    return hits


def main() -> int:
    print("=" * 92)
    print("  三引号扫描 —— 找'Markdown 正文被包进代码块'的坏用法")
    print("=" * 92)
    print()
    bad = 0
    for f in sorted(DOCS.glob("*.md")):
        md = f.read_text(encoding="utf-8")
        for start, lang, src in split_blocks(md):
            if lang not in ("python", "py"):
                continue
            for body, unclosed in triple_content(src):
                if unclosed:
                    print(f"  ⚠️ {f.name}:{start}  有**未闭合**的三引号")
                    bad += 1
                    continue
                md_hits = [h for h in MD_HINTS if h in body]
                if md_hits:
                    print(f"  ❌ {f.name}:{start}  三引号里疑似 Markdown 正文"
                          f"（含 {md_hits[:4]}）")
                    print(f"     内容预览: {body[:70]!r}")
                    bad += 1
    print()
    print("=" * 92)
    if bad:
        print(f"  发现 {bad} 处可疑 —— 需要人工确认哪些是坏用法")
    else:
        print("  ✅ 没有发现'Markdown 正文被包进代码块'的坏用法")
    print()
    print("  说明：合法的 docstring / 多行 prompt 不会被报 ——")
    print("  只有当三引号内容里含 Markdown 结构（标题/加粗/引用/表格）时才提示。")
    print("=" * 92)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
