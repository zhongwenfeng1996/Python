# 进度看板

> 这份文件只有一个作用：**让你自己在任何时刻都能回答"我现在在哪、下一步做什么"**。
> 每周日复盘时更新一次。计划原文见[前端转LLM应用工程师-学习计划.md](<前端转LLM应用工程师-学习计划.md>)。

**最后更新：W1–W3 · 项目一后端完成**

---

## 当前阶段

| 项目 | 值 |
|---|---|
| 模式 | 全职（计划 §11，8 周冲刺） |
| 当前周次 | **W1–W3**（项目一） |
| 今日重点 | 项目一前端浏览器验证 → 然后补 FastAPI 教程章节 |
| 卡点 | 前端只能在真实浏览器里人工验证（本开发环境无浏览器） |

**环境状态（已解决）**

| 项 | 值 |
|---|---|
| Python | ✅ `G:\Python\Python312`（3.12.10，官网静默安装） |
| 虚拟环境 | ✅ `G:\转型\.venv`（依赖已装，pip 走清华镜像） |
| 一键初始化 | ✅ `.\setup.ps1`（探测解释器 → 建 venv → 装依赖 → 自检，已实测跑通） |
| git | ✅ 14 个 commit，分支 `main`，**已推送到 GitHub**（build in public 已开始） |
| 公开仓库 | ✅ <https://github.com/zhongwenfeng1996/Python>（本地与远端 commit 一致） |
| 已知环境坑 | 无 `pwsh`；`.ps1` 必须带 BOM；`pip.ini` 在中文路径下不可用；pypi.org 不可达；`gh` 写不进自己的配置目录（改用 GCM 做凭据助手） |

---

## 阶段进度

| 阶段 | 周次 | 状态 | 产出物 | 完成度 |
|---|---|---|---|---|
| 环境与基线 | W0 | ✅ 完成 | 仓库、git、独立 Python + venv、Windows 指南、环境自检、一键 setup | 100% |
| 模型调用与流式 | W1–W3 | 🚧 进行中 | 项目一（后端+测试+ADR 完成，前端待浏览器验证） | 70% |
| RAG 完整链路 | W4–W7 | ⬜ 未开始 | 项目二 + 评估报告 | 0% |
| 数据 Agent 与工程化 | W8–W12 | ⬜ 未开始 | 项目三 + 求职材料 | 0% |

---

## 已有资产（W0 交付）

**可以直接用的：**

- [x] **独立 Python 3.12.10**（`G:\Python\Python312`）+ 仓库根 `.venv` + `setup.ps1` 一键初始化
- [x] **项目一**：FastAPI 流式后端 + Vue3 前端 + 13/13 验收测试 + 6 条 ADR + 端到端验证脚本
- [x] 学习计划全文（809 行，§0–§14，含全职/在职两套排期）
- [x] `ai-lab/week01/chat.py` —— 304 行零依赖流式对话 CLI，含 TTFT 统计、成本估算、优雅中断
- [x] `ai-lab/week01/mock_server.py` —— 本地 mock，**没有 API Key 也能验证全部代码**
- [x] `ai-lab/python_basics/` —— 10 章教程（3511 行）+ JS→Python 速查表 + 3 个零依赖脚本
- [x] `ai-lab/python_basics/check_env.py` —— 学习材料自检
- [x] `ai-lab/tools/env_report.py` —— 环境自检（Windows 优先）
- [x] `ai-lab/data/schema.sql` + `generate_saas_data.py` —— 自造 SaaS 业务库，含 6 个数据陷阱
- [x] `ai-lab/data/out/*.csv` —— 12 租户 / 1206 用户 / 1200 订阅 / 约 2.08 万事件 / 3171 付款
- [x] `ai-lab/scripts/verify_dataset.py` —— **把"六个雷"变成 31 条可执行断言**（当前 31/31 通过）
- [x] `ai-lab/scripts/rebuild_dataset.py` —— 生成 + 校验一键入口
- [x] `ai-lab/semantics/metrics.yml` —— 语义层定义（含 4 组口径冲突）
- [x] `ai-lab/docs/windows-quickstart.md` —— Windows 命令对照 + 报错速查
- [x] git 仓库初始化 + 首次提交 + `.gitattributes`

**明确还没有的（这些才是能不能转职的关键）：**

- [x] 项目一：流式多模型对话 —— **后端 + 13 条验收测试 + 6 条 ADR 已完成**
      - [x] FastAPI 流式 SSE 后端（`backend/`）
      - [x] 4 模型注册表、首 token 超时降级、成本统计
      - [x] 13/13 验收测试通过（真进程 + 真 HTTP）
      - [x] 端到端验证脚本（TTFT 83ms / 成本 $0.000045 实测）
      - [ ] 前端在真实浏览器里的渲染与交互（**未验证**）
- [x] 评估脚本基础 —— `eval/run_eval.py` + `eval.ps1`（Windows 版 make eval）
      - [x] 断言全部机械可判定（关键词 / JSON / 长度 / 拒答 / 禁用词）
      - [x] 离线可跑（自动起 mock 上游），带 `--fail-under` / `--stability-under` 门禁
      - [x] **对照实验证明指标不是摆设**：jitter 上游让稳定性 100% → 0%，门禁正确拦截
      - [x] 测试 28 passed（13 API + 15 评估框架自测）
- [x] MCP Server 最小实验 —— `ai-lab/projects/mcp-minimal/`
      - [x] 纯标准库手写 JSON-RPC over stdio（initialize / tools/list / tools/call）
      - [x] **24 条协议测试通过**：握手、错误码语义、通知无响应、参数校验
      - [x] 8 个真实注入载荷全部被拒（`calc` 用 AST 解析，不用 eval）
      - [x] **不暴露 read_file / run_command** —— 有测试专门盯着这件事
      - [x] README 写清" MCP vs function calling"的机制级区别
- [x] 教程重排 + 零前置硬化 —— `python_basics/docs/`
      - [x] 章节重排（函数 05→04），21 处前置倒挂降到 **0**
      - [x] 零前置成为**硬规则**（脚本强制，两层都查）
      - [x] 新增 附-A-读懂一次模型API调用.md（承接从章节里挪出的 100 行）
- [x] **第二层·后端篇** —— `python_basics/backend/`
      - [x] 6 章：Pydantic 基础/进阶、自定义异常、FastAPI、流式 SSE、测试
      - [x] **6 个可运行示例**，全部实测通过（含 7 条 pytest 用例）
      - [x] `verify_backend_demos.py`：逐个跑示例并校验输出，已接入一致性检查
      - [x] README 写明前置门槛、章节顺序理由、**未覆盖内容的诚实清单**
- [ ] 项目二：RAG 全链路（解析、切片、pgvector、rerank、引用）
- [ ] 项目三：Text-to-SQL + 语义层 + 图表 + SQL 安全
- [ ] Docker Compose 一键启动
- [ ] CI（lint + 测试 + 评估门禁）
- [ ] 简历、技术文章、投递记录

---

## 待办（按优先级）

### P0 · 立刻做

- [x] ~~**项目一后端**：FastAPI `/chat` 流式接口 + 多模型路由 + 8s 超时降级~~
- [x] ~~**装独立 Python 3.12**~~ → `G:\Python\Python312`（3.12.10），`.venv` 已用它重建，
      新增 `setup.ps1` 一键初始化；重建后 13/13 测试与 31/31 数据断言全部复验通过
- [ ] **项目一前端浏览器验证**：按 [README 的自测清单](ai-lab/projects/project1-stream-chat/README.md)
      走一遍（逐字渲染、停止后保留已生成部分、重新生成、降级提示、成本显示）
      —— 需要人工执行，开发环境没有浏览器

### P1 · 本周内

- [ ] 补 Python 教程缺失的 **FastAPI 章节**（当前 0 行，是最大内容缺口）
      —— 现在有项目一的真实代码可以当教材了，比凭空写一章靠谱
- [ ] 写第一版评估脚本（哪怕只有 5 条样本）
- [ ] 项目一：前端引入 Vite + TS —— ADR-001 里写明"刻意接受的代价"，
      到该还的时候了（触发条件是"需要 TS 类型安全"，而不是"感觉该上了"）

### P2 · 之后

- [x] ~~把仓库推到 GitHub，开始 build in public~~ → <https://github.com/zhongwenfeng1996/Python>
      推到后就有一个"别人能打开看"的产物了 —— 计划 §11 里说的
      "对抗空窗期叙事"靠的就是这个，越早越好。
- [ ] 写第 1 篇技术文章《手写 SSE 流式解析》
- [ ] 把 MCP Server 接进真实客户端（Claude Desktop / Cursor）验证一次

---

## 本轮（目标轮次 1/12）完成情况

| 目标项 | 状态 | 证据 |
|---|---|---|
| 1) Windows 开发环境 + git/公开仓库 | ✅ 环境完成 / ⏸️ 远端待推 | Python 3.12.10 + `.venv` + `setup.ps1` 实跑通过；6 个 commit |
| 2) 项目一落地 | ✅ 后端+前端+测试+ADR | 28 passed；`verify_e2e.py` 实测 TTFT 83ms、成本 $0.000045 |
| 3) 评估脚本基础（make eval 雏形） | ✅ | `eval.ps1` 六个动作；对照实验证明稳定性门禁有效（100% → 0%） |
| 4) 最小 MCP Server 实验 | ✅ | 24 passed；8 个注入载荷被拒 |

**全量复验（独立解释器下重跑）**：项目一 28 passed · MCP 24 passed ·
数据集 31/31 断言 · 评估门禁通过。**合计 52 个自动化测试。**

**只剩一件需要你手动做**：项目一前端在真实浏览器里的验证（开发环境无浏览器）。

---

## 已知问题（待修）

**数据集（已修，2026-02 · 本轮）**

- [x] 陷阱 6 与数据相反：文档说"最近 30 天活跃下跌"，数据实际是 **上涨 2.6 倍**。
      根因是"只在最后 30 天注入登录、此前 700 天不补基线"。已改为历史按月补基线 +
      最近 30 天按密度压低，走同一个注入函数。
- [x] 事件未按真实时刻排序（按带时区的 ISO 字符串排序，UTC 顺序被打乱）→ 改按真实 instant 排序
- [x] 41% 的取消订阅共用同一个 `ended_at`（人工流失悬崖）→ 改为先抽取消时刻再反推开始时间
- [x] `subscription_id` 恒等于 `user_id`（Text-to-SQL 的相关性捷径）→ 已打乱 ID 分配
- [x] 重复邮箱用户连带复制订阅与付款（MRR/收入双计 0.5%）→ 重复行不再携带订阅/付款
- [x] **新增** `verify_dataset.py`，把以上全部写成 31 条断言，防止再次劣化

**Python 教程（`ai-lab/python_basics/docs/`）**

| 位置 | 问题 | 严重度 |
|---|---|---|
| `08-类与对象.md:277` | `except ValidationError` 未 import → NameError | 高 |
| `09-异步与并发.md:227` | `API_KEY` 未定义 → NameError | 高 |
| `10-调试与报错.md:74` | `RuntimeWarning` 写成 `RuntimeError` | 中 |
| `10-调试与报错.md:95` | 示例引号是 ASCII，不会报 SyntaxError，与正文矛盾 | 中 |
| `10-调试与报错.md:14,34,45` | 引用行号 47，实际是 68 | 低 |
| `docs/README.md:3,37-45` | 写"9 章"，实际 10 章；章节表缺第 10 章 | 低 |
| `python_basics/README.md:90-97` | 说"三天之后仍不需要学 asyncio"，与 Day 3 覆盖 07–10 冲突 | 低 |
| `check_env.py:129` | 检查的是 `ai-lab/.venv`，但教程让在 `python_basics` 建 `.venv` | 中 |
| `check_env.py:61-69` | 只检查 10 章中的 2 章，却宣称"检查必需文件" | 中 |
| `check_env.py:53` | 门槛 3.9，但教程代码用了 3.10+ 语法（`X \| None`） | 中 |
| 两份 README | `chat.py` 写 257 行，实际 304 行 | 低 |

**最大内容缺口**

- [ ] Python 教程里 **FastAPI 占 0 行**（目标是"能写 FastAPI 后端"，现在一步没教）
- [ ] Pydantic 只到"能定义模型"，缺 `model_json_schema()`（结构化输出/工具调用的地基）
- [ ] asyncio 只到 `gather` 入门，缺"已运行事件循环里再 `asyncio.run()`"这个 FastAPI 最经典错误
- [ ] 测试（pytest）、日志、`.env` 配置管理全部缺失

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
