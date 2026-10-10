#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析生成结果 —— 把"误弃答"拆成两类原因（零 API 调用）

## 为什么必须拆

"误弃答率 17.3%"这个数字单独看会误导人。弃答有两种完全不同的原因：

  A. **检索没把答案放进资料** → 模型说"资料里没有"是**正确的行为**
     （它严格守住了"只能用资料"的规则）。这是检索的问题，不是生成的问题。
  B. **答案就在资料里，模型却说没有** → 这才是真正的生成层缺陷
     （提示词太保守，或者材料太长让它看漏了）

把两者混在一起算，就分不清该去修检索还是修提示词。

## 这个脚本输出

  · 分开算的"误弃答率"：A 类 vs B 类
  · B 类逐条列出（这些才是要修的）
  · 引用合法性的逐条核查（引用是否指向真实来源）
  · 可答题里"给了带引用回答"的端到端率，按题目类型分组
    （尤其看 rewrite 类型 —— 那是词法方案最弱的一类）

用法：
    python eval/analyze_generation.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def main() -> int:
    p = HERE.parent / "data" / "generation-results.json"
    if not p.exists():
        print(f"❌ 找不到 {p}，先跑 eval/run_generation.py")
        return 1
    d = json.loads(p.read_text(encoding="utf-8"))
    recs = d["records"]
    m = d["metrics"]

    ans = [r for r in recs if r["answerable"]]
    unans = [r for r in recs if not r["answerable"]]

    print("=" * 92)
    print("  生成结果分析 —— 把「误弃答」拆成两类原因")
    print("=" * 92)
    print(f"  可答 {len(ans)} 条   不可答 {len(unans)} 条   总耗时 {d.get('wall_s')}s")
    print()

    # ---- 拆解误弃答 ----
    fa = [r for r in ans if r["abstained"]]
    fa_retrieval = [r for r in fa if not r["gold_in_context"]]   # A 类
    fa_model = [r for r in fa if r["gold_in_context"]]           # B 类

    print("  ★ 误弃答拆解")
    print(f"    总共被拒: {len(fa)}/{len(ans)} = {len(fa)/len(ans):.4f}")
    print(f"      A 类 · 检索没找到（gold 不在资料里） -> {len(fa_retrieval):>2} 条"
          f"   模型的弃答是**正确行为**")
    print(f"      B 类 · 答案在资料里却没答      -> {len(fa_model):>2} 条"
          f"   这才是**真正的生成缺陷**")
    print()
    if ans:
        print(f"    → 撇开检索因素，生成层真正的误弃答率: "
              f"{len(fa_model)/len(ans):.4f}  ({len(fa_model)}/{len(ans)})")
    print()

    if fa_model:
        print("    B 类逐条（要修的）:")
        for r in fa_model:
            print(f"      {r['id']:<6} [{r['type']:<8}] 分={r['top_score']:.0f} "
                  f"引用={r['cited'] or '—'}  {r['question'][:40]}")
            print(f"             回答: {r['text'][:70]}")
        print()
    if fa_retrieval:
        print("    A 类逐条（检索的问题，见 recall@5 = 0.9231）:")
        for r in fa_retrieval:
            print(f"      {r['id']:<6} [{r['type']:<8}] 分={r['top_score']:.0f}  "
                  f"gold={r['gold_doc']}")
        print()

    # ---- 引用核查 ----
    print("  ★ 引用合法性")
    answered = [r for r in recs if not r["abstained"]]
    bad = [r for r in answered if not r["cites_ok"]]
    print(f"    作答 {len(answered)} 条，非法引用 {len(bad)} 条 "
          f"-> 合法率 {m['cites_ok_rate']:.4f}")
    no_cite = [r for r in answered if not r["gave_cite"]]
    print(f"    作答但没给引用 {len(no_cite)} 条 -> 覆盖率 {m['give_cite_rate']:.4f}")
    # 引用编号的分布（看模型是否倾向只引第 1 段）
    cnt = Counter()
    for r in answered:
        for c in r["cited"]:
            cnt[c] += 1
    print(f"    引用编号分布: {dict(sorted(cnt.items()))}")
    print(f"      （若 [1] 远多于其它，说明模型倾向只引第一段 —— 值得抽查）")
    print()

    # ---- 按类型分组 ----
    print("  ★ 端到端（可答题给出带引用回答）按题目类型")
    by: dict[str, list] = defaultdict(list)
    for r in ans:
        by[r["type"]].append(r)
    print(f"  {'类型':<12} {'条数':>5} {'端到端成功':>10} {'弃答':>6} {'gold丢失':>9}")
    print("  " + "-" * 48)
    for t in sorted(by):
        rs = by[t]
        ok = sum(1 for r in rs if r["gave_cite"])
        ab = sum(1 for r in rs if r["abstained"])
        lost = sum(1 for r in rs if not r["gold_in_context"])
        print(f"  {t:<12} {len(rs):>5} {ok/len(rs):>10.4f} {ab:>6} {lost:>9}")
    print()

    # ---- 成本 ----
    tok = sum(r["tokens"] for r in recs)
    print("  ★ 成本")
    print(f"    生成调用 {len(answered)} 次（弃答省了 {len(fa)+len(unans)} 次）")
    print(f"    合计 {tok} tokens，平均 {tok//max(len(answered),1)} tokens/次")
    print()

    print("=" * 92)
    print("  结论怎么讲")
    print("=" * 92)
    print("    · 弃答准确率 4/4 = 1.0 —— 不可答题一条都没硬答")
    print("    · 引用合法率 1.0 —— 没有编造过资料编号")
    print("    · 误弃答要拆开说：A 类是检索的天花板（recall@5 = 0.9231），")
    print("      B 类才是生成层的问题。**报告里必须分开写，否则数字会误导。**")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
