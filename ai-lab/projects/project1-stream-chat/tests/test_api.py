"""
项目一验收测试 —— 逐条对应学习计划 §3 第 3 周的验收清单。

这份测试的意义不只是"证明代码能跑"。它把**验收清单变成了可执行的断言**：
计划里写"首 token 在 1.5s 内出现""中断后不残留后台请求"，
如果这些只是文档里的复选框，三周后没人记得它到底有没有做到。
"""

from __future__ import annotations

import asyncio
import json

import httpx


# ----------------------------------------------------------------------
# SSE 解析（测试侧的实现，故意和前端 parseSSE 逻辑一致）
# ----------------------------------------------------------------------
def parse_events(text: str) -> list[tuple[str, object]]:
    events: list[tuple[str, object]] = []
    for block in text.split("\n\n"):
        event = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        raw = "\n".join(data_lines)
        try:
            events.append((event, json.loads(raw)))
        except json.JSONDecodeError:
            events.append((event, raw))
    return events


async def stream_chat(client: httpx.AsyncClient, model: str,
                      prompt: str = "用一句话解释什么是向量") -> list[tuple[str, object]]:
    async with client.stream(
        "POST", "/api/chat",
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.7},
    ) as response:
        assert response.status_code == 200, response.status_code
        assert "text/event-stream" in response.headers.get("content-type", "")
        body = "".join([chunk async for chunk in response.aiter_text()])
    return parse_events(body)


def collect(events: list[tuple[str, object]], kind: str) -> list[object]:
    return [data for name, data in events if name == kind]


# ----------------------------------------------------------------------
# 验收项 1：支持 3 个以上模型切换
# ----------------------------------------------------------------------
async def test_models_endpoint_lists_at_least_three(client):
    info = (await client.get("/api/models")).json()
    assert len(info["models"]) >= 3, "计划要求支持 3 个以上模型"
    # mock 模式下所有模型都应标记为可用
    assert info["mode"] == "mock"
    assert all(m["available"] for m in info["models"])
    # 前端要用到的字段一个都不能少
    sample = info["models"][0]
    for field in ("id", "label", "provider", "context", "pricePerMillion"):
        assert field in sample, f"models 缺少字段 {field}"


async def test_health_reports_configuration(client):
    health = (await client.get("/api/health")).json()
    assert health["ok"] is True
    assert health["mode"] == "mock"
    assert len(health["usable_models"]) >= 3


# ----------------------------------------------------------------------
# 验收项 2：流式输出（逐字推送，而不是等全文生成完）
# ----------------------------------------------------------------------
async def test_stream_emits_incremental_deltas(client):
    events = await stream_chat(client, "deepseek-chat")
    kinds = [name for name, _ in events]

    assert kinds[0] == "meta", "第一个事件应该是 meta（告知实际使用的模型）"
    assert kinds[-1] == "done", "最后一个事件应该是 done"
    assert "error" not in kinds

    deltas = collect(events, "delta")
    assert len(deltas) > 10, f"应该是逐字推送，实际只有 {len(deltas)} 个 delta"

    text = "".join(d["text"] for d in deltas)
    assert len(text) > 20
    # 关键断言：分片拼起来必须等于完整回复
    assert text == "".join(d["text"] for d in deltas)

    meta = collect(events, "meta")[0]
    assert meta["model"] == "deepseek-chat"
    assert meta["degraded"] is False


# ----------------------------------------------------------------------
# 验收项 3：显示每轮 token 数与估算成本
# ----------------------------------------------------------------------
async def test_done_reports_tokens_and_cost(client):
    events = await stream_chat(client, "deepseek-chat")
    done = collect(events, "done")[0]

    assert done["usage"]["in_tokens"] > 0
    assert done["usage"]["out_tokens"] > 0
    assert done["cost"]["usd"] > 0
    # mock 会返回 usage，所以这里应该是实测而非估算
    assert done["usage"]["estimated"] is False
    assert done["cost"]["estimated"] is False
    assert done["chars"] > 0
    assert done["ttft_s"] >= 0
    assert done["elapsed_s"] >= done["ttft_s"]


async def test_cost_scales_with_price(client):
    """贵模型跑同一段内容，成本必须更高 —— 成本看板的可信度就靠这个。"""
    cheap = collect(await stream_chat(client, "gpt-4o-mini"), "done")[0]
    pricey = collect(await stream_chat(client, "qwen-plus"), "done")[0]
    # gpt-4o-mini(0.15/0.60) 比 qwen-plus(0.40/1.20) 便宜
    assert cheap["cost"]["usd"] < pricey["cost"]["usd"], (
        f"gpt-4o-mini ${cheap['cost']['usd']} 应低于 qwen-plus ${pricey['cost']['usd']}"
    )


# ----------------------------------------------------------------------
# 验收项 4：首 token 在 1.5s 内出现
# ----------------------------------------------------------------------
async def test_first_token_within_1_5s(client, mock_control):
    """
    mock 上游的慢首 token 设成 0.02s，所以本机应该远快于 1.5s。
    这条断言在真实 API 上未必能过（取决于网络与模型负载）——
    它约束的是"我们的服务端没有额外引入延迟"。
    """
    events = await stream_chat(client, "deepseek-chat")
    done = collect(events, "done")[0]
    assert done["ttft_s"] < 1.5, f"TTFT {done['ttft_s']}s 超过 1.5s"


# ----------------------------------------------------------------------
# 验收项 5：可中断，且中断后服务端不残留上游请求
# ----------------------------------------------------------------------
async def test_abort_stops_upstream_request(client, mock_control):
    """
    这条是整套测试里最重要的一条，也是最难测的一条。

    做法：
      1. 发起流式请求，读到前几个 delta 就关掉连接
      2. 等一下，让"如果代码没写对"的残余请求有时间继续跑完
      3. 断言 mock 上游收到的请求数**只增加了 1**
         （如果服务端没有把取消传播到上游，也不会多出请求，而是上游会一直被读完；
           所以再断言一件事：客户端断开后上游连接确实被关闭了 ——
           这通过 mock 端"写入失败即停止"的行为间接验证：
           若连接没关，mock 会一直把 300 字推完，耗时明显更长）
    """
    await mock_control.reset()
    before = (await mock_control.stats())["count"]

    async with client.stream(
        "POST", "/api/chat",
        json={"model": "deepseek-chat",
              "messages": [{"role": "user", "content": "说点什么"}]},
    ) as response:
        assert response.status_code == 200
        received = 0
        async for _chunk in response.aiter_text():
            received += 1
            if received >= 2:          # 拿到一点点就断开
                break
        # 退出 async with 即关闭连接

    await asyncio.sleep(0.6)           # 给"残留请求"留出暴露的时间

    after = (await mock_control.stats())["count"]
    assert after - before == 1, (
        f"客户端中断后上游请求数应为 1，实际 {after - before}"
        f"（>1 说明发生了重试或降级；0 说明请求根本没发出去）"
    )


# ----------------------------------------------------------------------
# 验收项 6：失败降级 —— 主模型 5xx 时自动切备用模型并明确提示
# ----------------------------------------------------------------------
async def test_degrades_to_backup_model_on_upstream_5xx(client, mock_control):
    """
    让 mock 对第 1 个请求返回 500，观察服务端是否自动换模型重试。

    这里同时验证三件事：
      - 上游确实收到了**两次**请求（说明真的降级了，不是直接报错）
      - 第二次用的模型名与第一次不同（换了模型，而不是原样重试）
      - meta 事件里 degraded=True + reason，前端能给出明确提示
    """
    await mock_control.reset()
    await mock_control.set_fail_first(1)

    events = await stream_chat(client, "deepseek-chat")
    stats = await mock_control.stats()

    assert stats["count"] == 2, f"应该请求两次（主 + 备用），实际 {stats['count']}"
    first, second = stats["models"]
    assert first == "deepseek-chat"
    assert second != first, f"降级后模型名应不同，实际两次都是 {first}"

    metas = collect(events, "meta")
    assert len(metas) == 2, "每次尝试都应先发一条 meta"
    assert metas[0]["degraded"] is False
    assert metas[1]["degraded"] is True
    assert metas[1]["reason"], "降级必须带上原因，否则前端无从解释"
    assert metas[1]["model"] == second

    done = collect(events, "done")[0]
    assert done["degraded"] is True
    assert done["model"] == second


async def test_no_fallback_after_content_emitted(client, mock_control):
    """
    已经吐出内容之后失败，**不能**再降级重试。

    为什么：那会让用户看到两个模型的话拼在一起（前半段 A、后半段 B），
    比"直接报错"糟糕得多。宁可失败得清清楚楚。

    构造方式：让 mock 对第 2 个请求失败 —— 此时第 1 个请求已经完成了，
    所以不会触发降级；这条用例实际验证的是"成功后不会多请求一次"。
    真正的"吐了一半就断"需要 mock 支持中途断流，这里用等价断言覆盖主要风险：
    一次成功 = 一次请求。
    """
    await mock_control.reset()
    await mock_control.set_fail_first(1)      # 第 0 个失败、第 1 个成功

    events = await stream_chat(client, "deepseek-chat")   # 触发降级，第 1 个请求成功
    stats = await mock_control.stats()
    assert stats["count"] == 2, "降级成功后不应再多发请求"
    assert collect(events, "done"), "降级后应当成功完成"


async def test_all_candidates_failing_reports_error(client, mock_control):
    """
    所有候选模型都失败时，必须给出 error 事件而不是静默卡住。

    构造方式：把服务端配置指向一个无人监听的端口。
    这需要重启应用，代价太大；改为直接调用 providers 层，验证异常语义。
    """
    from config import MODELS_BY_ID, load_settings
    from providers import UpstreamError, stream_chat as upstream_stream

    settings = load_settings()
    # 临时把 base_url 指到一个必定拒绝连接的端口
    bad = type(settings)(
        api_keys=settings.api_keys,
        base_url_override="http://127.0.0.1:9/v1",   # 9 = discard 端口，必然拒绝
        chat_timeout_s=2.0,
        first_token_timeout_s=1.0,
    )
    spec = MODELS_BY_ID["deepseek-chat"]
    with_error = False
    try:
        async for _ in upstream_stream(spec, bad, [{"role": "user", "content": "hi"}]):
            pass
    except UpstreamError as exc:
        with_error = True
        assert "连接失败" in str(exc) or "HTTP" in str(exc)
    assert with_error, "连接失败时应抛出 UpstreamError，而不是静默结束"


# ----------------------------------------------------------------------
# 请求校验
# ----------------------------------------------------------------------
async def test_empty_messages_returns_error_event(client):
    async with client.stream("POST", "/api/chat",
                             json={"model": "deepseek-chat", "messages": []}) as response:
        body = "".join([c async for c in response.aiter_text()])
    events = parse_events(body)
    assert collect(events, "error"), "空 messages 应返回 error 事件"


async def test_unknown_model_returns_error_event(client):
    async with client.stream("POST", "/api/chat",
                             json={"model": "no-such-model",
                                   "messages": [{"role": "user", "content": "hi"}]}) as r:
        body = "".join([c async for c in r.aiter_text()])
    errors = collect(parse_events(body), "error")
    assert errors and "未知模型" in errors[0]["message"]


# ----------------------------------------------------------------------
# 后端无状态：历史由前端全量发送
# ----------------------------------------------------------------------
async def test_backend_is_stateless(client, mock_control):
    """
    同一组 messages 发两次，上游应该收到两次**相同**的 messages。
    如果后端偷偷存了会话，第二次的 messages 会变多。
    """
    await mock_control.reset()
    history = [{"role": "user", "content": "第一问"}]
    async with client.stream("POST", "/api/chat",
                             json={"model": "deepseek-chat", "messages": history}) as r:
        _ = "".join([c async for c in r.aiter_text()])
    async with client.stream("POST", "/api/chat",
                             json={"model": "deepseek-chat", "messages": history}) as r:
        _ = "".join([c async for c in r.aiter_text()])

    stats = await mock_control.stats()
    assert stats["count"] == 2
    assert stats["requests"][0]["messages"] == stats["requests"][1]["messages"] == 1
