#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精确核查：语料里有没有"项目一的测试用例文件名"

## 为什么要单独查这个

`probe_hallucination.py` 搜 `test_` 时命中了 `tests/test_api.py`，
但那一条来自 **`python_basics/backend/README.md`（项目二后端篇）**，
不是项目一。而题目问的是**项目一**的测试文件名。

所以要按**来源文件**过滤后再判断。判据：

  · 如果 `projects/project1-stream-chat/` 下的文档里明确写了测试文件名
    → 题可答，我错了
  · 如果没有 → 模型引用别处的文件名来回答项目一的问题，那是**跨文档混淆**
    （也算一种幻觉，但和"凭空编造"不同）

用法：
    python eval/probe_p1_testfile.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from rag.corpus import load_corpus      # noqa: E402


def main() -> int:
    docs = {d.rel_path: d for d in load_corpus()}

    print("=" * 94)
    print("  核查：语料里有没有「项目一的测试用例文件名」")
    print("=" * 94)
    print()

    pats = ["test_", "tests/", "conftest", "测试文件", "测试用例", "测试代码"]
    for pat in pats:
        print(f"  ── 搜 {pat!r}")
        for path, d in sorted(docs.items()):
            for m in re.finditer(re.escape(pat), d.text):
                s = max(0, m.start() - 60)
                ctx = d.text[s:m.start() + 80].replace("\n", " ")
                # 按来源分类
                if "project1" in path:
                    tag = "★ **项目一自己的文档**"
                elif "python_basics/backend" in path:
                    tag = "（项目二后端篇）"
                else:
                    tag = ""
                print(f"    {path}  {tag}")
                print(f"      ...{ctx}...")
        print()

    print("=" * 94)
    print("  判定")
    print("=" * 94)
    p1_hits = []
    for path, d in docs.items():
        if "project1" not in path:
            continue
        for m in re.finditer(r"`[^`]*test[^`]*\.py`|tests/[\w/]+\.py", d.text):
            p1_hits.append((path, m.group(0)))
    if p1_hits:
        print("  项目一自己的文档里出现的测试文件名:")
        for path, name in sorted(set(p1_hits)):
            print(f"    {name}   （来自 {path}）")
        print()
        print("  → 题**可答**，我把它归为不可答是错的。")
        print("    应该改成可答题，或者把问题问得更具体"
              "（比如'项目一有几个测试文件'——如果数量没写就是不可答）。")
    else:
        print("  项目一自己的文档里**没有**任何测试文件名。")
        print()
        print("  → 题确实不可答。但模型用**别处的**文件名（项目二的") 
        print("    `tests/test_api.py`）来回答项目一的问题 —— 这是**跨文档混淆**，")
        print("    属于幻觉的一种，只是不像'凭空编造'那么明显。")
        print("    修法：提示词里明确要求'引用必须支持论断'，")
        print("    并可考虑在生成时把来源路径也一起给模型看。")
    print("=" * 94)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
