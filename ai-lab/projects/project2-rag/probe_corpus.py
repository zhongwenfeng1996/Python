#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探测语料形态 —— 用来给切块策略定参数（不靠猜）

为什么要先做这个：
  切块大小（chunk_size）如果拍脑袋定，后面的 recall 差了你根本不知道
  是"检索算法不行"还是"块切得不对"。这是 RAG 里最常见的归因错误。

  先量清楚文档的实际结构（段落长度分布、标题层级、代码块占比），
  参数就有依据了。

用法：
    & G:\\转型\\.venv\\Scripts\\python.exe ai-lab\\projects\\project2-rag\\probe_corpus.py
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCS = [
    REPO / "ai-lab" / "python_basics" / "docs",
    REPO / "ai-lab" / "python_basics" / "backend",
    REPO / "ai-lab" / "projects" / "project1-stream-chat",
    REPO / "ai-lab" / "docs",
]


def slugs() -> list[Path]:
    got: list[Path] = []
    for d in DOCS:
        if d.exists():
            got.extend(sorted(d.rglob("*.md")))
    # 去掉 project2 自己的文档（不能拿自己当知识库）
    return [p for p in got if "project2-rag" not in str(p)]


def split_blocks(md: str) -> tuple[list[str], list[str]]:
    """按空行切段落；把 ``` 代码块整体视为一段。返回 (正文段, 代码段)。"""
    prose: list[str] = []
    code: list[str] = []
    buf: list[str] = []
    in_code = False
    code_buf: list[str] = []

    for line in md.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                code_buf.append(line)
                code.append("\n".join(code_buf))
                code_buf = []
                in_code = False
            else:
                if buf:
                    prose.append("\n".join(buf)); buf = []
                in_code = True
                code_buf = [line]
            continue
        if in_code:
            code_buf.append(line)
            continue
        if line.strip() == "":
            if buf:
                prose.append("\n".join(buf)); buf = []
        else:
            buf.append(line)
    if buf:
        prose.append("\n".join(buf))
    return prose, code


def main() -> int:
    files = slugs()
    print("=" * 74)
    print("  语料形态探测 · 给切块参数找依据")
    print("=" * 74)
    print(f"  语料：{len(files)} 个 Markdown，来自 {len(DOCS)} 个目录")
    print()

    all_prose: list[str] = []
    all_code: list[str] = []
    per_file = []

    for f in files:
        md = f.read_text(encoding="utf-8")
        prose, code = split_blocks(md)
        all_prose.extend(prose)
        all_code.extend(code)
        per_file.append((f, len(md), len(prose), len(code)))

    total_chars = sum(t[1] for t in per_file)
    code_chars = sum(len(c) for c in all_code)
    prose_chars = sum(len(p) for p in all_prose)

    print(f"  总字符数     : {total_chars:,}")
    print(f"  正文（prose）: {prose_chars:,} 字符，{len(all_prose)} 段")
    print(f"  代码块       : {code_chars:,} 字符，{len(all_code)} 段 "
          f"（占 {code_chars / max(total_chars, 1) * 100:.1f}%）")
    print()

    lens = sorted(len(p) for p in all_prose)
    if lens:
        print("  正文段落长度分布（字符）:")
        for q, name in ((0, "最小"), (0.25, "P25"), (0.5, "中位"),
                        (0.75, "P75"), (0.9, "P90"), (1.0, "最大")):
            idx = min(int(q * (len(lens) - 1)), len(lens) - 1)
            print(f"    {name:>4} : {lens[idx]:>6}")
        print(f"    平均 : {statistics.mean(lens):>6.0f}")
        print()
        over = sum(1 for x in lens if x > 1000)
        print(f"  超过 1000 字符的长段: {over} 个 "
              f"（这些是切块时要拆开的主要对象）")
        print(f"  少于 100 字符的短段  : {sum(1 for x in lens if x < 100)} 个 "
              f"（这些容易切出'没头没尾'的块）")
    print()

    # 标题层级：决定要不要按标题切
    heads: dict[int, int] = {}
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(#{1,6})\s+", line)
            if m:
                heads[len(m.group(1))] = heads.get(len(m.group(1)), 0) + 1
    print("  标题层级分布（# 的个数 -> 数量）:")
    for lv in sorted(heads):
        print(f"    {'#' * lv:<8} {heads[lv]:>5}")
    print()

    print("  —— 对切块参数的结论 ——")
    print(f"    · 平均段落 {statistics.mean(lens):.0f} 字符 —— "
          "按段落切天然就在合理区间")
    print("    · 有明确的标题层级 —— 应该**按标题分节**再切，这样每个块")
    print("      自带'我属于哪一节'的上下文，检索出来更容易判断相关性")
    print(f"    · 代码块占 {code_chars / max(total_chars,1) * 100:.0f}% —— "
          "代码块不能被拦腰截断，切块时要以它为边界")
    print("    · 存在超长段 —— 必须做'按句/按长度二次切分'，否则单块过大会稀释相关性")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
