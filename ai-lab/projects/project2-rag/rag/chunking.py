#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
切块策略（见 ADR-001）

## 参数不是拍脑袋定的

`probe_corpus.py` 实测了语料形态，决定了这个实现的样子：

  - 段落中位长度 **28 字符**，915 段里 **751 段不足 100 字符**（82%）
      → **绝不能按段落切**，否则切出 700+ 个没头没尾的碎片，
        检索出来根本判断不了相关性
  - 有明确的标题层级（206 个 `#`、128 个 `##`、70 个 `###`）
      → **按标题分节**，每个块自带「我属于哪一节」的上下文
  - 代码块占 **49.3%**
      → 代码块**不可拦腰截断**，要以它为硬边界
  - 存在超过 1000 字符的长段
      → 节内还要按长度二次切分，并留 overlap 防语义断裂

## 三种策略都会被实现，因为要能用同一套评估集对比（ADR-005）

  - fixed     ：固定字符数硬切（**基线，也是最差的那个**）
  - paragraph ：每段一块（预计很差 —— 用来验证 ADR-001 的判断）
  - heading   ：按标题分节 + 节内累积（默认）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal

# 默认参数（来自 probe_corpus.py 的实测，见 ADR-001）
DEFAULT_CHUNK_SIZE = 600
DEFAULT_OVERLAP = 80

Strategy = Literal["fixed", "paragraph", "heading"]


@dataclass
class Chunk:
    """一个可检索的块。元数据是引用功能的前提（要能说出'来自哪一节'）。"""

    text: str
    source_path: str
    heading_path: str = ""            # 如 "05 · 条件、循环与作用域 > 3. 循环"
    chunk_index: int = 0
    strategy: str = "heading"

    @property
    def char_len(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "source_path": self.source_path,
            "heading_path": self.heading_path,
            "chunk_index": self.chunk_index,
            "strategy": self.strategy,
        }


# ======================================================================
# Markdown 结构解析
# ======================================================================


@dataclass
class Section:
    """一个标题下的内容（含它所属的标题路径）。"""

    heading_path: str
    level: int
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def split_sections(md: str) -> list[Section]:
    """
    按标题把 Markdown 切成节，并维护标题路径。

    标题路径是**刻意保留的上下文**：同一个块在没有标题时可能看不出在讲什么，
    带上 "05 · 条件、循环与作用域 > 3. 循环" 之后，检索和生成都更容易判断相关性。
    """
    sections: list[Section] = []
    # 标题栈：[(level, title)]，用来算当前路径
    stack: list[tuple[int, str]] = []
    current = Section(heading_path="(文档开头)", level=0)
    in_code = False

    for line in md.splitlines():
        # 代码块里的 # 不是标题！这个坑很常见（Python 注释、shell 注释都是 #）
        if line.strip().startswith("```"):
            in_code = not in_code
            current.lines.append(line)
            continue
        if in_code:
            current.lines.append(line)
            continue

        m = _HEADING_RE.match(line)
        if m:
            if current.text:
                sections.append(current)
            level = len(m.group(1))
            title = m.group(2)
            # 弹出层级 >= 当前的
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            current = Section(
                heading_path=" > ".join(t for _, t in stack),
                level=level,
            )
        else:
            current.lines.append(line)

    if current.text:
        sections.append(current)
    return sections


def split_units(text: str) -> list[tuple[str, bool]]:
    """
    把一节的内容切成"不可再分的最小单元"，返回 [(内容, 是否代码块)]。

    单元边界 = 空行。代码块整体是一个单元（**不可切断**）。
    这一步是为了后续按单元累积到目标大小，而不是粗暴按字符数截。
    """
    units: list[tuple[str, bool]] = []
    buf: list[str] = []
    code_buf: list[str] = []
    in_code = False

    def flush_prose() -> None:
        if buf:
            t = "\n".join(buf).strip()
            if t:
                units.append((t, False))
            buf.clear()

    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                code_buf.append(line)
                units.append(("\n".join(code_buf), True))   # 代码块整体成单元
                code_buf = []
                in_code = False
            else:
                flush_prose()
                in_code = True
                code_buf = [line]
            continue
        if in_code:
            code_buf.append(line)
            continue
        if line.strip() == "":
            flush_prose()
        else:
            buf.append(line)

    if in_code and code_buf:                     # 未闭合的代码块（文档写错了也容错）
        units.append(("\n".join(code_buf), True))
    flush_prose()
    return units


# ======================================================================
# 策略实现
# ======================================================================


def chunk_heading(
    md: str,
    source_path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """
    默认策略：按标题分节 → 节内按单元累积到 chunk_size → 超长单元再切。

    overlap 只在"因超长被强制切开"的地方添加，用来缓解切断语义。
    """
    out: list[Chunk] = []
    idx = 0

    for sec in split_sections(md):
        if not sec.text:
            continue
        units = split_units(sec.text)
        if not units:
            continue

        buf: list[str] = []
        buf_len = 0

        def flush() -> None:
            nonlocal idx, buf, buf_len
            body = "\n\n".join(buf).strip()
            if body:
                # 标题路径作为块的第一行 —— 让块自带上下文
                text = f"{sec.heading_path}\n{body}" if sec.heading_path else body
                out.append(Chunk(text=text, source_path=source_path,
                                 heading_path=sec.heading_path,
                                 chunk_index=idx, strategy="heading"))
                idx += 1
            buf, buf_len = [], 0

        for unit, is_code in units:
            # 超长单元：先 flush，再按 chunk_size 硬切（代码块尽量不切，除非实在太大）
            if len(unit) > chunk_size:
                flush()
                if is_code and len(unit) <= chunk_size * 2:
                    # 代码块宁可超一点也不切 —— 切了就没法读
                    text = f"{sec.heading_path}\n{unit}" if sec.heading_path else unit
                    out.append(Chunk(text=text, source_path=source_path,
                                     heading_path=sec.heading_path,
                                     chunk_index=idx, strategy="heading"))
                    idx += 1
                    continue
                step = max(chunk_size - overlap, 1)
                for i in range(0, len(unit), step):
                    piece = unit[i:i + chunk_size]
                    if not piece.strip():
                        continue
                    text = (f"{sec.heading_path}\n{piece}"
                            if sec.heading_path else piece)
                    out.append(Chunk(text=text, source_path=source_path,
                                     heading_path=sec.heading_path,
                                     chunk_index=idx, strategy="heading"))
                    idx += 1
                    if i + chunk_size >= len(unit):
                        break
                continue

            # 加上这个单元会超？先封块（除非当前是空的）
            if buf and buf_len + len(unit) > chunk_size:
                flush()
            buf.append(unit)
            buf_len += len(unit) + 2

        flush()

    return out


def chunk_paragraph(md: str, source_path: str, **_kw) -> list[Chunk]:
    """
    基线 B：每段一块。

    **这个策略是故意保留的对照组** —— ADR-001 判断它很差，
    但"判断"必须有数据支撑。评估集跑完会给出它的 recall，用来证明：
    碎片化的块确实检索不到（大概率会明显低于 heading 策略）。
    """
    out: list[Chunk] = []
    idx = 0
    for sec in split_sections(md):
        for unit, _is_code in split_units(sec.text):
            body = unit.strip()
            if not body:
                continue
            text = f"{sec.heading_path}\n{body}" if sec.heading_path else body
            out.append(Chunk(text=text, source_path=source_path,
                             heading_path=sec.heading_path,
                             chunk_index=idx, strategy="paragraph"))
            idx += 1
    return out


def chunk_fixed(
    md: str, source_path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    **_kw,
) -> list[Chunk]:
    """
    基线 A：完全不管结构，按固定字符数硬切。

    预期最差 —— 会切断代码块和句子。保留它做对照，
    正好能回答面试常问的"切块策略对检索影响多大"。
    """
    out: list[Chunk] = []
    step = max(chunk_size - overlap, 1)
    for idx, i in enumerate(range(0, len(md), step)):
        piece = md[i:i + chunk_size]
        if not piece.strip():
            continue
        out.append(Chunk(text=piece, source_path=source_path,
                         heading_path="", chunk_index=idx, strategy="fixed"))
        if i + chunk_size >= len(md):
            break
    return out


STRATEGIES = {
    "heading": chunk_heading,
    "paragraph": chunk_paragraph,
    "fixed": chunk_fixed,
}


def chunk_document(
    md: str,
    source_path: str,
    strategy: Strategy = "heading",
    **kwargs,
) -> list[Chunk]:
    fn = STRATEGIES.get(strategy)
    if fn is None:
        raise ValueError(f"未知策略 {strategy!r}，可选：{list(STRATEGIES)}")
    return fn(md, source_path, **kwargs)


def chunk_files(
    files: Iterable,
    strategy: Strategy = "heading",
    **kwargs,
) -> list[Chunk]:
    """对多个文件切块。用 pathlib.Path 的列表比较方便。"""
    from pathlib import Path
    out: list[Chunk] = []
    for p in files:
        path = Path(p)
        md = path.read_text(encoding="utf-8")
        out.extend(chunk_document(md, str(path), strategy=strategy, **kwargs))
    return out
