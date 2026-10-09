#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 03 章示例 · 自定义异常：让错误"能分类型"

对应正文「1. 为什么要自己的异常类」「2. 一个真实的例子」。

基础篇 06 章讲了 try/except 和内建异常。这一章解决一个更实际的问题：
**"调用失败了"这句话信息量太低** —— 是网络问题？对方返回 4xx？
还是对方太慢？三种情况该做的事完全不同：

  - 网络失败    -> 可以重试
  - 401 / 403   -> key 配错了，重试一万次也没用，该直接报错
  - 首 token 超时 -> 应该换一个模型降级
  - 429 限流    -> 应该退避重试

如果全都抛 RuntimeError，调用方只能靠**解析错误字符串**来判断 —— 那是灾难。

运行：
    py -3 03_custom_exceptions.py
"""

from __future__ import annotations


# ----------------------------------------------------------------------
# 1) 三个自定义异常，对应三类该做不同处理的情况
# ----------------------------------------------------------------------
class UpstreamError(RuntimeError):
    """
    上游服务返回的错误。

    带 status 和 body 两个字段 —— 这是关键：
    调用方要能"看状态码决定怎么办"，而不是"看错误文本猜"。
    """

    def __init__(self, status: int | None, message: str, body: str = "") -> None:
        self.status = status
        self.body = body[:1000]
        detail = f"HTTP {status} — {message}" if status else message
        if self.body:
            detail += f" | 响应体: {self.body}"
        super().__init__(detail)


class FirstTokenTimeout(TimeoutError):
    """
    首 token 没在约定时间内到达 —— 这不是致命错误，是**降级信号**。

    继承 TimeoutError（内建）而不是 RuntimeError，因为语义上它就是超时；
    继承内建异常的好处是 `except TimeoutError` 也能抓到它。
    """


class BadRequest(ValueError):
    """请求本身有问题（参数错了）—— 重试没用，得改请求。"""


# ----------------------------------------------------------------------
# 2) 用异常类型决定"怎么办"，而不是解析字符串
# ----------------------------------------------------------------------
def decide_action(exc: Exception) -> str:
    """
    这就是自定义异常的全部意义：**调用方靠类型判断，不靠字符串。**

    对比一下如果只有一个 RuntimeError 会写成什么样：
        if "401" in str(exc) or "unauthorized" in str(exc).lower():
            ...  # 靠字符串匹配，改一次报错文案就崩
    """
    if isinstance(exc, BadRequest):
        return "改请求参数（重试无用）"
    if isinstance(exc, UpstreamError):
        if exc.status in (401, 403):
            return "检查 API Key 配置（重试无用）"
        if exc.status == 429:
            return "退避后重试"
        if exc.status and exc.status >= 500:
            return "换一个模型降级，或稍后重试"
        return "未知上游错误，记录后报给用户"
    if isinstance(exc, FirstTokenTimeout):
        return "换备用模型降级"
    if isinstance(exc, TimeoutError):
        return "总超时，降级或报错"
    return "未分类错误"


# ----------------------------------------------------------------------
# 3) 模拟几种真实失败，看分类结果
# ----------------------------------------------------------------------
def fake_call(kind: str) -> None:
    """模拟一次调用，按 kind 抛不同的异常。"""
    if kind == "no_key":
        raise UpstreamError(None, "模型 deepseek-chat 缺少 API Key（provider=deepseek）")
    if kind == "bad_key":
        raise UpstreamError(401, "Unauthorized",
                            '{"error":{"message":"Invalid API key"}}')
    if kind == "rate_limit":
        raise UpstreamError(429, "Too Many Requests", '{"error":"rate limit"}')
    if kind == "server_down":
        raise UpstreamError(500, "Internal Server Error", '<html>502 Bad Gateway</html>')
    if kind == "slow":
        raise FirstTokenTimeout("首 token 超过 8.0s 未到达")
    if kind == "bad_param":
        raise BadRequest("temperature 必须在 0~2 之间，收到 3.5")
    raise ValueError("别的什么错")


def demo() -> int:
    print("=" * 74)
    print("  第 03 章 · 自定义异常：让错误能分类型")
    print("=" * 74)

    print("\n—— 1) 自定义异常携带结构化信息 ——")
    try:
        fake_call("bad_key")
    except UpstreamError as exc:
        print(f"    exc.status = {exc.status!r}          ← 可以判断，不用解析字符串")
        print(f"    exc.body   = {exc.body!r}")
        print(f"    str(exc)   = {exc}")
        print("    ↑ 注意 body 被截断到 1000 字符：")
        print("      上游有时返回一大坨 HTML，全塞进日志会把有用的信息淹掉。")

    print("\n—— 2) 每种失败该怎么处理（靠 isinstance，不靠字符串）——")
    kinds = ["no_key", "bad_key", "rate_limit", "server_down",
             "slow", "bad_param", "other"]
    for kind in kinds:
        try:
            fake_call(kind)
        except Exception as exc:            # noqa: BLE001 - 演示就是全接住
            action = decide_action(exc)
            name = type(exc).__name__
            print(f"    {kind:<12} {name:<18} -> {action}")

    print("\n—— 3) 为什么不要把所有异常都包成 RuntimeError ——")
    print("    如果只有一个 RuntimeError，调用方只能这么写：")
    print("        if '401' in str(exc): ...        # 靠字符串")
    print("        if 'timed out' in str(exc): ...  # 改一次文案就崩")
    print("    而用异常类型，可以：")
    print("        except UpstreamError as e:")
    print("            if e.status in (401, 403): ...")
    print("    前者是「猜」，后者是「读一个字段」。")

    print("\n—— 4) 分层：什么时候该自己定义异常 ——")
    print("    ✅ 值得：调用方需要区分处理（可重试 / 不可重试 / 该降级）")
    print("    ✅ 值得：需要携带结构化信息（状态码、响应体、字段名）")
    print("    ❌ 不值得：只是为了换个名字，`class MyError(Exception): pass`")
    print("             却又从不按类型分别处理 —— 那和内建 Exception 没区别")

    print("\n" + "=" * 74)
    print("  结论：自定义异常的收益不在「定义」，而在「调用方能用类型分支」。")
    print("        项目一的 providers.py 就是这么做的 —— 见 UpstreamError /")
    print("        FirstTokenTimeout，前者决定「报错还是降级」，后者是纯降级信号。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(demo())
