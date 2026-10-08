# 进度看板

> 这份文件只有一个作用：**让你自己在任何时刻都能回答"我现在在哪、下一步做什么"**。
> 每周日复盘时更新一次。计划原文见[前端转LLM应用工程师-学习计划.md](<前端转LLM应用工程师-学习计划.md>)。

**最后更新：W0 收尾 / W1 起点**

---

## 当前阶段

| 项目 | 值 |
|---|---|
| 模式 | 全职（计划 §11，8 周冲刺） |
| 当前周次 | **W0 → W1** |
| 今日重点 | 落地项目一：FastAPI 流式后端 |
| 卡点 | 系统未安装独立 Python（只有 Store 占位符）；PowerShell 沙箱受限 |

---

## 阶段进度

| 阶段 | 周次 | 状态 | 产出物 | 完成度 |
|---|---|---|---|---|
| 环境与基线 | W0 | ✅ 完成 | ai-lab 仓库、git 初始化、Windows 上手指南、环境自检脚本 | 100% |
| 模型调用与流式 | W1–W3 | 🚧 进行中 | 项目一 | 20% |
| RAG 完整链路 | W4–W7 | ⬜ 未开始 | 项目二 + 评估报告 | 0% |
| 数据 Agent 与工程化 | W8–W12 | ⬜ 未开始 | 项目三 + 求职材料 | 0% |

---

## 已有资产（W0 交付）

**可以直接用的：**

- [x] 学习计划全文（809 行，§0–§14，含全职/在职两套排期）
- [x] `ai-lab/week01/chat.py` —— 304 行零依赖流式对话 CLI，含 TTFT 统计、成本估算、优雅中断
- [x] `ai-lab/week01/mock_server.py` —— 本地 mock，**没有 API Key 也能验证全部代码**
- [x] `ai-lab/python_basics/` —— 10 章教程（3511 行）+ JS→Python 速查表 + 3 个零依赖脚本
- [x] `ai-lab/python_basics/check_env.py` —— 学习材料自检
- [x] `ai-lab/tools/env_report.py` —— 环境自检（Windows 优先）
- [x] `ai-lab/data/schema.sql` + `generate_saas_data.py` —— 自造 SaaS 业务库，含 6 个数据陷阱
- [x] `ai-lab/data/out/*.csv` —— 12 租户 / 1206 用户 / 9000 事件 / 2862 付款
- [x] `ai-lab/semantics/metrics.yml` —— 语义层定义（含 4 组口径冲突）
- [x] `ai-lab/docs/windows-quickstart.md` —— Windows 命令对照 + 报错速查
- [x] git 仓库初始化 + 首次提交 + `.gitattributes`

**明确还没有的（这些才是能不能转职的关键）：**

- [ ] 项目一：FastAPI 流式后端 + Vue 前端（**进行中**）
- [ ] 项目二：RAG 全链路（解析、切片、pgvector、rerank、引用）
- [ ] 项目三：Text-to-SQL + 语义层 + 图表 + SQL 安全
- [ ] 评估脚本（`make eval` 雏形）与量化指标记录
- [ ] Docker Compose 一键启动
- [ ] CI（lint + 测试 + 评估门禁）
- [ ] MCP Server 最小实验
- [ ] 简历、技术文章、投递记录

---

## 待办（按优先级）

### P0 · 立刻做

- [ ] **装独立 Python 3.12**（系统当前没有可用解释器，见 [windows-quickstart §2](ai-lab/docs/windows-quickstart.md)）
- [ ] **项目一后端**：FastAPI `/chat` 流式接口 + 多模型路由 + 8s 超时降级
- [ ] **项目一前端**：Vue3 + TS 流式渲染、可中断、可重新生成、成本显示

### P1 · 本周内

- [ ] 修 `python_basics/docs` 里已确认的 5 处代码错误（详见下方"已知问题"）
- [ ] 补 Python 教程缺失的 **FastAPI 章节**（当前 0 行，是最大缺口）
- [ ] 写第一版评估脚本（哪怕只有 5 条样本）

### P2 · 之后

- [ ] 把仓库推到 GitHub，开始 build in public
- [ ] MCP Server 最小实验（50 行内）
- [ ] 写第 1 篇技术文章《手写 SSE 流式解析》

---

## 已知问题（待修）

**Python 教程（`ai-lab/python_basics/docs/`）**

| 位置 | 问题 | 严重度 |
|---|---|---|
| `08-类与对象.md:277` | `except ValidationError` 未 import → NameError | 高 |
| `09-异步与并发.md:227` | `API_KEY` 未定义 → NameError | 高 |
| `10-调试与报错.md:74` | `RuntimeWarning` 写成 `RuntimeError` | 中 |
| `10-调试与报错.md:95` | 示例引号是 ASCII，不会报 SyntaxError，与正文矛盾 | 中 |
| `10-调试与报错.md:14,34,45` | 引用行号 47，实际是 68 | 低 |
| `docs/README.md:3,37-45` | 写"9 章"，实际 10 章；章节表缺第 10 章 | 低 |
| 多处 | macOS 命令（`python3`、`source .venv/bin/activate`） | 中 |

**其他**

| 位置 | 问题 |
|---|---|
| `python_basics/check_env.py:129` | 检查的是 `ai-lab/.venv`，但教程让在 `python_basics` 建 `.venv` |
| `python_basics/check_env.py:61-69` | 只检查 10 章中的 2 章，却宣称"检查必需文件" |
| `check_env.py:53` | 门槛 3.9，但教程代码用了 3.10+ 语法（`X \| None`） |
| 两份 README | `chat.py` 写 257 行，实际 304 行 |

---

## 每周复盘模板

复制这段填，**不要跳过**：

```markdown
### W? 复盘（日期）

- 实际投入：__ 小时（计划 __ 小时）
- 本周产出（别人能打开看的）：__
- 量化指标更新：__（EX / recall@5 / P95 延迟 / 单次成本）
- 卡住的点：__
- 下周要砍掉什么：__
- 有没有连续 3 天没写代码：__
```

> **任何一项连续两次为空，就砍掉一个项目或缩减范围，而不是延长周期。**
> 全职学习最贵的是时间成本，不是精力成本。
