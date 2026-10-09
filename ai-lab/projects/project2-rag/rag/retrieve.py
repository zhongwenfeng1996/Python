#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检索层：稠密 / 稀疏 / RRF 混合（见 ADR-004）

三个通道都要能单独跑，因为**评估必须分别报告它们的指标** ——
那组对比表才是这个项目的核心产出（而不是"我做了个 RAG"）。

## 为什么混合用 RRF 而不是加权求和

  稠密分数是余弦（0~1），稀疏分数是 BM25（无上界）。量纲不同，
  加权求和必须调权重 + 归一化，非常脆弱 —— 换一份语料权重就失效。

  RRF 只看**排名**，对分数尺度完全不敏感：

      RRF(d) = Σ_i  1 / (k + rank_i(d))          k 通常取 60

  一个参数、有成熟默认值、不需要归一化。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .embeddings import Embedder, _ngrams  # noqa: PLC2701
from .store import Store, cosine, unpack_vector

#: RRF 的平滑常数。60 是原论文（Cormack et al. 2009）的经验值，
#: 作用是压低"排名很靠前"的边际收益，让多路结果的融合更稳。
RRF_K = 60


@dataclass
class Hit:
    """一条检索结果。带上来源信息 —— 引用功能的前提。"""

    chunk_id: int
    score: float
    source_path: str
    heading_path: str
    text: str
    rank: int = 0
    #: 该结果来自哪条通道（dense/sparse/hybrid），用于调试和对比
    channel: str = ""

    @property
    def preview(self) -> str:
        t = self.text.replace("\n", " ")
        return t[:100] + ("…" if len(t) > 100 else "")


class Retriever:
    """
    检索器。**在内存里持有全部向量** —— 这是 P1 阶段的刻意选择（ADR-003）：

      - 几百到几千个块，暴力余弦毫秒级，完全够用
      - 你会亲身体会到"够用"的边界在哪，等换成索引时能说出具体加速倍数
    """

    def __init__(self, store: Store, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder
        self.metas, self.vecs, self.dim = store.load_matrix()
        # chunk_id -> 在 metas/vecs 里的下标
        self._by_id = {m.id: i for i, m in enumerate(self.metas)}
        self._n = len(self.metas)
        # 平均块长，BM25 要用
        self._avg_len = (
            sum(m.char_len for m in self.metas) / self._n if self._n else 1.0
        )

    # ------------------------------------------------------------------
    # 通道 1：稠密（向量余弦）
    # ------------------------------------------------------------------

    def search_dense(self, query: str, k: int = 5) -> list[Hit]:
        if not self._n:
            return []
        qv = self.embedder.embed_one(query)
        scored = [(cosine(qv, v), i) for i, v in enumerate(self.vecs)]
        scored.sort(key=lambda x: -x[0])
        out: list[Hit] = []
        for rank, (score, i) in enumerate(scored[:k], start=1):
            m = self.metas[i]
            out.append(Hit(chunk_id=m.id, score=score, source_path=m.source_path,
                           heading_path=m.heading_path, text=m.text,
                           rank=rank, channel="dense"))
        return out

    # ------------------------------------------------------------------
    # 通道 2：稀疏（BM25 的简化实现）
    # ------------------------------------------------------------------

    def search_sparse(self, query: str, k: int = 5) -> list[Hit]:
        """
        BM25。用自建倒排（不用 FTS5，原因见 store.py 的模块注释）。

        BM25 的三个机制，缺一不可：
          1. **IDF 加权** —— 稀有词更重要（"作用域"比"的"值钱）
          2. **词频饱和** —— tf 从 1→2 的收益远大于 10→11，所以用
             tf*(k1+1)/(tf+k1*...) 而不是直接用 tf
          3. **长度归一化** —— 长块天然更容易命中词，要按 len/avg_len 惩罚
        """
        if not self._n:
            return []
        terms = list(dict.fromkeys(_ngrams(query)))     # 去重但保序
        if not terms:
            return []

        postings = self.store.postings_for(terms)       # {term: {chunk_id: tf}}
        if not postings:
            return []

        k1, b = 1.5, 0.75
        N = self._n
        scores: dict[int, float] = {}
        for term, chunk_tfs in postings.items():
            df = len(chunk_tfs)                          # 该 term 出现在几个块里
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            for cid, tf in chunk_tfs.items():
                i = self._by_id.get(cid)
                if i is None:
                    continue
                clen = self.metas[i].char_len
                denom = tf + k1 * (1 - b + b * clen / max(self._avg_len, 1))
                scores[cid] = scores.get(cid, 0.0) + idf * (tf * (k1 + 1)) / max(denom, 1e-9)

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        out: list[Hit] = []
        for rank, (cid, score) in enumerate(ranked[:k], start=1):
            m = self.metas[self._by_id[cid]]
            out.append(Hit(chunk_id=m.id, score=score, source_path=m.source_path,
                           heading_path=m.heading_path, text=m.text,
                           rank=rank, channel="sparse"))
        return out

    # ------------------------------------------------------------------
    # 通道 3：RRF 混合
    # ------------------------------------------------------------------

    def search_hybrid(
        self,
        query: str,
        k: int = 5,
        *,
        pool: int = 30,
        rrf_k: int = RRF_K,
        weight_dense: float = 1.0,
        weight_sparse: float = 1.0,
    ) -> list[Hit]:
        """
        两路各取 pool 个候选，按 RRF 融合。

        权重是**可选的**额外杠杆（默认 1:1）—— 但 ADR-004 的立场是：
        先不用权重，RRF 直接融合往往就够；权重应该是"有数据支撑后再调"的东西。
        """
        dense = self.search_dense(query, k=pool)
        sparse = self.search_sparse(query, k=pool)

        fused: dict[int, float] = {}
        info: dict[int, Hit] = {}
        for hits, w in ((dense, weight_dense), (sparse, weight_sparse)):
            for h in hits:
                fused[h.chunk_id] = fused.get(h.chunk_id, 0.0) + w / (rrf_k + h.rank)
                info.setdefault(h.chunk_id, h)

        ranked = sorted(fused.items(), key=lambda x: -x[1])[:k]
        out: list[Hit] = []
        for rank, (cid, score) in enumerate(ranked, start=1):
            base = info[cid]
            out.append(Hit(chunk_id=cid, score=score,
                           source_path=base.source_path,
                           heading_path=base.heading_path, text=base.text,
                           rank=rank, channel="hybrid"))
        return out

    # ------------------------------------------------------------------
    # 统一切口
    # ------------------------------------------------------------------

    def search(self, query: str, k: int = 5, mode: str = "hybrid") -> list[Hit]:
        if mode == "dense":
            return self.search_dense(query, k)
        if mode == "sparse":
            return self.search_sparse(query, k)
        if mode == "hybrid":
            return self.search_hybrid(query, k)
        raise ValueError(f"未知检索模式 {mode!r}，可选 dense/sparse/hybrid")


# ======================================================================
# 暴力 vs numpy：这个对比是 ADR-003 里"体会边界"的具体化
# ======================================================================


def bench(modes: list[str], n_repeat: int = 20) -> None:  # pragma: no cover
    """留作性能对比的入口（P1 → P2 升级时用）。"""
    raise NotImplementedError("在 P2 阶段实现（对比暴力 / numpy 向量化 / sqlite-vec）")
