#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给附录里"裸 ``` 围栏"补上 python 语言标记（能判断的才补）

## 背景

附-C、附-D 的代码块全部写成了裸 ```（没有语言标记），导致：
  · Markdown 不语法高亮
  · 检查工具（audit / run_doc_examples）把它们当成"非代码"跳过
  · 里面的错误（如附-C 的 URL 被 Markdown 链接污染）从未被发现

## 判据（为什么不做"一刀切"）

附-D 里有伪代码/速记片段，不能全标 python：

  · `isinstance(对象, 类型)` —— 中文占位符，不是合法 Python 表达式
  · `model.model_dump()` 等 —— 方法调用速记，不是完整语句

所以只给"**看起来像真 Python**"的块补标记：
块内第一行以 import/from/def/class/print/with/for/try/@ 开头，
或块内含 `import ` 语句。

## 为什么这个判据可能漏

有些真 Python 块第一行是变量赋值（如 `a = 10`），不会命中上面的判据。
漏了没关系 —— 它保持裸 ```，只是不语法高亮，不会更糟。
**宁可漏标，不要给伪代码错标 python**（错标会被语法检查误报）。

用法：
    python tools/fix_code_fences.py --dry-run
    python tools/fix_code_fences.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

#: 只处理这几个附录（正文 01-10 已经都带 python 标记了）
TARGETS = ["附-C-with的用法.md", "附-D-Pydantic的常用模块.md"]

#: 块第一行以这些开头 → 判定为 Python 代码
PY_STARTS = ("import ", "from ", "def ", "class ", "print(", "with ",
             "for ", "try:", "@", "async ", "if ")


def looks_like_python(body: str) -> bool:
    # 跳过空行和注释行，找第一个"真正的代码行"
    code_lines = [l.strip() for l in body.splitlines()
                  if l.strip() and not l.strip().startswith("#")]
    if not code_lines:
        return False
    first = code_lines[0]
    if any(first.startswith(s) for s in PY_STARTS):
        return True
    # 块内含 import 语句也算
    return any(l.startswith(("import ", "from ")) for l in code_lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total = 0
    for name in TARGETS:
        p = DOCS / name
        if not p.exists():
            print(f"  ⚠️ 找不到 {name}")
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        changed = 0
        i = 0
        while i < len(lines):
            if lines[i].strip() == "```":
                # 找到这个围栏的结束
                j = i + 1
                body = []
                while j < len(lines) and lines[j].strip() != "```":
                    body.append(lines[j])
                    j += 1
                if j < len(lines) and looks_like_python("\n".join(body)):
                    if not args.dry_run:
                        lines[i] = "```python"
                    first = body[0].strip()[:40] if body else ""
                    print(f"  {'[dry]' if args.dry_run else '[改]'} "
                          f"{name}:{i+1}  → ```python   ({first})")
                    changed += 1
                i = j + 1
            else:
                i += 1
        if not args.dry_run and changed:
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        total += changed
    print()
    print(f"  共补 {total} 处 python 标记")
    if args.dry_run:
        print("  （--dry-run，未写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
