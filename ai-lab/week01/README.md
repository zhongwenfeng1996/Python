# W1 · 流式对话 CLI

对应学习计划 [§3 第 1 周](<../../前端转LLM应用工程师-学习计划.md>)（全职模式下即第 1 周）。

产物：`chat.py` —— 不用 SDK、不用框架，纯标准库手写流式对话。
手写过一遍，再用框架时你才知道它替你做了什么。

---

## 今天的任务（约 4 小时）

| 时间 | 任务 | 说明 |
|---|---|---|
| 0.5h | 跑通 mock | 不需要 API Key，先确认代码本身能跑 |
| 0.5h | 接真实 API | 复制 `.env.example` 为 `.env`，填你的 key |
| 1h | 读一遍 `chat.py` | 重点读 `open_stream()` 和 `stream_turn()` 两个函数 |
| 2h | 动手改造 | 见下面「动手改造」，这部分才是学习真正发生的地方 |

## 运行

```bash
# A. 没有 key：用本地 mock 验证代码
python3 mock_server.py                    # 终端 1
OPENAI_BASE_URL=http://127.0.0.1:8765/v1 OPENAI_API_KEY=test python3 chat.py   # 终端 2

# B. 有 key：真实调用
cp .env.example .env                      # 填入 OPENAI_API_KEY
python3 chat.py
python3 chat.py --model gpt-4o-mini --temperature 0.3
```

会话内命令：`/exit` `/clear` `/stats` `/temp 0.7` `/model gpt-4o-mini`

---

## 验收清单

- [ ] 首 token 在 1.5s 内出现（国内模型通常 0.5–1.5s）
- [ ] `Ctrl+C` 立刻中断，**且中断后还能继续对话**（说明没有残留后台请求）
- [ ] 每轮显示 input/output token 与实际成本
- [ ] 能逐字解释 `data: {...}` 这行在说什么
- [ ] 能说清 TTFT 和总耗时的区别，以及各自受什么影响

## 动手改造（按顺序做）

1. **加 `/retry` 命令** —— 重发上一条用户消息并丢弃上一次回复。
   这是"重新生成"功能的底层实现，做完你对流式 UI 的理解会不一样。
2. **加 `/save` 命令** —— 把对话历史导出成 JSON 文件（含时间戳与 token 数）。
   顺带想一下：真实产品里这个文件应该存本地还是服务端？
3. **对比估算与真实值** —— 把 `estimate_tokens()` 的结果和 API 返回的 `usage` 并列打印，
   看偏差有多大。中文场景下你会有惊喜。

## 观察实验（20 分钟，别跳过）

同一个问题（例如"用一句话解释什么是向量"）在 `temperature` = 0 / 0.7 / 1.5 下各跑 5 次，
把答案记成一张表。这是理解"模型为什么不确定"最快的方式，也是后面写评估集的心理基础。

## 常见坑

| 现象 | 原因 |
|---|---|
| `401` | key 没填对，或 key 与 `OPENAI_BASE_URL` 不匹配（DeepSeek 的 key 不能打 OpenAI 端点） |
| `404` | `OPENAI_BASE_URL` 多了或少了 `/v1` |
| 统计里 usage 为空 | 不是所有兼容端点都支持 `stream_options`，程序会自动回退到估算 |
| 中文 token 数偏高 | 中文约 1 字 = 1 token，英文约 4 字符 = 1 token，成本差 4 倍以上 |

## 本周节奏（全职 8 周版 W1）

- **Day 1–2**：跑通 + 读懂（今天）
- **Day 3**：改造 1、2
- **Day 4**：改造 3 + 观察实验
- **Day 5**：按计划把 GitHub 仓库建起来，并发出第 1 篇文章《手写 SSE 流式解析》

> W1 结束时你应该能不看代码，白板画出"用户输入 → HTTP 流式请求 → SSE 解析 → 逐字渲染"的链路。
