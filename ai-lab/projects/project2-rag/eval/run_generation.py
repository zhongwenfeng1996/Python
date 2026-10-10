#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端生成评估 —— 带引用回答 + 不足则弃答（ADR-006）

## 为什么生成的指标要单独一套

检索的指标（recall@5 / MRR）衡量"正确答案有没有被捞出来"。
但生成层要回答的是另外三个问题，必须各有一个可机械判定的指标：

  1. **引用对不对** —— 回答里的 [n] 必须在资料范围内（不是编造的）
  2. **该不该弃答** —— 不可答题要弃答（漏弃答是严重错误），
     可答题不该被误弃答（误弃答是体验问题）
  3. **有没有引用** —— 作答时必须给出可溯源的出处，否则无法核查

## 关键：所有指标都是机械判定的，不靠人眼看

"回答得好不好"是主观的，不入指标。能机械判定的才入：

  · 引用编号是否越界 → 正则提取后比大小
  · 是否弃答 → 匹配固定短语（提示词要求的固定说法）
  · 作答时是否有引用 → 提取结果非空

## 结果会落盘

每次跑都把完整结果写成 JSON（`data/generation-results.json`），
这样后续分析（改阈值、算新指标）**不用重新调 API**。

用法：
    python eval/run_generation.py              # 全量 56 条
    python eval/run_generation.py --limit 8    # 先小规模
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from rag.chunking import chunk_document                       # noqa: E402
from rag.corpus import load_corpus                             # noqa: E402
from rag.embeddings import get_embedder                        # noqa: E402
from rag.generate import ABSTAIN_PHRASE, GenerateConfig, Generator  # noqa: E402
from rag.llm_rerank import LLMRerankConfig, LLMReranker, LLMScorer  # noqa: E402
from rag.rerank import TwoStageRetriever                       # noqa: E402
from rag.retrieve import Retriever                             # noqa: E402
from rag.store import Store                                    # noqa: E402
from run_eval import load_questions                            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--recall-k", type=int, default=50,
                    help="重排候选池大小。**默认 50 是实测出来的**：\n"
                         "15→答案块@5 = 0.8462，50→0.9423，90→0.9423（饱和）\n"
                         "见 ADR-010。代价是每查询打分次数 ×3.1")
    ap.add_argument("--abstain-threshold", type=float, default=3.0)
    ap.add_argument("--max-passages", type=int, default=5)
    ap.add_argument("--out", default=str(HERE.parent / "data" / "generation-results.json"))
    args = ap.parse_args()

    questions = load_questions(HERE / "questions.jsonl")
    if args.limit:
        # 可答与不可答都要留一点，否则算不出弃答指标
        ans = [q for q in questions if q.answerable][: max(args.limit - 1, 1)]
        unans = [q for q in questions if not q.answerable][:1]
        questions = ans + unans

    docs = load_corpus()
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy="heading"))

    embedder = get_embedder("local")
    store = Store(HERE.parent / "data" / "gen-eval.db")
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    scorer = LLMScorer(LLMRerankConfig(
        cache_path=str(HERE.parent / "data" / "llm-score-cache.json"),
        batch_size=10, max_chars=400))
    ts = TwoStageRetriever(retriever, LLMReranker(scorer),
                           recall_channels="union", recall_k=args.recall_k)
    gen = Generator(GenerateConfig(
        abstain_threshold=args.abstain_threshold,
        max_passages=args.max_passages))

    n_ans = sum(1 for q in questions if q.answerable)
    n_unans = len(questions) - n_ans
    print("=" * 90)
    print("  端到端生成评估 · 引用 + 弃答")
    print("=" * 90)
    print(f"  评估集    : {len(questions)} 条（可答 {n_ans} / 不可答 {n_unans}）")
    print(f"  检索      : union@{args.recall_k} 粗召回 + LLM 重排 → 取前 {args.k}")
    print(f"  生成      : {gen.cfg.model}，送 {args.max_passages} 段资料")
    print(f"  弃答阈值  : {args.abstain_threshold}（低于它直接弃答，不调生成模型）")
    print(f"  弃答短语  : 「{ABSTAIN_PHRASE}」")
    print()

    records = []
    t0 = time.time()
    for i, q in enumerate(questions, 1):
        hits = ts.search(q.question, k=args.k)
        ans = gen.generate(q.question, hits)

        # ---- 机械判定的三项 ----
        # 1. 引用合法性：所有出现的编号都必须在 1..len(passages)
        n_pass = len(ans.passages)
        all_cited = set(ans.cited) | set(ans.invalid_citations)
        cites_ok = not ans.invalid_citations
        # 2. 作答时是否给了引用
        gave_cite = (not ans.abstained) and bool(ans.cited)
        # 3. 引用是否指向真实存在的那一段（越界已在上面判过）

        records.append({
            "id": q.id,
            "type": q.qtype,
            "answerable": q.answerable,
            "gold_doc": q.gold_doc,
            "question": q.question,
            "text": ans.text,
            "abstained": ans.abstained,
            "cited": ans.cited,
            "invalid_citations": ans.invalid_citations,
            "cites_ok": cites_ok,
            "gave_cite": gave_cite,
            "n_passages": n_pass,
            "top_score": ans.top_score,
            "tokens": ans.usage.get("total_tokens", 0),
            "elapsed_s": ans.elapsed_s,
            # 引用到的来源路径（便于人工抽查）
            "cited_sources": [h.source_path for h in ans.cited_sources],
            "passage_sources": [h.source_path for h in ans.passages],
            # gold 是否在送给模型的资料里
            # ⚠️ 这个指标**太松**：一份文档平均切成 20.4 块，
            #    "文件在资料里"不等于"含答案的那一块在资料里"。
            #    所以同时算一个更严的（含 must_contain 的块）。
            #    报告里两个都要写，只写前者会高估生成层的能力。
            "gold_in_context": any(h.source_path == q.gold_doc for h in ans.passages),
            "answer_chunk_in_context": (
                any(q.must_contain in h.text for h in ans.passages)
                if q.must_contain else
                any(h.source_path == q.gold_doc for h in ans.passages)
            ),
        })

        mark = "🚫" if ans.abstained else ("✅" if cites_ok else "❌")
        print(f"  [{i:>2}/{len(questions)}] {mark} {q.id:<6} "
              f"{'弃答' if ans.abstained else '作答'} "
              f"引用={ans.cited or '—'} "
              f"分={ans.top_score:.0f} "
              f"tok={ans.usage.get('total_tokens', 0)} "
              f"{ans.elapsed_s}s  {q.question[:34]}")

    wall = time.time() - t0
    total_tok = sum(r["tokens"] for r in records)

    # ================= 指标 =================
    ans_rec = [r for r in records if r["answerable"]]
    unans_rec = [r for r in records if not r["answerable"]]

    # 弃答准确性（不可答题里正确弃答的比例）—— 漏弃答是最严重的错误
    n_abstain_ok = sum(1 for r in unans_rec if r["abstained"])
    abstain_acc = n_abstain_ok / len(unans_rec) if unans_rec else 0.0
    # 误弃答率（可答题里被拒的比例）
    n_false_abstain = sum(1 for r in ans_rec if r["abstained"])
    false_abstain = n_false_abstain / len(ans_rec) if ans_rec else 0.0
    # 引用合法率（所有作答里，没有非法编号的比例）
    n_answered = sum(1 for r in records if not r["abstained"])
    cites_ok_rate = (sum(1 for r in records if not r["abstained"] and r["cites_ok"])
                     / n_answered) if n_answered else 0.0
    # 引用覆盖率（作答里带了引用的比例）
    give_cite_rate = (sum(1 for r in records if r["gave_cite"]) / n_answered) if n_answered else 0.0
    # 上下文命中率（可答题里 gold 被送进资料的比例）—— 这是生成的上限
    ctx_rate = (sum(1 for r in ans_rec if r["gold_in_context"]) / len(ans_rec)
                if ans_rec else 0.0)
    # **更严的上限**：含答案的块被送进资料的比例。
    # 这个才是"模型有没有机会答对"的真判据（见 eval/probe_context_precision.py）
    chunk_ctx_rate = (sum(1 for r in ans_rec if r["answer_chunk_in_context"])
                      / len(ans_rec) if ans_rec else 0.0)

    # 端到端：可答题里"给了带引用的回答"的比例
    e2e = (sum(1 for r in ans_rec if r["gave_cite"]) / len(ans_rec)) if ans_rec else 0.0

    print()
    print("─" * 90)
    print("  指标")
    print("─" * 90)
    print(f"  可答题 {len(ans_rec)} 条 / 不可答 {len(unans_rec)} 条")
    print()
    print(f"  ★ 弃答准确率（不可答题正确弃答）: {abstain_acc:.4f}  "
          f"({n_abstain_ok}/{len(unans_rec)})")
    print(f"    误弃答率（可答题被拒）        : {false_abstain:.4f}  "
          f"({n_false_abstain}/{len(ans_rec)})")
    print()
    print(f"  ★ 引用合法率（作答里无非法编号）: {cites_ok_rate:.4f}  "
          f"（分母 {n_answered} 条作答）")
    print(f"    引用覆盖率（作答里带引用）    : {give_cite_rate:.4f}")
    print()
    print(f"    上下文命中率（gold 文件进了资料）: {ctx_rate:.4f}  ← **太松**")
    print(f"    含答案的块进了资料              : {chunk_ctx_rate:.4f}  "
          f"← **生成的真实上限**")
    print(f"  ★ 端到端（可答题给出带引用回答）  : {e2e:.4f}")
    # 生成层的**净能力**：答案块在资料里时，模型答出来的比例。
    # 这才是衡量生成层的指标 —— 把检索的锅摘出去。
    if ans_rec:
        has = [r for r in ans_rec if r["answer_chunk_in_context"]]
        net = (sum(1 for r in has if r["gave_cite"]) / len(has)) if has else 0.0
        print(f"  ★ 生成层净能力（答案块在时答出）  : {net:.4f}  "
              f"({sum(1 for r in has if r['gave_cite'])}/{len(has)})")
        fah = [r for r in has if r["abstained"]]
        print(f"    其中仍然弃答（真缺陷）          : {len(fah)} 条"
              + (f"  {[r['id'] for r in fah]}" if fah else ""))
        # 答案块不在资料里时，模型是不是正确地弃答了（而不是硬答）
        no = [r for r in ans_rec if not r["answer_chunk_in_context"]]
        if no:
            ok_ab = sum(1 for r in no if r["abstained"])
            print(f"    答案块不在资料里时正确弃答      : {ok_ab}/{len(no)}"
                  f" = {ok_ab/len(no):.4f}  ← 拒答能力（没材料时不说瞎话）")
    print()
    print(f"  耗时 {wall:.1f}s   生成调用 {gen.calls}   合计 {total_tok} tokens")
    print()

    if unans_rec:
        print("  不可答题逐条:")
        for r in unans_rec:
            print(f"    {r['id']}  分数 {r['top_score']:.0f}  "
                  f"{'✅ 弃答' if r['abstained'] else '❌ 漏弃答: ' + r['text'][:40]}")
    print()
    print(f"  完整结果已写入: {args.out}")
    print("=" * 90)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps({"config": vars(args), "wall_s": round(wall, 1),
                    "metrics": {"abstain_acc": abstain_acc,
                                "false_abstain": false_abstain,
                                "cites_ok_rate": cites_ok_rate,
                                "give_cite_rate": give_cite_rate,
                                "ctx_hit_rate": ctx_rate,
                                "answer_chunk_ctx_rate": chunk_ctx_rate,
                                "e2e": e2e},
                    "records": records},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
