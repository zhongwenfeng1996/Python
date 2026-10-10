#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核查"硬答"的那两条题：是真的幻觉，还是**我说错了（题其实可答）**？

## 为什么必须查

对抗集里那 2 条"硬答"让我很难判断：

    ❌ 跨项目   ：项目一的测试用例文件叫什么名字？
    ❌ 私有信息 ：作者下一步打算做什么功能？

两种可能，结论完全相反：
  A. **模型在编**（真幻觉）—— 严重问题，要改提示词
  B. **我说错了**（题其实可答）—— 我又违反了"编题前先查文档"这条规则

看模型给的引用就知道：如果它引用的段落里**真有那个信息**，
那就是 B（我错）；如果引用的段落里没有，那就是 A（模型编）。

## 这个脚本做什么

把模型引用到的那几段**原文打出来**，人工/机械核对
"答案是否真的在那里"。

用法：
    python eval/probe_hallucination.py
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

# 模型给出的引用（从 test_refusal 的输出里抄下来的）
CASES = [
    {
        "kind": "跨项目",
        "q": "项目一的测试用例文件叫什么名字？",
        "cited_sources": [],
        "claim": "（待核对：模型给了什么答案、引用了哪段）",
    },
    {
        "kind": "私有信息",
        "q": "作者下一步打算做什么功能？",
        "cited_sources": ["ai-lab/projects/project1-stream-chat/docs/ADR.md",
                          "ai-lab/week01/README.md"],
        "claim": "项目二引入 AI SDK，对照实现一遍手写的 SSE 流式方案；"
                 "W1 的动手改造列出三项待做功能（/retry、/save 等）",
    },
]


def main() -> int:
    docs = {d.rel_path: d for d in load_corpus()}

    print("=" * 96)
    print("  核查「硬答」是真幻觉还是我题写错了")
    print("=" * 96)
    print()

    for c in CASES:
        print("-" * 96)
        print(f"  [{c['kind']}] {c['q']}")
        print(f"    模型声称: {c['claim'][:100]}")
        print()

        # 搜索关键词
        if c["kind"] == "私有信息":
            kws = ["AI SDK", "retry", "save", "下一步", "待做", "动手改造"]
        else:
            kws = ["test_", "测试用例", "pytest", "test_stream", "文件名"]

        for kw in kws:
            hits = []
            for path, d in docs.items():
                for m in re.finditer(re.escape(kw), d.text):
                    s = max(0, m.start() - 70)
                    hits.append((path, d.text[s:m.start() + 90].replace("\n", " ")))
            if hits:
                print(f"    关键词 {kw!r} 出现在 {len(hits)} 处:")
                for path, ctx in hits[:3]:
                    print(f"      {path}")
                    print(f"        ...{ctx}...")
            else:
                print(f"    关键词 {kw!r}: **语料里没有**")
        print()

    print("=" * 96)
    print("  判定")
    print("=" * 96)
    print("    · 如果模型引用的段落里**真有**它说的信息 → 类 B：我题写错了，")
    print("      这两条其实是可答题，应该从对抗集移走（或改成正确答案进评估集）")
    print("    · 如果引用的段落里**没有**那个信息 → 类 A：真幻觉，")
    print("      要改提示词（要求它引用时必须逐字对应）")
    print()
    print("    这个区分很重要：把「我题写错」当成「模型幻觉」会去改一个")
    print("    本来正确的东西；反过来则会放过真正的幻觉风险。")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
