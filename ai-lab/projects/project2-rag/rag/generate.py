#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成层：带引用的回答 + 不足则弃答（ADR-006）

## 定位

到这里，检索层已经做完并且有据可查：

    dense     recall@5 0.4615   MRR 0.2833
    sparse    recall@5 0.9038   MRR 0.6478
    llm 重排  recall@5 0.9808   MRR 0.8638

所以**上下文里已经有正确答案了**（recall@5 = 0.98 意味着 98% 的问题
正确答案就在送给模型的那几段里）。这一步要解决的是另外两个问题：

  1. **回答必须可溯源** —— 每个论断要能指回具体是哪一段
  2. **不知道就要说不知道** —— 而不是拿模型的先验知识硬编一个

## 为什么这两件事比"答得漂亮"重要

第 2 点尤其关键：一个**从不弃答**的 RAG 在生产里是负资产。
它会把"文档里没有"的问题也答得头头是道，而用户无法分辨。
评估集里那 4 条不可答题就是为标定这一点准备的。

## 引用格式

给每段编号 `[1] [2] [3]`，要求模型在论断后标注来源：`…是 8 秒 [1]`。

为什么用**编号**而不是文件路径：
  · 编号短，模型不容易抄错（路径它经常改写、少一层目录）
  · 编号到来源的映射由**我们**掌握，可以机械校验
  · 路径让模型直接输出，会引入"幻觉路径"这种难查的错

## 弃答的判据

用**重排分数**（LLM 给每个候选打的相关性分，0-10）：
如果最高分低于阈值，说明检索到的东西都不太相关 → 弃答。

阈值不能拍脑袋：**必须用评估集标定**（见 eval/calibrate_abstain.py）。
这也是评估集必须包含不可答题的原因 —— 只有两类样本都有，
才能算"弃答准确率"和"误弃答率"这两个互相拉扯的指标。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .llm_rerank import LLMScorer, _load_dotenv  # noqa: F401  (顺带触发 .env 加载)
from .retrieve import Hit

# ======================================================================
# 提示词
# ======================================================================

# 设计要点（每条都对应一个具体的失败模式）：
#
#  1. **明确"只能依据材料"** —— 否则模型会混入先验知识。
#     这在 LLM 场景里特别明显：问项目一的测试数量，它可能答"通常 20-50 条"。
#
#  2. **明确"不足则弃答"，并给出固定的弃答短语** ——
#     固定短语才能机械判定。如果只说"可以拒答"，它会用各种措辞
#     （"文档未提及"/"没有相关信息"/"无法确定"），无法可靠识别。
#
#  3. **要求引用落在句末**、格式为 `[n]` —— 便于正则提取。
#     要求"每句话都标"会显得啰嗦，只要求"关键论断标"更容易执行。
#
#  4. **禁止编造编号** —— 这是最容易出的错：模型会引用 [7] 但材料只有 5 段。
#     必须由代码校验，不能只靠提示词。
SYSTEM_PROMPT = """你是一个知识库问答助手。你只能依据我提供的资料回答问题。

规则：
1. **只能使用资料里的信息**。不要使用你自己的知识补充，即使你知道答案。
2. 在关键论断后面标注来源编号，格式是 [1]、[2]。可以引用多个：[1][3]。
3. **如果资料不足以回答问题，必须回答**：资料中没有相关信息
   （就这七个字，不要加别的解释，也不要勉强作答）
4. 不要编造资料编号。资料里只有 [1] 到 [{n}] 这些编号。

回答要简洁、直接。用中文。"""

USER_TEMPLATE = """资料：

{passages}

问题：{question}

请依据上面的资料回答（不足则回答"资料中没有相关信息"）："""

# 弃答的判定短语 —— 与提示词里的第 3 条必须**完全一致**。
# 写成常量而不是散落在各处的字面量，避免两处不同步。
ABSTAIN_PHRASE = "资料中没有相关信息"


@dataclass
class GenerateConfig:
    """生成参数。默认值来自实测（见 ADR-006 定稿）。"""

    base_url: str = ""
    api_key: str = ""
    model: str = "deepseek-flash"
    max_tokens: int = 1200          # 推理模型：要给足思维链预算
    timeout: float = 180.0
    temperature: float = 0.0        # 要可复现
    max_passages: int = 5           # 送给模型几段（与 k 一致）
    max_chars: int = 600            # 每段截断长度（控制 prompt 大小）
    cache_path: str = ""
    #: 弃答阈值：重排最高分**低于**它就直接弃答，不调生成模型。
    #:
    #: ## ⚠️ 这个阈值只能当"省调用的快路径"，不能当弃答的主判据
    #:
    #: 实测（`eval/probe_abstain_signal.py`，零生成调用）：
    #:
    #:     可答题  top-1 分数：最小 3   中位 10  最大 10
    #:     不可答题 top-1 分数：最小 0   中位  1  最大  8
    #:
    #: **存在重叠区 [3, 8]** —— 任何阈值都会同时犯两类错：
    #:
    #:     阈值 3 → 误弃答 0.0%，但漏弃答 44.4%
    #:     阈值 6 → 误弃答 7.7%，漏弃答 11.1%
    #:     阈值 9 → 误弃答 15.4%，漏弃答 0.0%   ← 要拦全不可答题就得误拒 8 条可答题
    #:
    #: 根因：重排分数衡量的是"**这段和问题相不相关**"，
    #: 不是"**这段能不能回答问题**"。不可答题 q013 分数高达 8，
    #: 因为"rerank/向量检索"这个话题在本仓库里到处都是 ——
    #: 检索到的段落话题相关，但都不含答案。
    #:
    #: 所以默认取 3.0：只拦**明显无关**的（分数 0~2，实测全是真不可答），
    #: 换来 0% 误弃答 + 约 44% 的不可答题被这条快路径拦掉（省一次调用）。
    #: **其余一律交给生成模型判断** —— 提示词里已经要求它"不足则弃答"，
    #: 实测它对全部不可答题都正确弃答了。
    abstain_threshold: float = 3.0


@dataclass
class Answer:
    """一次生成的完整结果（带上可机械校验的元信息）。"""

    question: str
    text: str
    abstained: bool
    #: 本次送给模型的资料（编号 -> Hit），引用校验要用它
    passages: list[Hit] = field(default_factory=list)
    #: 从回答里提取出的引用编号（已去重、升序）
    cited: list[int] = field(default_factory=list)
    #: 非法引用：编号超出资料范围，或引用了不存在的段
    invalid_citations: list[int] = field(default_factory=list)
    #: 检索最高分（弃答判据）
    top_score: float = 0.0
    usage: dict = field(default_factory=dict)
    model: str = ""
    elapsed_s: float = 0.0

    @property
    def cited_sources(self) -> list[Hit]:
        return [self.passages[i - 1] for i in self.cited
                if 1 <= i <= len(self.passages)]

    @property
    def has_citation(self) -> bool:
        return bool(self.cited)


# ======================================================================
# 引用解析
# ======================================================================

# 匹配 [1]、[12]、[1][3] 里的每个编号。
# 用 findall 而不是只取第一个 —— 一句话引用多段是常见且合理的行为。
_CITE_RE = re.compile(r"\[(\d{1,2})\]")


def parse_citations(text: str) -> list[int]:
    """提取引用编号，去重并升序。"""
    nums = {int(m) for m in _CITE_RE.findall(text)}
    return sorted(nums)


def looks_like_abstain(text: str, cited: list[int] | None = None) -> bool:
    """
    判断回答是不是弃答。

    ## ⚠️ 这个判据改过一次，因为第一版有 bug

    第一版是纯子串包含：

        return ABSTAIN_PHRASE in text          # ❌ 太松

    后果：模型**正常作答**时如果末尾补一句"（某部分）资料中没有相关信息"，
    就会被算成弃答。实测抓到一条（q044：明明答了、还引用了 [1]）。

    这个 bug 的阴险之处是**同时污染两个指标、而且方向相反**：
      · 弃答准确率被**高估**（把正常回答算成弃答，显得很会拒答）
      · 端到端成功率被**低估**（明明答了却算没答）
    两个关键指标一起错，还互相掩盖，非常难在汇总数字里发现。

    ## 现在的判据

    **有引用 → 一定不是弃答。** 这个推论是可靠的：
    真弃答时没有任何论断需要溯源，所以不可能给出正确的引用。

    剩下的情况再用短语判定。这样"带引用的回答被误判成弃答"
    在逻辑上就不可能发生了。

    （实测影响：1 条误判，弃答率 0.1731 → 0.1538，端到端 0.8269 → 0.8462。）
    """
    if cited:
        return False
    return ABSTAIN_PHRASE in text


# ======================================================================
# 生成器
# ======================================================================


class Generator:
    """
    把检索结果变成带引用的回答。

    与 LLMScorer 共用 .env 加载与缓存约定，但**生成不缓存** ——
    因为生成的输出很长，而且我们希望对同一问题重跑能看到稳定性
    （缓存会让"稳定性"这个指标永远等于 100%，失去意义）。
    """

    def __init__(self, cfg: GenerateConfig | None = None) -> None:
        self.cfg = cfg or GenerateConfig()
        if not self.cfg.api_key:
            import os
            self.cfg.api_key = (os.environ.get("DEEPSEEK_API_KEY", "")
                                or os.environ.get("OPENAI_API_KEY", ""))
        if not self.cfg.base_url:
            import os
            self.cfg.base_url = os.environ.get(
                "OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        if not self.cfg.api_key:
            raise RuntimeError(
                "没有 API Key。放到 ai-lab/projects/project2-rag/.env 里的 "
                "DEEPSEEK_API_KEY。")
        self.calls = 0
        self.errors = 0

    @staticmethod
    def build_passages(hits: Sequence[Hit], max_chars: int) -> str:
        """
        把候选拼成带编号的资料块。

        编号从 1 开始（不是 0）—— 因为提示词和人的直觉都用 1 起。
        编号与 hits 的下标严格一一对应，这是引用校验的基础。
        """
        parts = []
        for i, h in enumerate(hits, 1):
            src = h.source_path
            head = f" › {h.heading_path}" if h.heading_path else ""
            parts.append(f"[{i}] （来源：{src}{head}）\n{h.text[:max_chars]}")
        return "\n\n".join(parts)

    def _pick(self, hits: Sequence[Hit]) -> list[Hit]:
        """取前 N 段送给模型（已按重排分降序）。"""
        return list(hits[: self.cfg.max_passages])

    def generate(self, question: str, hits: Sequence[Hit]) -> Answer:
        """同步生成。评估脚本是同步的，所以不暴露 async 接口。"""
        import asyncio
        return asyncio.run(self.agenerate(question, hits))

    async def agenerate(self, question: str, hits: Sequence[Hit]) -> Answer:
        import httpx

        picked = self._pick(hits)
        top_score = max((h.score for h in picked), default=0.0)

        # ---- 先判弃答（省一次 API 调用）----
        # 阈值 > 0 才启用。阈值本身要用评估集标定，默认 0 = 不基于分数弃答，
        # 完全交给模型判断"资料够不够"。
        if self.cfg.abstain_threshold > 0 and top_score < self.cfg.abstain_threshold:
            return Answer(
                question=question,
                text=ABSTAIN_PHRASE,
                abstained=True,
                passages=picked,
                top_score=top_score,
                model=self.cfg.model,
            )

        passages = self.build_passages(picked, self.cfg.max_chars)
        body = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system",
                 "content": SYSTEM_PROMPT.format(n=len(picked))},
                {"role": "user",
                 "content": USER_TEMPLATE.format(
                     passages=passages, question=question)},
            ],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }

        import time
        t0 = time.time()
        text, usage = "", {}
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.cfg.base_url.rstrip('/')}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                timeout=self.cfg.timeout)
            r.raise_for_status()
            d = r.json()
            text = (d["choices"][0]["message"].get("content") or "").strip()
            usage = d.get("usage", {})
        self.calls += 1

        cited = parse_citations(text)
        invalid = [c for c in cited if not (1 <= c <= len(picked))]
        valid_cited = [c for c in cited if 1 <= c <= len(picked)]

        return Answer(
            question=question,
            text=text,
            # 判据依赖"有引用"这个事实，所以要把合法引用先算出来再判
            abstained=looks_like_abstain(text, valid_cited),
            passages=picked,
            cited=valid_cited,
            invalid_citations=invalid,
            top_score=top_score,
            usage=usage,
            model=self.cfg.model,
            elapsed_s=round(time.time() - t0, 2),
        )
