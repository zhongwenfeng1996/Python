#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
弃答判定的指标缺陷核查（零 API 调用）

## 我发现的自己的 bug

`looks_like_abstain()` 的实现是：

    return ABSTAIN_PHRASE in text          # 子串包含

这个判据**太松**。模型在正常作答时，如果末尾补一句
"（某部分）资料中没有相关信息"，就会被误判成弃答。

实测发现的例子（q044）：它明明作答了、还引用了 [1]，
但因为末尾提了这句话，被算成"误弃答"。

## 这个脚本做什么

1. 把所有"被判为弃答、但回答很长"的案例列出来 —— 这些可疑
2. 对比几种更严的判据，看指标会怎么变
3. 报出真实数字

## 为什么值得花时间修这个

因为它会**同时污染两个关键指标**：
  · 弃答准确率被**高估**（正常回答被算成弃答，显得很会拒答）
  · 端到端成功率被**低估**（明明答了却被算没答）

一个坏判据会让两边都错，而且错的方向相反 —— 这种最难发现。

用法：
    python eval/probe_abstain_metric.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from rag.generate import ABSTAIN_PHRASE  # noqa: E402


# ---------------- 几种候选判据 ----------------

def rule_contains(text: str) -> bool:
    """现状：只要包含弃答短语就算弃答。**太松。**"""
    return ABSTAIN_PHRASE in text


def rule_short_only(text: str, max_len: int = 40) -> bool:
    """
    收紧版：必须是**短回答**且包含短语。

    理由：真弃答时提示词要求"就这七个字，不要加别的解释"，
    所以回答应该很短。正常作答里夹带这句话的，通常长得多。
    """
    t = text.strip()
    return len(t) <= max_len and ABSTAIN_PHRASE in t


def rule_startswith(text: str) -> bool:
    """更严：回答**以**弃答短语开头（允许前面有几个符号）。"""
    t = text.strip().lstrip("*# >-")
    return t.startswith(ABSTAIN_PHRASE)


def rule_no_citation(text: str, cited: list[int]) -> bool:
    """
    最贴合作答实质的判据：**没有引用**且包含弃答短语。

    理由：真弃答不可能给出引用（它没有任何论断需要溯源）。
    所以"有引用"基本等价于"真的作答了"。
    """
    return ABSTAIN_PHRASE in text and not cited


RULES = {
    "现状: 包含子串": lambda r: rule_contains(r["text"]),
    "收紧: 短回答只含短语": lambda r: rule_short_only(r["text"]),
    "更严: 以短语开头": lambda r: rule_startswith(r["text"]),
    "最贴合: 含短语且无引用": lambda r: rule_no_citation(r["text"], r["cited"]),
    "组合: 无引用 且 (短 或 开头)": lambda r: (
        not r["cited"]
        and (rule_short_only(r["text"]) or rule_startswith(r["text"]))
    ),
}


def main() -> int:
    p = HERE.parent / "data" / "generation-results.json"
    if not p.exists():
        print(f"❌ 找不到 {p}，先跑 eval/run_generation.py")
        return 1
    d = json.loads(p.read_text(encoding="utf-8"))
    recs = d["records"]

    print("=" * 92)
    print("  弃答判定的指标缺陷核查（零 API 调用）")
    print("=" * 92)
    print(f"  弃答短语: 「{ABSTAIN_PHRASE}」")
    print(f"  样本: {len(recs)} 条")
    print()

    # ---- 1. 可疑案例 ----
    sus = [r for r in recs if r["abstained"] and len(r["text"].strip()) > 40]
    print(f"  ★ 被判为弃答、但回答长度 > 40 字的: {len(sus)} 条（可疑）")
    for r in sus:
        print(f"    {r['id']} 可答={r['answerable']} 引用={r['cited']} "
              f"长度={len(r['text'].strip())}")
        print(f"      {r['text'][:180].replace(chr(10), ' ')}")
    print()

    # ---- 2. 各判据下的指标对比 ----
    print("  ★ 换判据后指标怎么变")
    ans = [r for r in recs if r["answerable"]]
    unans = [r for r in recs if not r["answerable"]]
    print(f"  {'判据':<26} {'弃答准确率':>10} {'误弃答率':>9} {'端到端':>8} "
          f"{'被判弃答':>9}")
    print("  " + "-" * 68)
    for name, fn in RULES.items():
        ab = {r["id"]: fn(r) for r in recs}
        acc = (sum(1 for r in unans if ab[r["id"]]) / len(unans)) if unans else 0
        fa = (sum(1 for r in ans if ab[r["id"]]) / len(ans)) if ans else 0
        e2e = (sum(1 for r in ans
                   if not ab[r["id"]] and r["cited"]) / len(ans)) if ans else 0
        n_ab = sum(1 for r in recs if ab[r["id"]])
        print(f"  {name:<26} {acc:>10.4f} {fa:>9.4f} {e2e:>8.4f} {n_ab:>9}")

    print()
    print("=" * 92)
    print("  读法")
    print("=" * 92)
    print("    · 弃答准确率**下降**说明现状高估了它（把正常回答算成了弃答）")
    print("    · 端到端**上升**说明现状低估了它")
    print("    · 两者同时朝这个方向变，就是判据太松的确证")
    print("    · 最终该用哪条：既不能漏掉真弃答，也不能把作答算成弃答。")
    print("      '含短语且无引用' 在语义上最贴合 —— 有引用就必然作答了。")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
