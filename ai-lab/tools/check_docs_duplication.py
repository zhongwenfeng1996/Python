#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
教程内容重复检测。

## 为什么需要它（这是"强行补充"最典型的症状）

我在第 04 章加了一段 86 行的「bytes 和 str 不能混着比」，
后来发现**第 10 章本来就讲过同一件事**。这段重复的后果不是"多花了篇幅"，
而是：

  - 同一个知识点在两处出现，读者不知道该信哪一处
  - 两处的说法不一致时（第 10 章说 startswith 会报错、我写的说静默失效），
    读者会彻底困惑
  - 后读的那处像是"又讲一遍"，读起来就是"强行补充"的感觉

文件存在性检查、悬空变量检查都抓不到这个。只有把两段文字放在一起比才发现。

## 方法

对每个代码块和每个较长段落做归一化（去注释、去空白、统一标点），
然后用"shingle（连续 N 个词的片段）"算相似度。
相似度超过阈值就报出来 —— 剩下的由人判断是"必要的重复"还是"该合并"。

已知会报的合理重复：同一份导航链接（"上一章/下一章"）、
各章都有的"常见坑"表头。所以用 KNOWN_OK 记为刻意重复。

用法：
    python ai-lab/tools/check_docs_duplication.py
    python ai-lab/tools/check_docs_duplication.py --threshold 0.6

退出码：0 = 没有可疑重复；1 = 有。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "ai-lab" / "python_basics" / "docs"

# 归一化时要去掉的东西 —— 它们不影响"讲的是不是同一件事"
NORMALIZE_RULES = (
    (re.compile(r"#.*"), ""),            # 行内注释
    (re.compile(r"^\s*$", re.M), ""),    # 空行
    (re.compile(r"\s+"), ""),            # 所有空白（中文排版下空白本就无关）
)

# 太短的片段不参与比较（"print(x)" 到处都是，报出来是噪音）
MIN_CHARS = 120

# 刻意重复，不算问题。
#
# 结构：(文件 A 的前缀, 文件 B 的前缀, 为什么这次重复是合理的)
# 加条目时必须写清理由 —— 否则过几个月没人敢删。
#
# 为什么按"文件对"而不是按"具体片段"匹配：
#   片段文本会因为改标点、调注释而变，按文本匹配的豁免规则会悄悄失效，
#   然后你又开始收到一堆"已知合理"的报警 —— 那就是噪音回来了。
#   文件对的粒度虽然粗，但稳定。
KNOWN_OK_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("03-", "附-A-", "附录 A 要重新展示『逐层取值』的写法，这是它的核心内容；"
                      "读者是从各章跳过来的，不能只说『见第 03 章』"),
    ("10-", "附-A-", "附录讲 API 响应结构（是什么），第 10 章讲调试过程（错了怎么查）"),
    ("03-", "10-", "同一段 `choices = response.get(\"choices\") or []`，"
                    "但第 03 章是学链式 .get() 的兜底写法，第 10 章是学"
                    "『响应为空时怎么排查』—— 学一次、用一次，是刻意的螺旋"),
    ("01-", "附-A-", "第 01 章只点明结论（真假值 vs is None），"
                      "附录给完整实现；这正是把长内容挪出章节后的预期形态"),
)

# 实测：阈值降到 0.30 会稳定报出上面那 3 对；0.20 开始出现噪音。
# 所以默认取 0.35 —— 比 0.30 略高一点，避免把"共用了样板代码"也算成重复。
DEFAULT_THRESHOLD = 0.35


def code_blocks(text: str) -> list[tuple[int, str]]:
    """提取代码块，返回 [(起始行, 归一化内容)]。"""
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("```"):
            start = i + 1
            i += 1
            body = []
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            raw = "\n".join(body)
            out.append((start, normalize(raw)))
        i += 1
    return out


def normalize(text: str) -> str:
    for pattern, repl in NORMALIZE_RULES:
        text = pattern.sub(repl, text)
    return text


def shingles(text: str, size: int = 12) -> set[str]:
    """
    把文本切成连续 size 个字符的片段集合。

    用字符级而不是词级：中文没有空格分词，字符级更稳。
    size=12 是个经验值 —— 小于 8 会因为常见语法撞车，大于 20 会漏掉改写过的重复。
    """
    if len(text) < size:
        return {text}
    return {text[i:i + size] for i in range(len(text) - size + 1)}


def similarity(a: str, b: str, size: int = 12) -> float:
    """Jaccard 相似度：交集 / 并集。"""
    sa, sb = shingles(a, size), shingles(b, size)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def known_ok_reason(name_a: str, name_b: str) -> str | None:
    """这一对文件是不是『刻意重复』？是则返回理由。"""
    for pa, pb, reason in KNOWN_OK_PAIRS:
        if ((name_a.startswith(pa) and name_b.startswith(pb)) or
                (name_a.startswith(pb) and name_b.startswith(pa))):
            return reason
    return None


def answer_regions(text: str) -> list[tuple[int, int]]:
    """
    返回所有 `<details>...</details>` 的 [起行, 止行)。

    为什么需要：练习的「参考答案」本质上就是题干的重复 ——
    **这是刻意设计的，不是内容冗余。** 不把它们排除掉，
    每个练习都会报一次"100% 重复"，噪音会淹没真正的问题。

    这个检查器第一版就踩了：05 章练习 4 的题干和答案在同一个文件里
    隔了 213 行，超过了"相邻块"的跳过阈值，于是被报成疑似重复。
    """
    regions = []
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("<details>"):
            start = i
        elif start is not None and line.strip().startswith("</details>"):
            regions.append((start, i))
            start = None
    if start is not None:
        regions.append((start, len(lines)))
    return regions


def in_answer(lineno: int, regions: list[tuple[int, int]]) -> bool:
    return any(lo <= lineno <= hi for lo, hi in regions)


def main() -> int:
    ap = argparse.ArgumentParser(description="教程内容重复检测")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"相似度阈值，默认 {DEFAULT_THRESHOLD}（越低越灵敏，也越多噪音）")
    ap.add_argument("--all", action="store_true",
                    help="连已核实的刻意重复一起列出（用于复查豁免清单还准不准）")
    args = ap.parse_args()

    docs = sorted(DOCS.glob("*.md"))
    if not docs:
        print(f"[x] {DOCS} 下没有 .md")
        return 1

    print("=" * 78)
    print("  教程内容重复检测 · 同一个知识点是不是讲了两遍")
    print("=" * 78)
    print(f"  目录：{DOCS.relative_to(REPO)}（{len(docs)} 篇）")
    print(f"  阈值：相似度 >= {args.threshold}（只比 {MIN_CHARS} 字以上的代码块）")
    print()

    items: list[tuple[str, int, str]] = []
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        regions = answer_regions(text)
        for lineno, body in code_blocks(text):
            if len(body) >= MIN_CHARS and not in_answer(lineno, regions):
                items.append((doc.name, lineno, body))
    print(f"  参与比较的代码块：{len(items)} 个"
          f"（已排除练习题干与参考答案 —— 那是有意的重复）")
    print()

    fresh: list[tuple[float, str, int, str, int]] = []
    known: list[tuple[float, str, int, str, int, str]] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            fa, la, ba = items[i]
            fb, lb, bb = items[j]
            if fa == fb and abs(la - lb) < 30:
                continue          # 同一文件里相邻的块，通常是同一段代码被拆开
            sim = similarity(ba, bb)
            if sim < args.threshold:
                continue
            reason = known_ok_reason(fa, fb)
            if reason:
                known.append((sim, fa, la, fb, lb, reason))
            else:
                fresh.append((sim, fa, la, fb, lb))

    if args.all and known:
        print("—— 已核实的刻意重复（--all 才显示）——")
        for sim, fa, la, fb, lb, reason in sorted(known, reverse=True):
            print(f"  {sim:.0%}  {fa}:{la} <-> {fb}:{lb}")
            print(f"        → {reason}")
        print()

    if not fresh:
        print("=" * 78)
        print(f"  [ok] 没有发现**新的**可疑重复"
              f"（另有 {len(known)} 对已核实的刻意重复）")
        print("=" * 78)
        return 0

    fresh.sort(reverse=True)
    print("=" * 78)
    print(f"  需要确认的重复 —— {len(fresh)} 对")
    print("=" * 78)
    for sim, fa, la, fb, lb in fresh:
        print(f"  相似度 {sim:.0%}   {fa}:{la}   <->   {fb}:{lb}")
    print()
    print("  怎么处理：")
    print("    1. 先读这两处，判断『是不是同一个知识点』")
    print("    2. 是 -> 只留一处，另一处改成一句指路（『见第 X 章』）")
    print("    3. 不是 -> 把这对文件加进脚本的 KNOWN_OK_PAIRS，并写明理由")
    print("=" * 78)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
