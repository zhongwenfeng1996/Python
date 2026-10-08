#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置与模型注册表。

设计要点（这些取舍写在这里，因为面试 80% 的深挖都发生在这里）：

1. **为什么把模型定义写在代码里，而不是数据库/配置文件？**
   模型清单是"部署期"的配置，不是"运行期"的数据。它需要类型检查、需要和代码一起
   做 code review、需要能 diff。放 YAML 里会得到一个没有校验的字典，
   放数据库里会让本地开发多一个依赖。等真需要多租户自定义模型时再抽出来。

2. **为什么用"provider + model"两层，而不是拉平成一个模型名？**
   因为 API Key、base_url、超时策略都是按 provider 走的。拉平之后每个模型都要
   重复一遍 base_url，改一个网关地址要改 N 处。

3. **为什么价格写成常量而不是查表接口？**
   价格会变，但"估算成本"这个功能的价值在于**相对比较**（哪个模型贵 10 倍），
   不在于绝对精确。写常量 + 注释提醒核对，是这个阶段正确的精度。
   真实产品的做法是定期同步价格表，并把实际账单作为校准依据。

环境变量约定（按 provider 取 key，缺失则该 provider 的模型标记为不可用）：

    DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY
    OPENAI_BASE_URL      —— 统一覆盖（本地 mock 用）
    CHAT_TIMEOUT_S       —— 单次请求总超时（默认 60）
    FIRST_TOKEN_TIMEOUT_S —— 首 token 超时，超过就降级（默认 8）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# ----------------------------------------------------------------------
# .env 加载（极简实现，不引第三方依赖）
# ----------------------------------------------------------------------
def load_env_file(path: Path) -> dict[str, str]:
    """解析 KEY=VALUE 形式的 .env；忽略注释与空行，去掉两侧引号。"""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


_ENV_FILE = load_env_file(Path(__file__).resolve().parent / ".env")


def env(key: str, default: str = "") -> str:
    """优先级：真实环境变量 > .env 文件 > 默认值。"""
    return os.environ.get(key) or _ENV_FILE.get(key) or default


def env_float(key: str, default: float) -> float:
    raw = env(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ----------------------------------------------------------------------
# 模型注册表
# ----------------------------------------------------------------------
# 每 100 万 token 的价格（美元，输入 / 输出）。**用前请去官网核对**，价格会变。
@dataclass(frozen=True)
class ModelSpec:
    id: str                 # 对外暴露的模型 id
    provider: str           # 供应商标识，决定用哪个 API Key
    model: str              # 供应商侧的真实模型名
    label: str              # 给前端显示的中文名
    base_url: str           # OpenAI 兼容端点
    price_in: float         # 美元 / 百万输入 token
    price_out: float        # 美元 / 百万输出 token
    context: int            # 上下文窗口（token），用于前端提示
    supports_usage: bool = True   # 是否支持 stream_options.include_usage
    tags: tuple[str, ...] = field(default_factory=tuple)


# 顺序即前端下拉框的顺序。第一个是默认模型。
MODEL_REGISTRY: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="deepseek-chat",
        provider="deepseek",
        model="deepseek-chat",
        label="DeepSeek Chat（国产 · 便宜）",
        base_url="https://api.deepseek.com/v1",
        price_in=0.27, price_out=1.10, context=64_000,
        tags=("开源系", "便宜"),
    ),
    ModelSpec(
        id="qwen-plus",
        provider="dashscope",
        model="qwen-plus",
        label="通义千问 Plus（国产 · 长上下文）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        price_in=0.40, price_out=1.20, context=128_000,
        tags=("开源系", "长上下文"),
    ),
    ModelSpec(
        id="gpt-4o-mini",
        provider="openai",
        model="gpt-4o-mini",
        label="GPT-4o mini（闭源 · 稳定）",
        base_url="https://api.openai.com/v1",
        price_in=0.15, price_out=0.60, context=128_000,
        tags=("闭源",),
    ),
    ModelSpec(
        id="glm-4-flash",
        provider="zhipu",
        model="glm-4-flash",
        label="智谱 GLM-4-Flash（国产 · 免费额度）",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        price_in=0.0, price_out=0.0, context=128_000,
        tags=("开源系", "免费额度"),
    ),
)

MODELS_BY_ID: dict[str, ModelSpec] = {m.id: m for m in MODEL_REGISTRY}


@dataclass(frozen=True)
class Settings:
    api_keys: dict[str, str]        # provider -> key（只包含已配置的）
    base_url_override: str          # 非空时覆盖所有模型的 base_url（本地 mock 用）
    chat_timeout_s: float
    first_token_timeout_s: float

    def key_for(self, spec: ModelSpec) -> str:
        return self.api_keys.get(spec.provider, "")

    def base_url_for(self, spec: ModelSpec) -> str:
        return self.base_url_override or spec.base_url


def load_settings() -> Settings:
    keys: dict[str, str] = {}
    for provider, var in (
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("dashscope", "DASHSCOPE_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("zhipu", "ZHIPU_API_KEY"),
    ):
        value = env(var)
        if value:
            keys[provider] = value

    return Settings(
        api_keys=keys,
        base_url_override=env("OPENAI_BASE_URL"),
        chat_timeout_s=env_float("CHAT_TIMEOUT_S", 60.0),
        first_token_timeout_s=env_float("FIRST_TOKEN_TIMEOUT_S", 8.0),
    )


def usable_models(settings: Settings) -> list[ModelSpec]:
    """返回当前配置下真正可用的模型（本地 mock 模式下全部视为可用）。"""
    if settings.base_url_override:
        return list(MODEL_REGISTRY)
    return [m for m in MODEL_REGISTRY if settings.key_for(m)]
