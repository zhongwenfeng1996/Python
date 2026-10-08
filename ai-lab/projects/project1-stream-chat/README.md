# 项目一 · 流式多模型对话应用

> 学习计划 §3 第 3 周交付物 · 状态：**已可运行，13/13 验收测试通过**

一个可以真正用的多模型流式对话应用：**手写 SSE 解析**、可中断、首 token 超时自动降级、
每轮显示 token 与成本。前端 Vue 3，后端 FastAPI + httpx。

```
浏览器 (Vue 3 · 手写 SSE 解析)
   │  POST /api/chat   { model, messages, temperature }
   │  ← text/event-stream: meta / delta / usage / done / error
   ▼
FastAPI 后端 (无状态)
   │  首 token 超时 8s → 自动换模型重试（上限 3 个）
   │  ← 上游 SSE
   ▼
模型服务（DeepSeek / 通义 / OpenAI / 智谱，统一 OpenAI 兼容协议）
```

---

## 验收清单（对应计划 §3 第 3 周）

| 验收项 | 实现位置 | 测试 |
|---|---|---|
| 支持 3 个以上模型切换，统一 OpenAI 兼容接口 | `backend/config.py` 的 `MODEL_REGISTRY`（4 个模型、4 家 provider） | `test_models_endpoint_lists_at_least_three` |
| 流式输出（逐字，非整段） | `backend/providers.py::_stream_once` + SSE 编码 | `test_stream_emits_incremental_deltas` |
| 可中断，且中断后服务端不残留上游请求 | `backend/main.py::_relay` 的 `CancelledError` 处理 + `providers.py` 的 `finally: await response.aclose()` | `test_abort_stops_upstream_request` |
| 重新生成 / 编辑后重跑 | `frontend/index.html::regenerate`（后端无状态，天然支持） | `test_backend_is_stateless` |
| 显示每轮 token 数与估算成本 | `backend/cost.py` + `done` 事件 | `test_done_reports_tokens_and_cost`、`test_cost_scales_with_price` |
| 首 token 8s 超时自动切备用模型并明确提示 | `backend/main.py::_relay` 的降级循环 + `meta.degraded/reason` | `test_degrades_to_backup_model_on_upstream_5xx` |
| 首 token 在 1.5s 内出现 | 服务端不引入额外延迟 | `test_first_token_within_1_5s` |
| 前端手写 `fetch` + `ReadableStream`（不用 AI SDK） | `frontend/index.html::parseSSE` | 人工验证（见下文"已知未验证项"） |

### 实测指标（本机 · 本地 mock 上游 · `tools/verify_e2e.py` 输出）

| 指标 | 数值 |
|---|---|
| 首 token 延迟 (TTFT) | **83 ms**（服务端记录） / 84 ms（客户端观测） |
| 单轮端到端 | **496 ms** |
| 流式分片数 | 38 个 delta（逐字推送，非整段） |
| token 与成本 | in 11 / out 38，**$0.000045**（实测 usage，非估算） |
| 测试套件 | **13 passed in 5.1s** |
| 首页托管 | 200，14,290 字节 |

> 这组数字是"**服务端没有引入额外延迟**"的证据（上游 mock 的慢首 token 设为 20ms），
> 不是真实模型的性能。真实 TTFT 取决于 provider，国内模型通常 0.5–1.5s。
> 跑真实模型时请用同一个脚本重新记录并替换本表 —— **指标要能随时重跑出来**。

复现这组数字：

```powershell
G:\转型\.venv\Scripts\python tools\verify_e2e.py --app-port 8802 --mock-port 8803
```

> ⚠️ 端口要挑空闲的。本机上 8901 曾被一个托盘程序（`douyin_tray`）占用，
> 表现为一个毫无头绪的 `WinError 10054` —— 脚本里 `wait_port` 现在会同时
> 检查"我们的进程还活着"，就是为了第一时间暴露这种问题。

---

## 快速开始

### 方式 A · 离线运行（不需要任何 API Key，推荐先这样）

```powershell
# 0. 建虚拟环境并装依赖（国内建议加镜像）
py -3 -m venv G:\转型\.venv
G:\转型\.venv\Scripts\python -m pip install `
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple `
  fastapi "uvicorn[standard]" httpx pytest pytest-asyncio

# 1. 终端 1：起本地 mock 上游（假装是模型服务）
py -3 G:\转型\ai-lab\week01\mock_server.py --delay 0.01

# 2. 终端 2：起后端
Set-Location G:\转型\ai-lab\projects\project1-stream-chat\backend
$env:OPENAI_BASE_URL = "http://127.0.0.1:8765/v1"
$env:OPENAI_API_KEY  = "test"
G:\转型\.venv\Scripts\python -m uvicorn main:app --port 8000
```

打开 <http://127.0.0.1:8000/> —— 页面顶部会显示黄标 **"本地 mock（不花钱）"**。

> 也有一键脚本：`.\run.ps1`（见 `run.ps1 -Help`）。

### 方式 B · 真实调用

```powershell
Set-Location G:\转型\ai-lab\projects\project1-stream-chat\backend
Copy-Item .env.example .env     # 填入 DEEPSEEK_API_KEY 等（至少一个）
G:\转型\.venv\Scripts\python -m uvicorn main:app --port 8000
```

没有配置 Key 的模型在下拉框里会显示"（未配置 Key）"并不可选 —— 这是刻意的：
**不要给用户一个点了会报错的选项**。

### 跑测试

```powershell
Set-Location G:\转型\ai-lab\projects\project1-stream-chat
G:\转型\.venv\Scripts\python -m pytest
```

测试会自己起 mock 上游和被测应用（真进程 + 真 HTTP），不需要手动准备任何服务。

---

## 目录结构

```
project1-stream-chat/
├── backend/
│   ├── main.py          # FastAPI 路由、SSE 编码、降级与取消处理
│   ├── providers.py     # OpenAI 兼容端点的流式调用（httpx）
│   ├── config.py        # 模型注册表 + 环境变量
│   ├── cost.py          # token 估算与成本计算
│   └── .env.example
├── frontend/
│   └── index.html       # Vue 3 + 手写 SSE 解析（零构建）
├── tests/
│   ├── conftest.py      # 起 mock 上游 + 被测应用的夹具
│   ├── mock_runner.py   # 启停 mock 服务
│   └── test_api.py      # 13 条验收断言
├── docs/
│   └── ADR.md           # 6 条架构决策记录（面试深挖点）
├── run.ps1              # Windows 一键启动
├── pytest.ini
└── README.md
```

---

## 关键实现：三处最容易写错的地方

### 1. 中断后必须真的关掉上游连接

```python
# providers.py
try:
    ...  # 迭代上游 SSE
finally:
    await response.aclose()      # ← 这一行兜住"不残留后台请求"
```

客户端断开时，Starlette 取消这个生成器，`asyncio.CancelledError` 向上传播，
`finally` 保证连接被关闭。**绝不能 `except Exception` 吞掉它** ——
`CancelledError` 在 3.8+ 是 `BaseException`，用 `except Exception` 抓不到，
但用 `except BaseException` 或裸 `except` 就会把它吃掉，导致上游请求继续跑到结束。

测试怎么证明这条成立：`test_abort_stops_upstream_request` 在读到 2 个 chunk 后断开，
然后断言 mock 上游**只收到 1 个请求**，且不会继续把内容推完。

### 2. 已经吐出内容之后不能再降级

```python
# main.py
except UpstreamError as exc:
    if emitted_any or fatal_auth or attempt == len(candidates) - 1:
        yield sse("error", {"message": last_error, "fatal": True})
        return
    continue      # 只有"一个字都没吐"时才能安全换模型
```

否则用户会看到前半段是 A 模型、后半段是 B 模型拼接出来的回答 ——
比直接报错糟糕得多。

### 3. 半截 JSON 与跨 chunk 的中文

SSE 是字节流，一个 chunk 可能在任意位置被切开。所以前端**不能**用 `split('\n\n')`：

```javascript
// frontend/index.html
const decoder = new TextDecoder('utf-8');
buffer += decoder.decode(value, { stream: true });   // stream:true 处理跨 chunk 的多字节字符
let sep;
while ((sep = buffer.indexOf('\n\n')) !== -1) {
  const raw = buffer.slice(0, sep);
  buffer = buffer.slice(sep + 2);                    // 不完整的尾巴留在 buffer 里
  ...
}
```

`{ stream: true }` 是关键：少了它，一个中文字符被切成两半时会解码成乱码。

---

## 已知未验证项（诚实清单）

这一节故意留着。**没验证的东西不能装作验证过了。**

| 项 | 状态 | 原因 |
|---|---|---|
| 前端在真实浏览器里的渲染与交互 | ⚠️ 未验证 | 本机无浏览器自动化环境；已通过后端测试覆盖 SSE 协议层 |
| Vue 依赖 CDN，离线时页面空白 | ⚠️ 已知限制 | 需要联网加载 `unpkg.com`；离线场景请先 `npm i vue` 换成本地文件 |
| 真实模型的 TTFT / 成本数字 | ⚠️ 未验证 | 未配置真实 API Key；README 中的指标来自本地 mock |
| 多轮长对话的上下文裁剪 | ⚠️ 未实现 | 计划在项目二做（滑动窗口 + 摘要压缩） |
| 工具调用（function calling）增量拼接 | ✅ 已实现，⚠️ 未测试 | mock 上游暂不返回 `tool_calls`；代码路径见 `providers.py` |

### 浏览器端的自测清单（请自己走一遍）

1. 输入"用一句话解释什么是向量" → 文字**逐字**出现（不是整段蹦出来）
2. 点击"停止" → 立刻停下，并显示 [已停止]；**已生成的部分要保留**
3. 停止后，看后端终端：应出现 `请求被取消（客户端断开）` 的日志
4. 点"重新生成" → 同一条问题重跑，历史不重复累积
5. 每轮回复下方应显示 in/out token、成本（$ 与 ¥）、TTFT、总耗时
6. 把 `FIRST_TOKEN_TIMEOUT_S` 设成 `0.001` 再发一次 → 应看到黄色"已降级"提示与原因

---

## 面试可以怎么讲（3 分钟版）

> **问题**：公司内部要接多个大模型，但每个团队各写一套调用代码，
> 流式处理、超时、成本统计全是重复劳动，而且没有人知道花了多少钱。
>
> **方案**：做一个统一的多模型对话层。协议统一走 OpenAI 兼容接口，
> 用"模型注册表"把 provider、价格、上下文窗口集中管理；
> 流式用 SSE，服务端只做转发与降级，保持无状态，把"重新生成/编辑重跑"
> 这类交互全部变成前端重发数组。
> 关键取舍有两个：首 token 用独立的 8 秒超时触发降级（因为流式体验里
> TTFT 比总耗时更影响体感），但已经吐出内容后就不再降级（避免两段拼接）。
>
> **量化结果**：13 条验收断言全部通过；首 token 服务端额外延迟约 20ms；
> 中断后上游请求能被确认关闭（用 mock 的请求计数验证）。
>
> **踩过的坑**：`httpx` 的 `client.send(stream=True)` 返回的 Response
> 不能用 `async with`（那是 `client.stream()` 的用法），不显式 `aclose()`
> 会泄漏连接 —— 这个 bug 是被测试抓出来的，不是看文档看出来的。

**深挖点准备**（面试官最可能追问的）：
- 为什么无状态？代价是什么？什么时候必须改成有状态？（→ ADR-003）
- 为什么不用 AI SDK？（→ ADR-002）
- 降级为什么上限 3 个？（→ ADR-005）
- 怎么证明中断真的停掉了上游请求？（→ 测试怎么写的）
- 半截 JSON / 跨 chunk 中文怎么处理？（→ `parseSSE` 与 `{stream:true}`）
