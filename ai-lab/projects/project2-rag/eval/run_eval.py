#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检索评估：recall@k / MRR / 拒答准确率（见 ADR-005）

## 为什么这个脚本是本项目最重要的文件

RAG 最容易被做成"试几个问题看起来能答"。没有这个脚本，
你无法回答面试官那句"**你的检索准不准？**"。

## 指标口径（写清楚，避免自欺）

  recall@k    前 k 个结果里包含 gold_doc 的查询比例。
              分母 = **有 gold_doc 的查询数**（不可答题不参与，单独算拒答）。
  MRR         第一个命中结果的排名倒数的平均。
              比 recall@k 更敏感：命中但排第 5 与排第 1，recall 都一样，MRR 能区分。
  hit@1       第一名就命中的比例 —— 直接决定"塞进 prompt 的上下文干不干净"。
  拒答准确率  不可答题里系统正确拒答的比例（需要检索分数阈值，见 --abstain-threshold）。

## 门禁

  `--fail-under recall@5=0.6` 指标不达标就返回非 0，可以挂到 CI。

## 评估集必须被校验

脚本会检查每条题的 `gold_doc` 真实存在、且 `must_contain`（如果给了）
真的能在那篇文档里找到。**评估集造假 = 后面所有指标都没有意义。**
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rag.chunking import STRATEGIES, chunk_document     # noqa: E402
from rag.corpus import load_corpus                       # noqa: E402
from rag.embeddings import get_embedder                  # noqa: E402
from rag.rerank import LexicalReranker, RerankWeights, TwoStageRetriever  # noqa: E402
from rag.retrieve import Retriever                       # noqa: E402
from rag.store import Store                              # noqa: E402


@dataclass
class Question:
    id: str
    question: str
    answer: str
    gold_doc: str                       # 相对仓库根；空字符串 = 不可答题
    qtype: str = "fact"
    difficulty: str = "easy"
    must_contain: str = ""              # 校验用：答案必须能在 gold_doc 里找到

    @property
    def answerable(self) -> bool:
        return bool(self.gold_doc)


@dataclass
class EvalResult:
    mode: str
    strategy: str
    k: int
    n_answerable: int = 0
    n_unanswerable: int = 0
    recall: float = 0.0
    mrr: float = 0.0
    hit1: float = 0.0
    abstain_acc: float = 0.0
    per_question: list[dict] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "strategy": self.strategy, "k": self.k,
            "n_answerable": self.n_answerable,
            "n_unanswerable": self.n_unanswerable,
            f"recall@{self.k}": round(self.recall, 4),
            "mrr": round(self.mrr, 4),
            "hit@1": round(self.hit1, 4),
            "abstain_acc": round(self.abstain_acc, 4),
            "elapsed_s": round(self.elapsed_s, 2),
        }


def load_questions(path: Path) -> list[Question]:
    out: list[Question] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"评估集第 {lineno} 行不是合法 JSON：{exc}") from exc
        out.append(Question(
            id=d["id"], question=d["question"], answer=d.get("answer", ""),
            gold_doc=d.get("gold_doc", ""), qtype=d.get("type", "fact"),
            difficulty=d.get("difficulty", "easy"),
            must_contain=d.get("must_contain", ""),
        ))
    return out


def validate_questions(questions: list[Question], docs: list) -> list[str]:
    """
    校验评估集本身。返回问题列表（空 = 全部通过）。

    这一步不能省：gold_doc 写错一个字符，那条题的指标就永远是 0，
    而你会以为是检索算法不行 —— 排查方向全错。
    """
    problems: list[str] = []
    by_path = {d.rel_path: d for d in docs}
    for q in questions:
        if not q.answerable:
            continue
        doc = by_path.get(q.gold_doc)
        if doc is None:
            problems.append(f"{q.id}: gold_doc 不存在 -> {q.gold_doc!r}")
            continue
        if q.must_contain and q.must_contain not in doc.text:
            problems.append(
                f"{q.id}: must_contain {q.must_contain!r} 在 {q.gold_doc} 里找不到"
            )
        if not q.answer:
            problems.append(f"{q.id}: 可答题但没写 answer")
    return problems


def evaluate(
    retriever: Retriever,
    questions: list[Question],
    mode: str,
    strategy: str,
    k: int = 5,
    abstain_threshold: float = 0.0,
) -> EvalResult:
    """
    abstain_threshold 用于不可答题：
      不可答题没有 gold_doc，我们期望系统"拒答"。
      但检索器总会返回 top-k，所以要有个判据 —— 这里用**最高分低于阈值**表示"没找到"。
      同一个阈值也用于可答题（分数太低就算它没找到）。
    """
    res = EvalResult(mode=mode, strategy=strategy, k=k)
    t0 = time.time()
    ranks: list[int] = []
    hit1 = 0
    abstain_ok = 0

    for q in questions:
        hits = retriever.search(q.question, k=k, mode=mode)
        top_score = hits[0].score if hits else 0.0
        found_rank = 0
        for h in hits:
            if h.source_path == q.gold_doc:
                found_rank = h.rank
                break

        if q.answerable:
            res.n_answerable += 1
            if found_rank:
                ranks.append(found_rank)
                if found_rank == 1:
                    hit1 += 1
            else:
                # 没命中：MRR 记 0（不是不记 —— 不记会让 MRR 虚高）
                ranks.append(0)
        else:
            res.n_unanswerable += 1
            # 拒答判据：最高分低于阈值 = 系统承认"我没找到"
            if top_score < abstain_threshold:
                abstain_ok += 1

        res.per_question.append({
            "id": q.id, "question": q.question, "mode": mode,
            "gold_doc": q.gold_doc, "found_rank": found_rank,
            "top_score": round(top_score, 4),
            "top1": hits[0].source_path if hits else "",
        })

    res.elapsed_s = time.time() - t0
    if res.n_answerable:
        res.recall = sum(1 for r in ranks if r > 0) / res.n_answerable
        res.mrr = sum(1.0 / r for r in ranks if r > 0) / res.n_answerable
        res.hit1 = hit1 / res.n_answerable
    if res.n_unanswerable:
        res.abstain_acc = abstain_ok / res.n_unanswerable
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG 检索评估")
    ap.add_argument("--questions", default=str(HERE / "questions.jsonl"))
    ap.add_argument("--strategy", default="heading", choices=list(STRATEGIES))
    ap.add_argument("--modes", default="dense,sparse,hybrid")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--abstain-threshold", type=float, default=0.0,
                    help="最高分低于它就算'没找到'（用于不可答题的拒答判定）")
    ap.add_argument("--fail-under", default="",
                    help="门禁，如 recall@5=0.6,mrr=0.5")
    ap.add_argument("--report", default=str(HERE / "report.json"))
    ap.add_argument("--verbose", action="store_true", help="列出每条题的命中情况")
    # ---- 重排相关（见 ADR-007）----
    ap.add_argument("--rerank", action="store_true",
                    help="打开两段式：粗召回 + 词法重排")
    ap.add_argument("--recall-channels", default="union",
                    choices=["union", "dense", "sparse", "hybrid"],
                    help="第一阶段召回方式。union = 两路各取 topN 求并集交给重排")
    ap.add_argument("--recall-k", type=int, default=50,
                    help="第一阶段每路召回多少个候选")
    ap.add_argument("--w-overlap", type=float, default=1.0, help="重排权重：IDF 词重叠")
    ap.add_argument("--w-heading", type=float, default=RerankWeights().heading,
                    help="重排权重：标题命中（默认取自 RerankWeights）")
    # ⚠️ 这里的默认值必须与 RerankWeights 的默认值一致。
    #    踩过的坑：dataclass 里把 proximity 默认改成 0（实测有害），
    #    但 CLI 这边还写着 0.3 —— argparse 的默认值会**覆盖** dataclass 的默认值，
    #    于是"改了默认值"其实没生效，指标一直没变，白跑了几轮。
    #    教训：默认值只留一个来源，或者两处必须同步。
    ap.add_argument("--w-proximity", type=float, default=RerankWeights().proximity,
                    help="重排权重：邻近度（实测有害，默认 0）")
    ap.add_argument("--w-dense", type=float, default=RerankWeights().dense_prior,
                    help="重排权重：原始稠密分数先验")
    args = ap.parse_args()

    qpath = Path(args.questions)
    if not qpath.exists():
        print(f"评估集不存在：{qpath}")
        return 2
    questions = load_questions(qpath)

    docs = load_corpus()
    problems = validate_questions(questions, docs)
    if problems:
        print("=" * 78)
        print("  ❌ 评估集本身有问题，先修它再看指标（否则指标没有意义）")
        print("=" * 78)
        for p in problems:
            print(f"    - {p}")
        return 2

    embedder = get_embedder(prefer="local")
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d.text, d.rel_path, strategy=args.strategy))

    db = HERE.parent / "data" / f"eval-{args.strategy}.db"
    store = Store(db)
    store.build(chunks, embedder, batch_size=64)
    retriever = Retriever(store, embedder)

    # ---- 可选：两段式（粗召回 + 重排）----
    # 重排器需要全语料的 IDF 统计，所以要把所有块文本传进去预计算。
    reranker = None
    two_stage = None
    if args.rerank:
        weights = RerankWeights(
            overlap=args.w_overlap,
            heading=args.w_heading,
            proximity=args.w_proximity,
            dense_prior=args.w_dense,
        )
        reranker = LexicalReranker([c.text for c in chunks], weights=weights)
        two_stage = TwoStageRetriever(
            retriever, reranker,
            recall_channels=args.recall_channels,
            recall_k=args.recall_k,
        )

    print("=" * 78)
    print("  检索评估 · recall@k / MRR")
    print("=" * 78)
    print(f"  embedder   : {embedder.signature}")
    print(f"  切块策略   : {args.strategy}（{len(chunks)} 块）")
    print(f"  评估集     : {len(questions)} 条"
          f"（可答 {sum(1 for q in questions if q.answerable)} / "
          f"不可答 {sum(1 for q in questions if not q.answerable)}）")
    print(f"  k          : {args.k}")
    print(f"  拒答阈值   : {args.abstain_threshold}")
    if args.rerank:
        print(f"  重排       : 开  召回={args.recall_channels}@{args.recall_k}  "
              f"权重 overlap={args.w_overlap} heading={args.w_heading} "
              f"proximity={args.w_proximity} dense_prior={args.w_dense}")
    else:
        print("  重排       : 关（加 --rerank 打开）")
    print()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    results: list[EvalResult] = []
    for mode in modes:
        if mode == "rerank":
            if two_stage is None:
                print("  ⚠️ --modes 里有 rerank 但没加 --rerank，跳过")
                continue
            # 用两段式检索器替换掉单通道检索器
            r = evaluate(two_stage, questions, "rerank", args.strategy,
                         k=args.k, abstain_threshold=args.abstain_threshold)
        else:
            r = evaluate(retriever, questions, mode, args.strategy,
                         k=args.k, abstain_threshold=args.abstain_threshold)
        results.append(r)

    # ---- 对比表（这是项目的核心产出）----
    print("─" * 78)
    print(f"  {'通道':<8} {'recall@' + str(args.k):>10} {'MRR':>8} {'hit@1':>8} "
          f"{'拒答':>8} {'耗时':>9}")
    print("─" * 78)
    for r in results:
        print(f"  {r.mode:<8} {r.recall:>10.4f} {r.mrr:>8.4f} {r.hit1:>8.4f} "
              f"{r.abstain_acc:>8.4f} {r.elapsed_s:>8.2f}s")

    best = max(results, key=lambda r: (r.recall, r.mrr))
    print("─" * 78)
    print(f"  最佳通道：{best.mode}（recall@{args.k}={best.recall:.4f}, "
          f"MRR={best.mrr:.4f}）")
    print()

    if args.verbose:
        print("  逐题明细（未命中的标 ❌）:")
        for r in results:
            print(f"\n  —— {r.mode} ——")
            for pq in r.per_question:
                if not pq["gold_doc"]:
                    continue
                mark = "✅" if pq["found_rank"] else "❌"
                rank = pq["found_rank"] or "-"
                print(f"    {mark} rank={rank:<3} {pq['id']:<6} {pq['question'][:44]}")

    # ---- 落盘 + 门禁 ----
    report = {
        "strategy": args.strategy, "k": args.k,
        "abstain_threshold": args.abstain_threshold,
        "n_questions": len(questions),
        "embedder": embedder.signature,
        "n_chunks": len(chunks),
        "results": [r.to_dict() for r in results],
        "per_question": [pq for r in results for pq in r.per_question],
    }
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  报告已写入：{rp}")

    store.close()

    if args.fail_under:
        failures = []
        for spec in args.fail_under.split(","):
            spec = spec.strip()
            if "=" not in spec:
                continue
            name, val = spec.split("=", 1)
            name, target = name.strip(), float(val)
            for r in results:
                got = (r.recall if name == f"recall@{args.k}"
                       else r.mrr if name == "mrr"
                       else r.hit1 if name == "hit@1"
                       else r.abstain_acc if name in ("abstain", "abstain_acc")
                       else None)
                if got is None:
                    continue
                if got + 1e-9 < target:
                    failures.append(
                        f"{r.mode}: {name}={got:.4f} < 要求 {target}")
        print()
        if failures:
            print("=" * 78)
            print("  ❌ 门禁未通过：")
            for f in failures:
                print(f"    - {f}")
            print("=" * 78)
            return 1
        print("  ✅ 门禁通过")

    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
