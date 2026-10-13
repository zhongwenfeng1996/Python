#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给"接着上文"或"故意演示"的块补上显式标记

## 为什么必须标注，而不是让工具学会猜

`run_doc_examples.py` 真跑代码块时，会报出 14 处失败。逐一核实后发现
**没有一个是新错误**，全是两类：

  ① **跨块延续**：块假设上文定义过的 `response` / `messages` / `my_dict`
     还在。`NameError` 是设计使然。
  ② **故意演示**：`05` 那个 `UnboundLocalError` 就是练习 3 的题目；
     `02` 的 `list.join` 报错是讲"用错方法"。

问题是：工具**没法可靠地区分**这两类和真错误 —— 我试过词表判断，
第一次漏了 `01/3`（题干写的是"可能有问题"），第二次漏了 `08` 练习 3
（题干写的是"会报错"）。**每次都是"改判据"，每次都有漏网。**

所以改成显式标注：作者在块里写一行 `# 续上文` 或 `# 前置：`，
工具就跳过。没标的按问题报。

代价是作者要多写一行；收益是**判据不会再有灰色地带** ——
这与 scope_check（ADR-012）的取舍一致：宁可显式，不要靠猜。

## 这个脚本怎么改

按"真跑失败"的清单，在**块的第一行**插入标记（若已有则跳过）。
标记内容带上"需要什么"，让读者也能受益：

    # 续上文：这里假设 `response` 是上面 API 调用拿到的字典
    # 演示：这段故意会报 UnboundLocalError（见本节练习）

用法：
    python tools/mark_continuations.py --dry-run
    python tools/mark_continuations.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

#: 文件:行号 -> 插入的标记行（不含缩进）。
#: 这些行号来自 `run_doc_examples.py` 的失败清单，逐条人工核实过。
MARKS: dict[str, str] = {
    "02-字符串与格式化.md:120":
        "# 续上文：这里假设 `parts` 是上面切分得到的片段列表",
    "02-字符串与格式化.md:224":
        "# 演示：这段故意用错方法（list 没有 join）—— 本节讲的就是这个错",
    "02-字符串与格式化.md:259":
        "# 续上文：这里假设 `words` 是上面分词得到的词列表",
    "03-列表字典与推导式.md:171":
        "# 续上文：`response` 是调用模型 API 拿到的响应字典（来源见第 01 章）",
    "03-列表字典与推导式.md:202":
        "# 续上文：同上，`response` 来自上面的 API 调用",
    "03-列表字典与推导式.md:396":
        "# 续上文：这里假设 `messages` 是上面的对话消息列表",
    "03-列表字典与推导式.md:406":
        "# 续上文：`response` 来自上面的 API 调用",
    "04-函数.md:323":
        "# 续上文：这段接着上面练习 3 的定义（`record_usage` 已存在）",
    "05-条件循环与作用域.md:90":
        "# 续上文：这里假设 `my_dict` 是上面那个字典",
    "05-条件循环与作用域.md:360":
        "# 演示：这段故意会报 UnboundLocalError —— 本题就是找这个错",
    "06-异常与文件.md:181":
        "# 演示：这里故意读一个不存在的文件，用来展示 FileNotFoundError",
    "07-模块与虚拟环境.md:39":
        "# 续上文：这里假设同级目录下有个 `utils.py`（本节在讲 import 的路径问题）",
    "07-模块与虚拟环境.md:84":
        "# 续上文：同上，仍然假设 `utils.py` 存在",
    "附-A-读懂一次模型API调用.md:112":
        "# 续上文：`response` 是上面那次 API 调用拿到的字典",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 按文件分组，从后往前改（避免行号偏移）
    by_file: dict[str, list[tuple[int, str]]] = {}
    for key, mark in MARKS.items():
        fname, ln = key.rsplit(":", 1)
        by_file.setdefault(fname, []).append((int(ln), mark))

    total = 0
    for fname, items in by_file.items():
        p = DOCS / fname
        if not p.exists():
            print(f"  ⚠️ 找不到 {fname}")
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for ln, mark in sorted(items, reverse=True):
            idx = ln - 1                    # 代码块第一行
            if idx >= len(lines):
                print(f"  ⚠️ {fname}:{ln} 超出行数")
                continue
            # 幂等：块开头几行已有标记就跳过
            head = "\n".join(lines[idx: idx + 3])
            if "续上文" in head or "# 前置：" in head or "# 演示：" in head:
                print(f"  [skip] {fname}:{ln} 已有标记")
                continue
            indent = len(lines[idx]) - len(lines[idx].lstrip())
            pad = " " * indent
            # 若插到 docstring 里会破坏字符串 —— 先确认这一行不在三引号内
            if lines[idx].lstrip().startswith('"""'):
                print(f"  ⚠️ {fname}:{ln} 是 docstring 开头，跳过（需人工处理）")
                continue
            lines.insert(idx, f"{pad}{mark}")
            total += 1
            print(f"  {'[dry]' if args.dry_run else '[改]'} {fname}:{ln}  + {mark[:46]}")
        if not args.dry_run and total:
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    print(f"  共插入 {total} 处标记")
    if args.dry_run:
        print("  （--dry-run，未写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
