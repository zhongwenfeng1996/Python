#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Embedding 接口与两种实现（见 ADR-002）

## 为什么要有接口

检索质量主要由 embedding 决定，而当前机器**没有任何 API Key**。
如果代码直接写死"调用某个 API"，那这个项目今天就没法开始，
更糟的是**评估做不了** —— 而评估才是这个项目的核心价值。

所以：定义接口，两种实现可切换。
  - LocalHashEmbedder  默认。确定性、零成本、零网络。开发/测试/CI/离线评估用。
  - APIEmbedder        有 Key 时用。真实语义，最终基线。

## 本地实现为什么不是"凑数"

常见的糊弄做法是"返回随机向量"（不可复现）或"直接用词频"（不适合语义检索）。
这里用**字符 n-gram 特征哈希**：

  1. 把文本切成 2~3 字符的滑窗（字符级 → 中文不需要分词）
  2. 每个 n-gram 用**稳定哈希**（md5，不用内置 hash —— 它带随机盐，跨进程不一致！）
     映射到固定维度的某一维
  3. 按 TF 加权累加，最后 L2 归一化

得到的向量有**局部性**：字符重叠越多，向量越接近。这正是"字面相似"。

## 必须写清楚的短板

它**语义泛化弱** —— 同义词、改写问句的召回会明显差于真实 embedding。
这不是 bug，是特性：它给出一个**下限基线**。等接入真实模型，
recall 的提升幅度就是可以写进简历的量化收益。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

# ======================================================================
# 接口
# ======================================================================


class Embedder(ABC):
    """
    所有 embedding 实现的统一接口。

    `name` 和 `dim` 必须参与索引的标识 —— **换 embedder 必须重建索引**，
    因为向量空间完全不同，混用会得到毫无意义的相似度。
    """

    #: 实现名，会写进索引元数据
    name: str = "abstract"
    #: 向量维度
    dim: int = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """把一批文本编成向量。批量接口（不是单条）—— API 实现要批量才快。"""

    def embed_one(self, text: str) -> list[float]:
        """单条便捷方法。"""
        return self.embed([text])[0]

    @property
    def signature(self) -> str:
        """唯一标识这次 embedding 的"配方"，用来判断索引能不能复用。"""
        return f"{self.name}:dim={self.dim}"


# ======================================================================
# 实现 A：本地确定性 embedding
# ======================================================================


def _ngrams(text: str, n_min: int = 2, n_max: int = 3) -> list[str]:
    """
    提取字符级 n-gram。

    为什么是**字符**而不是词：
      中文没有空格分隔，按词切需要分词库（本机没有 jieba 的 wheel）。
      字符级 n-gram 对中文、英文、代码混排都成立，且零依赖。
    """
    # 先归一化：小写、把连续空白压成一个空格。
    # 这样 "Chapter  1" 和 "chapter 1" 得到相同的 n-gram。
    s = re.sub(r"\s+", " ", text.lower()).strip()
    if not s:
        return []
    out: list[str] = []
    for n in range(n_min, n_max + 1):
        if len(s) < n:
            continue
        out.extend(s[i:i + n] for i in range(len(s) - n + 1))
    return out


def _stable_bucket(token: str, dim: int) -> int:
    """
    把 token 稳定地映射到 [0, dim)。

    ⚠️ **不能用内置 `hash()`**：Python 的字符串 hash 带每进程随机盐
    （PYTHONHASHSEED），同一个词在不同进程/不同次运行会落到不同桶 ——
    那样"同一输入永远同向量"就不成立，评估结果不可复现。
    用 md5 取前 8 字节，跨进程稳定。
    """
    h = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") % dim


class LocalHashEmbedder(Embedder):
    """
    本地确定性 embedder（字符 n-gram + 特征哈希 + TF 加权 + L2 归一化）。

    参数 `dim` 的选择：
      - 太小 → 不同 n-gram 大量撞桶，向量区分度差
      - 太大 → 稀疏、占空间，且没有额外信息
      256 维在"几百到几千个块"的规模下够用。这是**可调参数**，
      可以用评估集对比不同 dim 的效果（ADR-005 说的"参数有依据"）。
    """

    name = "local-hash-ngram"

    def __init__(self, dim: int = 256, n_min: int = 2, n_max: int = 3) -> None:
        self.dim = dim
        self.n_min = n_min
        self.n_max = n_max

    @property
    def signature(self) -> str:
        return f"{self.name}:dim={self.dim}:n={self.n_min}-{self.n_max}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        grams = _ngrams(text, self.n_min, self.n_max)
        if not grams:
            return vec
        # TF 加权：出现次数越多权重越大，但用 sqrt 抑制高频 n-gram 的统治力
        # （不做 IDF 是刻意的：IDF 需要全语料统计，会让"单条文本的向量"
        #   依赖语料状态，破坏"同一输入永远同向量"的确定性）
        counts: dict[str, int] = {}
        for g in grams:
            counts[g] = counts.get(g, 0) + 1
        for g, c in counts.items():
            vec[_stable_bucket(g, self.dim)] += math.sqrt(c)

        # L2 归一化 → 之后余弦相似度就是点积，省一次除法
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


# ======================================================================
# 实现 B：真实 embedding API（OpenAI 兼容）
# ======================================================================

# 默认配置。切换 endpoint 即可换成 bge-m3 / 通义 / 智谱等
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
DEFAULT_EMBED_BASE = "https://api.openai.com/v1"


class EmbeddingError(RuntimeError):
    """embedding 调用失败。单独定义是为了调用方能区分"网络问题"和"参数问题"。"""


class APIEmbedder(Embedder):
    """
    走 OpenAI 兼容的 /embeddings 端点。

    只要能配 `OPENAI_BASE_URL` + `OPENAI_API_KEY` 就能用，
    所以同样适配 bge-m3 的自建服务、通义、智谱等。

    用标准库 urllib 而不是 httpx：**这个文件不想引入额外依赖**，
    让"本地实现"真的做到零依赖。真实项目里用 httpx 更合适（见项目一）。
    """

    name = "api-openai-compatible"

    def __init__(
        self,
        model: str = DEFAULT_EMBED_MODEL,
        base_url: str = "",
        api_key: str = "",
        dim: int = 0,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or DEFAULT_EMBED_BASE).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.timeout = timeout
        # 真实维度要等第一次调用才知道；0 表示"未确定"
        self.dim = dim
        if not self.api_key:
            raise EmbeddingError(
                "没有 API Key。设置 OPENAI_API_KEY 环境变量，或改用 LocalHashEmbedder。"
            )

    @property
    def signature(self) -> str:
        return f"{self.name}:model={self.model}:dim={self.dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise EmbeddingError(f"HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise EmbeddingError(f"网络失败：{exc}") from exc

        # 按 index 排序，保证返回顺序与输入一致（API 不保证顺序）
        items = sorted(body["data"], key=lambda d: d["index"])
        vectors = [it["embedding"] for it in items]
        if vectors:
            self.dim = len(vectors[0])
        return vectors


# ======================================================================
# 工厂：按环境自动选
# ======================================================================


def get_embedder(prefer: str = "auto", dim: int = 256) -> Embedder:
    """
    选一个 embedder。

    prefer:
      "auto"  有 Key 且没显式禁用 → APIEmbedder，否则 LocalHashEmbedder
      "local" 强制本地（测试和 CI 必须用它 —— 不能让 CI 依赖网络和 Key）
      "api"   强制 API（没有 Key 会抛 EmbeddingError，早失败早发现）
    """
    if prefer == "local":
        return LocalHashEmbedder(dim=dim)

    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    if prefer == "api":
        return APIEmbedder()
    # auto
    if has_key and os.environ.get("RAG_USE_API_EMBEDDING", "1") != "0":
        try:
            return APIEmbedder()
        except EmbeddingError:
            return LocalHashEmbedder(dim=dim)
    return LocalHashEmbedder(dim=dim)
