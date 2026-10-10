#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核查 must_contain 是否真的能证明"答案在语料里"（零 API 调用）

## 背景：发现了一个设计缺陷

`check_questions.py` 用 `must_contain in gold_doc` 来证明"答案有出处"。
但这个校验**太弱** —— 如果 `must_contain` 是个**通用词**，
它可能在文档里出现，却和问题的答案毫无关系。

实测抓到的例子（q017）：
    问题      : 为什么日志要自己写一个立刻 flush 的 handler？
    期望答案  : Python 日志写文件时默认块缓冲，要攒够一个缓冲区才落盘
    must_contain: 'flush'

    'flush' 在语料里出现在 **MCP 的 stdio 分帧**上下文
    （"每次写完必须 flush，否则客户端会一直等"），
    与"日志 handler 的块缓冲"是**两件不同的事**。

    而"Python 日志默认块缓冲"这个解释**在语料里根本不存在** ——
    所以模型弃答是**正确的**，错的是评估集。

## 这个脚本做什么

对每条题，检查 `must_contain` 是否**真的与答案相关**：
  1. 它出现在 gold 文档的哪些位置？上下文是什么？
  2. 用**答案文本**里的关键词反过来查 —— 答案的内容在语料里吗？
  3. 标出"must_contain 存在但答案内容不存在"的题

判据（可机械执行）：
  · 把 answer 切成短词（2-4 字的中文片段 / 英文词），
    看有多少比例出现在 gold 文档里。
  · 如果 must_contain 在，但 answer 的覆盖率很低
    → 这条题的 must_contain 不能证明答案有出处

用法：
    python eval/probe_mustcontain_validity.py
    python eval/probe_mustcontain_validity.py q017
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
from run_eval import load_questions     # noqa: E402


def keywords(text: str) -> list[str]:
    """
    从一段中文里切出"有信息量"的片段。

    为什么不用分词库：本仓库没有中文分词（FTS5 的中文分词也不可用，
    见 ADR-003 的记录）。这里用的是**字符 n-gram + 英文词**的近似法：
      · 英文/数字词：按 \\w+ 切，长度 >= 4
      · 中文：切 3-gram（3 个字的重叠片段），去掉纯标点的

    3-gram 的好处：对"块缓冲"这类术语敏感（"块缓冲"会命中），
    而单个汉字太泛（"的"到处都是）。
    """
    out: list[str] = []
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_\.\-]{3,}", text):
        out.append(w.lower())
    han = re.sub(r"[^\u4e00-\u9fff]", " ", text)
    for seg in han.split():
        for i in range(len(seg) - 2):
            out.append(seg[i:i + 3])
    return out


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    questions = [q for q in load_questions(HERE / "questions.jsonl") if q.answerable]
    if only:
        questions = [q for q in questions if q.id == only]
    docs = {d.rel_path: d for d in load_corpus()}

    print("=" * 98)
    print("  must_contain 的有效性核查 —— 它真能证明「答案在语料里」吗？")
    print("=" * 98)
    print("  判据：把 answer 切成关键词，看有多少比例真的出现在 gold 文档里")
    print()

    suspect = []
    for q in questions:
        doc = docs.get(q.gold_doc)
        if doc is None:
            continue
        text = doc.text
        kws = keywords(q.answer)
        if not kws:
            continue
        hit = [k for k in kws if k in text]
        ratio = len(set(hit)) / len(set(kws))

        mc_ok = q.must_contain in text
        if ratio < 0.55:
            suspect.append((q, ratio, mc_ok, len(set(kws)), len(set(hit))))

    suspect.sort(key=lambda x: x[1])
    if suspect:
        print(f"  ⚠️ 答案内容覆盖率 < 55% 的题（{len(suspect)} 条）—— "
              f"must_contain 通过但答案不在文档里:")
        print()
        print(f"  {'id':<7} {'覆盖率':>7} {'must_contain':>13} {'关键词':>7} {'命中':>6}  问题")
        print("  " + "-" * 88)
        for q, ratio, mc_ok, nk, nh in suspect:
            print(f"  {q.id:<7} {ratio:>7.2f} {('✅在' if mc_ok else '❌不在'):>13} "
                  f"{nk:>7} {nh:>6}  {q.question[:36]}")
        print()
        print("  逐条看详情:")
        for q, ratio, mc_ok, nk, nh in suspect[:6]:
            doc = docs[q.gold_doc]
            kws = sorted(set(keywords(q.answer)))
            miss = [k for k in kws if k not in doc.text]
            print(f"    ── {q.id}  覆盖率 {ratio:.2f}")
            print(f"       问题    : {q.question}")
            print(f"       期望答案: {q.answer[:88]}")
            print(f"       must_contain={q.must_contain!r} 在文档里: {mc_ok}")
            print(f"       答案里**不在**文档中的片段（前 12 个）: {miss[:12]}")
            print()
    else:
        print("  ✅ 所有题的答案内容覆盖率都 >= 55%")

    print("=" * 98)
    print("  这说明什么")
    print("=" * 98)
    print("    `must_contain` 是**必要不充分**条件：")
    print("      · 它不在 → 答案肯定没出处（校验会拦下）")
    print("      · 它在   → **不代表答案有出处**（可能只是个通用词碰巧出现）")
    print()
    print("    这是个真实的设计缺陷。修法有两条：")
    print("      1. 换 must_contain 为**更具辨识度的短语**（不是单词）")
    print("      2. 或者给每条题加一个 answer_coverage 检查（就是这个脚本做的）")
    print("    我选 1 + 把这个检查并进 check_questions.py 作为第二道闸。")
    print("=" * 98)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
