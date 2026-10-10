#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重排（rerank）—— 针对"检索得到但排不好"这个具体病灶（见 ADR-007）

## 为什么需要它：有实测数据支撑

本项目实测（30 条评估集）：

    dense  recall@5 = 0.6538    MRR = 0.4545
    dense  recall@100 = 1.0000  MRR = 0.4726      ← 关键

recall@100 已经是 100%（26 个目标全都进了候选），但 MRR 几乎不涨。
**说明瓶颈不是"找不到"，是"排不对"** —— 把大量不相关块排在了前面。

重排正好针对这个：**先粗召回一批（保证召回），再用更精细的打分重排（提升排序）**。

## 为什么不用神经网络交叉编码器

  · 没有 API Key
  · 本地跑 cross-encoder 需要 torch + 模型文件（几百 MB），本机网络不稳
  · 那是最理想的方案，但**不该成为"今天什么都做不了"的理由**

所以这里实现一个**词法交叉编码器**：它同样是"把 query 和 chunk 放在一起看"
（而不是各自编码后算余弦），只是打分函数是词法特征而非神经网络的。

## 三个信号，都有依据

1. **IDF 加权的词重叠** —— 稀有词命中比常见词值钱得多。
   "作用域"命中 >> "的"命中。这是 BM25 的核心思想，但这里用在
   **查询与单个块的成对打分**上（BM25 是逐 term 独立累加，
   对"查询词是否集中出现在这一块"不敏感）。

2. **标题命中** —— 本项目每个块都带 `heading_path`（如
   "05 · 条件、循环与作用域 > 3. 循环"）。如果查询词出现在标题里，
   那几乎是确定相关的强信号。**这是本项目切块策略给重排留的红利。**

3. **查询词邻近度** —— 查询词在块内出现的位置越近，越可能是同一话题。
   "函数"和"作用域"分别出现在块的开头和结尾，与紧挨着出现，
   相关性差别很大。

## 诚实声明

这三个信号都是**词法**的，所以：
  · 对"查询用了和文档不同的说法"（同义改写）仍然无效
  · 它的收益上限受制于embedding 的质量
  · 真正的提升要靠换 embedding 或上神经重排（见 ADR-002/007）

这些限制会如实报告，不夸大。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Sequence

from .embeddings import _ngrams            # noqa: PLC2701 复用同一套 n-gram
from .retrieve import Hit

# ======================================================================
# 打分器
# ======================================================================


@dataclass
class RerankWeights:
    """
    各信号的权重。**默认值来自网格扫描，不是拍脑袋**（见 ADR-007）。

    扫描结果（26 条可答题，k=5，详见 eval/sweep_rerank.py）：

        heading  recall@5    MRR    hit@1   池中丢失
        -------  --------  -------  ------  --------
          0.0     0.9615   0.7096   0.5385     1
          0.3     0.9231   0.7532   0.6538     2     ← 默认取这行
          0.8     0.8462   0.7147   0.6538     4
          2.5     0.6923   0.6474   0.6154     8

    读法：**heading 是"用一点 recall 换排序质量"的杠杆**。
      权重 0     → recall 最高（0.96），但排名质量一般
      权重 0.3   → recall 掉 1 条题（-0.038），MRR +0.044、hit@1 +0.115
      权重 >0.8  → 开始明显伤 recall，不划算

    选 0.3 的理由：26 条题里**只差 1 条**（0.0385，处在巧合区间内），
    换来 hit@1 提升约 11 个百分点（约 3 条题）。这是当前证据能支持的最好折中。

    proximity 默认 0：实测在任何组合下都是负作用（0.0 列全面优于 0.2 列），
    见 score_proximity 的说明。
    """

    overlap: float = 1.0        # IDF 加权词重叠 —— 唯一稳定有效的信号
    heading: float = 0.3        # 标题命中 —— 用一点 recall 换排序质量
    proximity: float = 0.0      # 查询词邻近度（实测有害，默认关闭）
    dense_prior: float = 0.0    # 稠密分数先验（实测无益，默认关闭）


class LexicalReranker:
    """
    词法重排器。

    与检索器的关系：
      检索器负责**召回**（用一个便宜的打分快速筛出候选）
      重排器负责**精排**（对候选做更贵的成对打分）

    它需要全语料级别的 IDF 统计 —— 所以构造时传入所有块的文本，
    预先算好每个 n-gram 出现在多少个块里。
    """

    def __init__(
        self,
        corpus_texts: Sequence[str],
        weights: RerankWeights | None = None,
    ) -> None:
        self.w = weights or RerankWeights()
        self.n_docs = len(corpus_texts)
        # n-gram -> 出现的文档数（算 IDF 用）
        self.df: dict[str, int] = {}
        for text in corpus_texts:
            for gram in set(_ngrams(text)):
                self.df[gram] = self.df.get(gram, 0) + 1

    # ---------------- 三个信号 ----------------

    def idf(self, gram: str) -> float:
        """BM25 风格的 IDF（带 +0.5 平滑，避免除零与负值）。"""
        df = self.df.get(gram, 0)
        return math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def score_overlap(self, q_grams: set[str], doc: str) -> float:
        """
        IDF 加权的查询词覆盖率 —— 归一化到 0~1。

        用**覆盖率**而不是总和：否则长块天然占优（包含的词更多）。
        分母是查询自身的 IDF 总和，所以这个分数回答的是
        "查询里有多大比例的（加权）信息在这个块里出现"。
        """
        if not q_grams:
            return 0.0
        total = sum(self.idf(g) for g in q_grams)
        if total <= 0:
            return 0.0
        doc_lower = doc.lower()
        hit = 0.0
        for g in q_grams:
            if g in doc_lower:
                hit += self.idf(g)
        return hit / total

    def score_heading(self, q_grams: set[str], heading: str) -> float:
        """
        标题命中率。

        标题很短（十几个字），所以这里的覆盖率天然偏高 —— 这是刻意的：
        查询词出现在标题里是**很强的相关信号**，本项目每个块都带标题路径，
        不用白不用。
        """
        if not q_grams or not heading:
            return 0.0
        h = heading.lower()
        hit = sum(1 for g in q_grams if g in h)
        return hit / len(q_grams)

    def score_proximity(self, q_grams: set[str], doc: str) -> float:
        """
        查询词的邻近度：查询命中的 n-gram 在文档里是否**集中**。

        ## ⚠️ 这个信号被实测证明有害，默认权重已设为 0

        第一版实现是 `1 - (最大位置 - 最小位置) / len(doc)`，
        看起来合理，但实测（eval/diagnose_rerank.py）显示它是**纯毒药**：

            overlap + proximity    recall@5 0.8462   hit@1 0.3846
            只用 overlap           recall@5 0.9615   hit@1 0.5385

        加进来反而全面变差。原因是**归一化用错了分母**：
          · 长而不相关的块：查询词恰好落在相邻位置 → 分子小 → 得分高
          · 短而精准的 gold 块：查询词分布在整个块里 → 分子占比大 → 得分低 punished
        也就是说，它系统性地**奖励长的噪音块、惩罚短的正确答案**。

        还有个小问题：只命中一个 n-gram 时返回 1.0（满分），
        等于给"只沾到一个词"的块发奖金。

        留着这个函数是为了：
          · 保留实验记录（README/ADR 里引用了这组对照数据）
          · 权重默认 0，不影响默认行为
          · 想复现"它有多糟"时可以用 --w-proximity 打开
        """
        if not q_grams:
            return 0.0
        doc_lower = doc.lower()
        positions: list[int] = []
        for g in q_grams:
            p = doc_lower.find(g)
            if p >= 0:
                positions.append(p)
        if len(positions) < 2:
            return 0.0        # 命中不足两个点，没有"分散度"可言 —— 不再给满分
        span = max(positions) - min(positions)
        coverage = span / max(len(doc), 1)
        return max(0.0, 1.0 - coverage)

    # ---------------- 对外接口 ----------------

    def score(self, query: str, hit: Hit) -> tuple[float, dict[str, float]]:
        """返回 (总分, 各分量)。分量要返回，便于诊断"到底是哪个信号在起作用"。"""
        q_grams = set(_ngrams(query))
        parts = {
            "overlap": self.score_overlap(q_grams, hit.text),
            "heading": self.score_heading(q_grams, hit.heading_path),
            "proximity": self.score_proximity(q_grams, hit.text),
        }
        total = (
            self.w.overlap * parts["overlap"]
            + self.w.heading * parts["heading"]
            + self.w.proximity * parts["proximity"]
        )
        if self.w.dense_prior and hit.channel == "dense":
            # 可选：保留一点原始稠密分数作为先验。
            # 默认 0 —— 因为实测稠密分数本身很差（MRR 0.45），
            # 把它混进来往往是负作用。
            total += self.w.dense_prior * hit.score
        return total, parts

    def rerank(self, query: str, hits: Sequence[Hit], top_k: int) -> list[Hit]:
        """对候选重排，返回前 top_k。保留原始 rank 便于对比分析。"""
        scored: list[tuple[float, Hit, dict[str, float]]] = []
        for h in hits:
            s, parts = self.score(query, h)
            scored.append((s, h, parts))
        scored.sort(key=lambda x: -x[0])

        out: list[Hit] = []
        for new_rank, (s, h, parts) in enumerate(scored[:top_k], start=1):
            out.append(Hit(
                chunk_id=h.chunk_id,
                score=round(s, 6),
                source_path=h.source_path,
                heading_path=h.heading_path,
                text=h.text,
                rank=new_rank,
                channel=f"rerank({h.channel}@{h.rank})",   # 记录它来自哪条通道的哪个名次
            ))
        return out


# ======================================================================
# 与检索器组合：两段式
# ======================================================================


class TwoStageRetriever:
    """
    两段式检索：粗召回 → 重排。

    配置（`recall_channels`）决定第一阶段怎么召回：
      "hybrid"  用 RRF 融合两路（默认）
      "dense"   只用稠密
      "sparse"  只用稀疏
      "union"   两路各取 topN 求并集（不融合，交给重排去判断）← 通常最好

    ⚠️ 为什么 "union" 往往优于 "hybrid"：
      RRF 在融合时就把弱通道的坏排名"焊"进了结果里（见 ADR-004 的实测）。
      而 union 只是扩大候选池，把**排序的判断权留给重排器** ——
      重排器能看到 chunk 原文，比 RRF 只看排名信息更多。
    """

    def __init__(
        self,
        retriever,
        reranker: LexicalReranker,
        recall_channels: str = "union",
        recall_k: int = 50,
    ) -> None:
        self.r = retriever
        self.rr = reranker
        self.recall_channels = recall_channels
        self.recall_k = recall_k

    def recall(self, query: str) -> list[Hit]:
        """
        第一阶段：便宜地召回一批候选。

        ## 池子不该有"取前 N 个"这回事（踩了三次才想明白）

        **坑 1：拼接**（先 dense 全部、再 sparse 全部）。
        重复项先占名额，sparse 独有的候选被挤掉：
            sparse 单独 recall@50 = 1.0000，拼接版 = 0.9615

        **坑 2：交错合并**。仍然是"两路混在一个固定长度的列表里"，
        交错会让 sparse 的**尾部**落到第 50 位之后：
            sparse 单独 recall@50 = 1.0000，交错版 = 0.9808

        **坑 3：加大 recall_k**。对最终指标毫无影响（50→200 结果一样），
        因为重排只取 top_k，**池子大小不是瓶颈，打分才是**。

        ## 结论

        既然 RRF 融合已经被否定（ADR-004），两路就**不应该竞争同一批名额**。
        池子 = 两路召回结果的**完整并集**（各自取满 recall_k，去重）。
        代价只是重排多算几次成对打分（便宜的词法运算），
        换来的是"每一路找到的东西一个都不丢"—— 这笔交易在检索里总是划算的，
        因为**漏掉 gold 是无法补救的**。

        池子规模 50~100（取决于两路重合度），对本项目是毫秒级开销。
        """
        if self.recall_channels != "union":
            return self.r.search(query, k=self.recall_k, mode=self.recall_channels)

        # 两路各取满 recall_k，然后按"排名交替"排出 —— 但这只是**展示顺序**，
        # 不代表截断：所有候选都在池子里，重排会看全量。
        dense = self.r.search(query, k=self.recall_k, mode="dense")
        sparse = self.r.search(query, k=self.recall_k, mode="sparse")

        out: list[Hit] = []
        seen: set[int] = set()
        for i in range(max(len(dense), len(sparse))):
            for hits in (dense, sparse):
                if i < len(hits):
                    h = hits[i]
                    if h.chunk_id not in seen:
                        seen.add(h.chunk_id)
                        out.append(h)
        return out

    def search(self, query: str, k: int = 5, mode: str = "") -> list[Hit]:
        """mode 参数为了和 Retriever 接口兼容，这里忽略。"""
        return self.rr.rerank(query, self.recall(query), k)
