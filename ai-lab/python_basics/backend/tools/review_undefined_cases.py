#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 audit_code_blocks 报出的"依赖块外定义"逐个打出来审查

针对每个可疑块，打印：
  · 该块完整代码
  · 块前面 12 行（题干/正文，判断它是不是故意演示、正文有没有交代来源）
  · 块后面 4 行

这样能一次性判断"这一处到底是该修，还是正文已经解释清楚了"。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

#: (文件, 行号) —— 来自 audit_code_blocks.py --show-notes 的清单
CASES = [
    ("01-变量与类型.md", 57),
    ("03-列表字典与推导式.md", 194),
    ("06-异常与文件.md", 243),
    ("07-模块与虚拟环境.md", 11),
    ("09-异步与并发.md", 168),
    ("附-A-读懂一次模型API调用.md", 52),
    ("附-A-读懂一次模型API调用.md", 143),
    ("附-A-读懂一次模型API调用.md", 224),
]


def main() -> int:
    for fname, ln in CASES:
        p = DOCS / fname
        lines = p.read_text(encoding="utf-8").splitlines()
        # 找到从 ln 开始的那个代码块（ln 指向块内第一行附近）
        # 先往上找 ``` 围栏，再往下找结束围栏
        start = ln - 1
        while start > 0 and not lines[start].strip().startswith("```"):
            start -= 1
        block_start = start + 1           # 代码第一行的 0 基索引
        end = block_start
        while end < len(lines) and not lines[end].strip().startswith("```"):
            end += 1
        block_end = end                   # 结束围栏的 0 基索引

        print("=" * 84)
        print(f"  {fname}  第 {ln} 行")
        print("=" * 84)
        print("  —— 块前面的正文（前 10 行，判断是否故意演示）——")
        for j in range(max(0, block_start - 11), block_start):
            print(f"    {j+1:>4}| {lines[j][:88]}")
        print("  —— 代码块 ——")
        for j in range(block_start, block_end):
            print(f"    {j+1:>4}| {lines[j][:92]}")
        print("  —— 块后面的正文（后 3 行）——")
        for j in range(block_end + 1, min(len(lines), block_end + 4)):
            print(f"    {j+1:>4}| {lines[j][:88]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
