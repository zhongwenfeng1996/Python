#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 重排 —— 用对话模型做真正的语义相关性判断（ADR-009）

## 为什么做这个：词法重排的败因已经被数据定位

ADR-007 的实测结论：**词法重排打不过纯 sparse**。
原因不是实现不好，而是它和稀疏检索**用的是同一类信息**（词的字面重叠），
没有新增信号 —— 所以天花板就是 sparse 的水平。

而 ADR-008 通过评估集审计进一步指出：加入 44% 改写问句后，
sparse 的 recall@5 从 1.0000 掉到 0.9038，**掉的全是"换了说法"的题**。
这正是词法方法的死穴，也正是语义方法的机会。

所以这一层的定位很清晰：**提供词法方法拿不到的那个信号。**

## 为什么现在能做（之前不做）

ADR-007 里写"神经网络 cross-encoder 需要 Key 或 torch + 模型，本地条件不具备"。
现在有了 DeepSeek 的 Key —— 虽然它**不提供 /embeddings 端点**
（实测 HTTP 404），但它提供 chat/completions，
而 chat 模型同样能做相关性判断。**LLM 重排就是 cross-encoder 的一种实现。**

## 设计：逐条打分 + 并发 + 缓存

打分方式选 **pointwise**（对每个候选单独问"这段和问题有多相关，0-10"），
而不是 listwise（一次给所有候选排序）：

  · pointwise 之间**相互独立** → 可以并发、可以缓存
  · listwise 一次改一个候选就要重算全部，且难以缓存
  · pointwise 的分数可跨查询复用（缓存命中率高）

代价是调用次数多（每查询 N 个候选 = N 次调用），所以必须配缓存，
否则评估跑一次就是上千次 API 调用。

## 诚实声明

  · LLM 判断也有噪声：同一段问两次可能给不同分。这会体现在指标的波动上
  · 它比稀疏检索慢得多、且要花钱 —— 是"精度换成本"
  · 并发数不能太高，否则会撞上服务端限流
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .retrieve import Hit

# ======================================================================
# 打分提示词
# ======================================================================

# ⚠️ 提示词与参数是**实测**定下来的，不是拍的（见 ADR-009）：
#
#   1. **必须批量打分，不能逐条**。逐条时 max_tokens 要 256+，
#      而每次调用约 180-350 token（全是 reasoning）。5 个候选 = 1105 token。
#      批量一次只要 796 token，**省 28% token、省 80% 调用**。
#      更关键的是：逐条时会出现"token 耗尽 → content 为空 → 解析失败"，
#      批量反而稳定（推理开销被摊到多个候选上）。
#
#   2. **max_tokens 必须给足**。两个模型（deepseek-flash / deepseek-v4-pro）
#      **都是推理模型**：先吐 reasoning_content，再吐 content。
#      给 max_tokens=4 时它会连思维链都没写完就截断，content 永远是空 ——
#      我一开始就栽在这里，还误判成"网络失败 35/36 次"。
#
#   3. **要 JSON 数组**，元素个数必须与候选数一致，方便机械校验。
SYSTEM_PROMPT = """你是一个检索相关性评分员。我会给你一个问题，以及若干个候选文档片段。
请为每个片段判断它对回答该问题的帮助程度。

评分标准：
0-2 分：完全无关，或者只是碰巧出现了相同的词
3-5 分：提到了相关概念，但没有回答问题所需的具体信息
6-8 分：包含回答问题所需的主要信息
9-10 分：直接、完整地回答了问题

只输出一个 JSON 数组，例如 [8, 2, 0, 5, 3]，元素个数必须与候选个数一致。
不要输出任何解释文字。"""

USER_TEMPLATE = """问题：{question}

{passages}

请为上面 {n} 个片段逐个打分，输出 {n} 个数字的 JSON 数组："""


def _load_dotenv() -> None:
    """
    把项目根目录的 .env 读进 os.environ（**不覆盖已有的环境变量**）。

    为什么放在模块里自动做：踩过好几次 ——
    换了新终端或新脚本就报"没有 API Key"，因为上一条命令里
    `$env:DEEPSEEK_API_KEY=...` 只对那个进程有效。
    每次手动 export 既烦又容易忘。

    优先用真实环境变量（CI 里就是靠它），.env 只是本地开发的兜底。
    """
    # rag/llm_rerank.py -> 上两级就是 project2-rag/
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v and k not in os.environ:
                os.environ[k] = v
    except Exception:  # noqa: BLE001
        # .env 读失败不该让程序崩 —— 后面会用"没有 Key"的明确错误提示
        pass


_load_dotenv()


@dataclass
class LLMRerankConfig:
    """
    参数默认值来自**实测的参数网格扫描**（`eval/probe_batch_limits.py`），不是拍的。
    扫描结果（deepseek-flash，真实语料块平均 395 字符）：

        批  截断  max_tok | 条数  prompt  推理   耗时
        10   400    1500  | ✅10    1682   132   1.1s   ← 默认
        10   800    1500  | ✅10    2075   593   3.3s
        15   400    2500  | ✅15    2700  2361  12.1s
        15   800    2500  | ❌截断  3248  2500  12.0s

    **推理 token 会剧烈波动**（同一格从 80 到 2361 都出现过），
    所以 max_tokens 不能贴着平均值给，必须留足余量；
    批次也不是越大越好 —— 15×800 那格把 2500 预算全烧在推理上，
    仍然拿不到答案。
    """

    base_url: str = ""
    api_key: str = ""
    model: str = "deepseek-flash"      # 便宜快；实测 v4-pro 慢一倍且没更好
    concurrency: int = 3               # 并发批次数（不是候选数）
    timeout: float = 180.0
    cache_path: str = ""               # 空 = 不缓存
    max_chars: int = 400               # 每个候选的截断长度（扫描出最省的）
    batch_size: int = 10               # 单批**最多**几条（不是固定几条）
    char_budget: int = 4500            # 单批的字符预算（近似 token 量）
    max_tokens: int = 1500             # **必须给足**：推理模型先写思维链
    max_retries: int = 4               # 失败重试次数（含首次）
    retry_backoff: float = 1.5         # 退避基数（秒），按 attempt 线性增长


class LLMScorer:
    """
    用 LLM 给「查询, 段落」对打相关性分（0~10）。

    必须配缓存：一次完整评估是 52 题 × 50 候选 = 2600 次调用。
    没有缓存的话，每改一行代码重跑一遍评估都要花掉这个量级的钱和时间。
    缓存键 = 模型 + 问题 + 段落哈希 —— 三者任一变化就重新算。
    """

    def __init__(self, cfg: LLMRerankConfig | None = None) -> None:
        self.cfg = cfg or LLMRerankConfig()
        if not self.cfg.api_key:
            self.cfg.api_key = os.environ.get("DEEPSEEK_API_KEY", "") or \
                os.environ.get("OPENAI_API_KEY", "")
        if not self.cfg.base_url:
            self.cfg.base_url = os.environ.get("OPENAI_BASE_URL",
                                               "https://api.deepseek.com/v1")
        if not self.cfg.api_key:
            raise RuntimeError(
                "没有 API Key。设置 DEEPSEEK_API_KEY 环境变量，"
                "或把 ai-lab/projects/project2-rag/.env 里的值读进环境。")

        self._cache: dict[str, float] = {}
        self._cache_hits = 0
        self._cache_miss = 0
        self._errors = 0
        self._last_errors: list[str] = []       # 最近几次失败的原文，便于定位
        self._unsaved = 0                       # 距上次落盘的调用数
        if self.cfg.cache_path:
            cp = Path(self.cfg.cache_path)
            if cp.exists():
                try:
                    self._cache = json.loads(cp.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    self._cache = {}
        self._dirty = False

    # ---------------- 缓存 ----------------

    @staticmethod
    def _key(model: str, question: str, passage: str) -> str:
        h = hashlib.sha256(f"{model}\x00{question}\x00{passage}".encode("utf-8"))
        return h.hexdigest()[:32]

    def save_cache(self) -> None:
        if not self.cfg.cache_path or not self._dirty:
            return
        Path(self.cfg.cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.cfg.cache_path).write_text(
            json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")

    @property
    def stats(self) -> dict:
        return {
            "cache_hit": self._cache_hits,
            "cache_miss": self._cache_miss,
            "errors": self._errors,
            "cached_total": len(self._cache),
            "last_errors": self._last_errors[-5:],
        }

    def maybe_save_cache(self, every: int = 50) -> bool:
        """
        每完成若干次调用就落一次盘。

        ## 为什么必须要"中途落盘"（踩过）

        第一版只在**整轮评估结束后**才存缓存。
        于是跑全量时：进程看了 20 多分钟，磁盘上的缓存文件**一直不更新**，
        看起来像"卡死了"，我就把它杀了 —— **结果前面花钱买到的打分全丢了**。

        实际它一直在正常工作，只是把结果攒在内存里。

        > 教训：**长任务必须有可观察的进度**。没有进度可看，
        > 人就会误判成故障；误判成故障就会去杀它，然后丢掉真实进度。
        """
        if not self.cfg.cache_path:
            return False
        if self._unsaved < every:
            return False
        self.save_cache()
        self._unsaved = 0
        return True

    # ---------------- 打分（批量） ----------------

    @staticmethod
    def _parse_scores(text: str, expected: int) -> list[float] | None:
        """
        从模型输出里抠出 JSON 数组。返回 None 表示**条数不符**（不可用）。

        为什么要校验条数：一次问 10 个候选，模型可能只答 8 个。
        那时如果"缺的补 0"，就会把**没打分的候选当成不相关**，
        静默污染排序。所以条数不符一律返回 None，由调用方决定怎么办。
        """
        m = re.search(r"\[[^\]]*\]", text)
        if not m:
            return None
        try:
            vals = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(vals, list) or len(vals) != expected:
            return None
        out: list[float] = []
        for v in vals:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            out.append(min(10.0, max(0.0, f)))
        return out

    async def _score_batch(self, client, question: str,
                           passages: Sequence[str]) -> list[float]:
        """
        一次给模型若干个候选，要它输出等长的分数数组。

        ## 为什么必须批量（实测数据，见 ADR-009）

        逐条打分时：5 个候选 = 5 次调用 = 1105 token，
        而且**出现了 token 耗尽返回 None 的情况**（推理没写完）。
        批量打分时：5 个候选 = 1 次调用 = 796 token，5 条全部解析成功。

        省 28% token、省 80% 调用，而且**更可靠** ——
        因为推理开销被摊薄到多个候选上。

        缓存：按 (模型, 问题, 单个段落) 存。所以**换批次划分不会失效**，
        重跑同一批题几乎不花钱。
        """
        # 先看哪些能命中缓存
        cached = [self._cache.get(self._key(self.cfg.model, question, p))
                  for p in passages]
        if all(c is not None for c in cached):
            self._cache_hits += len(passages)
            return [c for c in cached if c is not None]

        self._cache_miss += len(passages)
        listed = "\n\n".join(
            f"[{i}] {p[:self.cfg.max_chars]}"
            for i, p in enumerate(passages, 1))
        body = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(
                    question=question, passages=listed, n=len(passages))},
            ],
            "temperature": 0.0,
            # ⚠️ 必须给足 —— 两个模型都是推理模型，先写思维链再给答案。
            #    给小了（比如 4）会连思维链都写不完，content 永远是空。
            "max_tokens": self.cfg.max_tokens,
        }

        last_err = ""
        for attempt in range(self.cfg.max_retries):
            try:
                r = await client.post(
                    f"{self.cfg.base_url.rstrip('/')}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                    timeout=self.cfg.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}: {r.text[:160]}"
                    await asyncio.sleep(self.cfg.retry_backoff * (attempt + 1))
                    continue
                r.raise_for_status()
                content = r.json()["choices"][0]["message"].get("content") or ""
                scores = self._parse_scores(content, len(passages))
                if scores is None:
                    # 条数不符或不是数组 —— 值得重试（可能是被截断）
                    last_err = f"解析失败（期望 {len(passages)} 个数）: {content[:120]!r}"
                    await asyncio.sleep(self.cfg.retry_backoff * (attempt + 1))
                    continue
                for p, s in zip(passages, scores):
                    self._cache[self._key(self.cfg.model, question, p)] = s
                self._dirty = True
                self._unsaved += len(passages)
                self.maybe_save_cache()      # 中途落盘，防止长任务白跑
                return scores
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(self.cfg.retry_backoff * (attempt + 1))

        # 重试耗尽 —— **抛出**，不要伪造分数。
        #
        # ⚠️ 第一版这里是 "except: score = 0.0"，把失败静默当成"不相关"。
        #    后果：所有候选都得 0 分、排序完全没变，
        #    看起来像"LLM 重排无效"，其实是一次都没成功。
        #    **把失败伪装成数据，比直接崩掉更危险。**
        self._errors += 1
        self._last_errors.append(last_err)
        raise RuntimeError(
            f"LLM 批量打分连续 {self.cfg.max_retries} 次失败。\n"
            f"最后一次错误：{last_err}\n"
            f"（提示：并发 {self.cfg.concurrency} 可能触发限流，"
            f"或 max_tokens={self.cfg.max_tokens} 不足以让推理模型写完思维链）")

    async def score_many(self, question: str,
                         passages: Sequence[str]) -> list[float]:
        """
        把候选切批、并发打分，按原顺序拼回。

        ## 为什么分批要**按字符预算动态算**，而不是固定条数

        实测踩到：固定 batch=10 时，全量评估跑到某个查询会失败。
        原因是**不同查询的候选长度差异很大** ——
        有的查询召回的块都很短（10 个 × 300 字符 = 3000），
        有的召回的块都很长（10 个 × 800 字符 = 8000）。
        后者会让推理模型的思维链超预算被截断，content 为空。

        所以分批依据是**字符总量**（近似 token 量），不是条数。
        另外单批不超过 batch_size 条，避免条数过多时模型漏答。
        """
        import httpx

        n = len(passages)
        if n == 0:
            return []

        # 按字符预算切批
        batches: list[tuple[int, list[str]]] = []
        i = 0
        while i < n:
            chars = 0
            j = i
            while j < n and (j - i) < self.cfg.batch_size:
                # 加进去会不会超预算？超了就停（至少放一个，避免死循环）
                if chars + min(len(passages[j]), self.cfg.max_chars) > \
                        self.cfg.char_budget and j > i:
                    break
                chars += min(len(passages[j]), self.cfg.max_chars)
                j += 1
            batches.append((i, list(passages[i:j])))
            i = j

        sem = asyncio.Semaphore(max(1, self.cfg.concurrency))

        async with httpx.AsyncClient() as client:
            async def bounded(start: int,
                              chunk: list[str]) -> tuple[int, list[float]]:
                async with sem:
                    return start, await self._score_batch_with_split(
                        client, question, chunk)

            got = await asyncio.gather(*(bounded(s, c) for s, c in batches))

        result = [0.0] * n
        for start, scores in got:
            for k, s in enumerate(scores):
                result[start + k] = s
        return result

    async def _score_batch_with_split(self, client, question: str,
                                      chunk: list[str]) -> list[float]:
        """
        先按整批试；如果失败（多半是推理超预算被截断），**折半重试**。

        为什么要有这一层：全量评估跑到一半因为**一个病态批次**整体崩掉，
        代价是前面所有已完成的工作都白跑（虽然缓存能救回大部分）。

        折半重试把"一个坏批次"降级成"这一批慢一点"，
        而不是让整次评估失败。**对批处理任务，局部失败不该是全局失败。**
        """
        if len(chunk) <= 1:
            # 单个候选仍然失败 —— 这时真的没救了，让它抛出去（不伪造分数）
            return await self._score_batch(client, question, chunk)

        try:
            return await self._score_batch(client, question, chunk)
        except RuntimeError:
            mid = len(chunk) // 2
            left = await self._score_batch_with_split(client, question, chunk[:mid])
            right = await self._score_batch_with_split(client, question, chunk[mid:])
            return left + right


    def score_many_sync(self, question: str,
                        passages: Sequence[str]) -> list[float]:
        """
        同步包装 —— 评估脚本是同步的，直接调这个。

        为什么保留同步入口而不是让调用方自己 asyncio.run：
        计算指标的代码（recall/MRR 的循环）是纯同步逻辑，
        把它改成 async 会污染整条链路，而收益只是省一层包装。
        """
        return asyncio.run(self.score_many(question, passages))


class LLMReranker:
    """
    与 LexicalReranker 同接口的替换品（都有 rerank(query, hits, top_k)）。

    之所以保持同接口：**这样评估脚本可以原样复用**，
    只换一个类就能对比"词法重排 vs LLM 重排" —— 对比才有意义。
    """

    def __init__(self, scorer: LLMScorer | None = None) -> None:
        self.scorer = scorer or LLMScorer()

    def rerank(self, query: str, hits: Sequence[Hit], top_k: int) -> list[Hit]:
        if not hits:
            return []
        passages = [h.text for h in hits]
        scores = asyncio.run(self.scorer.score_many(query, passages))

        order = sorted(range(len(hits)), key=lambda i: -scores[i])
        out: list[Hit] = []
        for new_rank, i in enumerate(order[:top_k], start=1):
            h = hits[i]
            out.append(Hit(
                chunk_id=h.chunk_id,
                score=round(scores[i], 3),
                source_path=h.source_path,
                heading_path=h.heading_path,
                text=h.text,
                rank=new_rank,
                channel=f"llm({h.channel}@{h.rank})",
            ))
        return out
