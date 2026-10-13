#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
列出所有"题干区泄漏答案"的练习 —— 供人工决定怎么改

## 为什么先列不直接改

"泄漏"有三种，修法不同，**不能一律删**：

  ① **完整解法写在题干里** → 删掉解法，指向折叠答案
     （比如 04/1 的 `chunk_text` 实现）

  ② **答案写在代码块的三引号字符串里** → 把答案从代码块里挪出去
     （比如 01/1 把输出写在 `\"\"\"` 里；这还会让代码块内容混杂）

  ③ **题干故意给一段完整的"可运行示例"** → 保留，但要说明
     （比如 06/2 的退避重试、09 的协程示例 —— 题目本身就是
     "看懂这个模式"，给了答案是有意的）

所以要人工过一遍。这个脚本只负责**把候选列全**，并给出每处的
行数与首行，方便判断属于哪一类。

用法：
    python tools/list_answer_leaks.py
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

SOLUTION_HINTS = ("def ", "for ", "while ", "if ", "return ", "print(")


def sections(md: str):
    out, title, buf = [], "(开头)", []
    for line in md.splitlines():
        if line.startswith("## "):
            out.append((title, "\n".join(buf)))
            title, buf = line[3:].strip(), []
        else:
            buf.append(line)
    out.append((title, "\n".join(buf)))
    return out


def main() -> int:
    print("=" * 96)
    print("  题干区疑似泄漏答案的练习块（供人工判断）")
    print("=" * 96)
    print()
    n = 0
    for f in sorted(DOCS.glob("*.md")):
        md = f.read_text(encoding="utf-8")
        for title, body in sections(md):
            if not title.startswith("练习"):
                continue
            # 找出 body 里 <details> 之前的部分 = 题干区
            stem = body.split("<details>")[0]
            # 逐个代码块
            in_block, buf, start = False, [], 0
            for i, line in enumerate(stem.splitlines(), 1):
                if line.strip().startswith("```"):
                    if in_block:
                        src = "\n".join(buf)
                        lines = [x for x in src.splitlines() if x.strip()]
                        has_three_quote = '"""' in src
                        suspect = (
                            (len(lines) >= 3
                             and any(h in src for h in SOLUTION_HINTS))
                            or has_three_quote
                        )
                        if suspect:
                            n += 1
                            tag = []
                            if has_three_quote:
                                tag.append("含三引号（答案写在块里？）")
                            if any(h in src for h in SOLUTION_HINTS):
                                tag.append("含解法关键词")
                            print(f"  ── {f.name}  [{title}]  第 {start} 行起  "
                                  f"{len(lines)} 行")
                            print(f"     判定线索: {' / '.join(tag)}")
                            print(f"     首行: {lines[0][:76] if lines else ''}")
                            # 提一句"题目怎么说的"
                            print()
                        in_block, buf = False, []
                    else:
                        in_block, start = True, i
                    continue
                if in_block:
                    buf.append(line)
    print("=" * 96)
    print(f"  共 {n} 处候选")
    print()
    print("  三类修法：")
    print("    ① 完整解法写在题干里   → 删掉，指向折叠答案")
    print("    ② 答案写在 `\"\"\"` 里    → 把答案挪出代码块（块内应只有可运行代码）")
    print("    ③ 故意给的可运行示例   → 保留，但在题干里明说『这题直接给答案』")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
