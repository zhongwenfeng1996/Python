# 转型 AI 大模型应用开发

一个前端工程师转 LLM 应用工程师的完整转型记录：学习计划、实战代码、数据集、评估脚本，
以及每周的进度与复盘。**所有产出公开，进度可验证。**

> **公开仓库**：<https://github.com/zhongwenfeng1996/Python>
> **当前状态：项目一可运行（28 条测试通过）· 项目二 RAG 全链路完成（10 条 ADR + 量化指标）· 教程两层齐全**
> 另有一个把两者接起来的**演示应用**（`project3-rag-chat`），端到端 19 项验收通过。

---

## 从这里开始

| 你想做什么 | 去哪里 |
|---|---|
| **看懂整体规划**（8 周全职 / 12 周在职） | [前端转LLM应用工程师-学习计划.md](<前端转LLM应用工程师-学习计划.md>) |
| **一条命令配好环境** | `Set-Location G:\转型; .\setup.ps1` |
| **跑起来看项目一**（流式对话） | `cd ai-lab\projects\project1-stream-chat; .\run.ps1` |
| **看 RAG 全链路的量化结果** | [项目二 README](ai-lab/projects/project2-rag/README.md) |
| **打开一个能用的 RAG 问答页** | `cd ai-lab\projects\project3-rag-chat; & $py backend\main.py` |
| **配环境出问题了**（Windows 专用，含本机实测的四个坑） | [ai-lab/docs/windows-quickstart.md](ai-lab/docs/windows-quickstart.md) |
| **看当前进度与下一步** | [STATUS.md](STATUS.md) |
| **写代码 / 做练习** | [ai-lab/](ai-lab/README.md) |
| **补 Python**（零基础，3 天） | [ai-lab/python_basics/](ai-lab/python_basics/README.md) |

**第一次进来，按这个顺序读 15 分钟就够：**
1. 本文件的「目标岗位」一节 —— 知道要去哪
2. [STATUS.md](STATUS.md) —— 知道现在在哪
3. [学习计划 §1 能力差距表](<前端转LLM应用工程师-学习计划.md#L42>) —— 知道差多远

---

## 目标岗位

**LLM 应用工程师 / AI 产品研发**，方向：RAG、Agent、数据 Agent（Text-to-SQL）。

不是"会调 API 的后端"，而是**能把大模型能力做成产品的人**：懂模型边界、懂检索、
懂 Agent 编排，同时能把流式、生成式 UI、可中断交互做到位。

四条原则（详见[计划 §0](<前端转LLM应用工程师-学习计划.md#L11>)）：

1. **工程 > 训练** —— 这个岗位 90% 的工作是软件工程，不是模型训练
2. **先手写，再上框架** —— 先用原生 API 手写一遍，再引入 LangChain / AI SDK
3. **评估是分水岭** —— "召回率 0.82、忠实度 0.91" 和 "效果还不错" 是两个价位
4. **不要丢掉前端** —— 交互体验是稀缺项，不是要抛弃的旧资产

---

## 仓库结构

```
转型/                                  # git 仓库根
├── README.md                          # 本文件：入口与导航
├── STATUS.md                          # 进度看板（每周更新）
├── 前端转LLM应用工程师-学习计划.md        # 完整学习计划（§0–§14）
└── ai-lab/                            # 所有代码与数据
    ├── docs/
    │   └── windows-quickstart.md      # Windows 环境搭建（PowerShell 命令）
    ├── week01/                        # W1：手写 SSE 流式对话
    │   ├── chat.py                    #   304 行，零依赖，纯标准库
    │   ├── mock_server.py             #   本地 OpenAI 兼容 mock，无需 API Key
    │   └── .env.example
    ├── python_basics/                 # Python 补给线（零基础，3 天）
    │   ├── docs/                      #   10 章教程，每章含「在 AI 应用里」
    │   ├── js_to_python.md            #   JS → Python 速查表
    │   ├── 01_hello_llm.py            #   第一次调用模型
    │   ├── 02_stream_chat.py          #   流式 + 多轮记忆
    │   ├── 03_chunk_text.py           #   文本切片（RAG 第一步）
    │   └── check_env.py               #   学习材料自检
    ├── tools/
    │   └── env_report.py              # 环境自检（Windows 优先）
    ├── projects/
    │   ├── project1-stream-chat/      # 🎯 项目一：流式多模型对话（已完成）
    │   │   ├── backend/               #   FastAPI + httpx，无状态可降级
    │   │   ├── frontend/              #   Vue 3 + 手写 SSE 解析（零构建）
    │   │   ├── eval/                  #   评估：断言 + 门禁 + 报告
    │   │   ├── tests/                 #   28 条测试（13 API + 15 评估框架）
    │   │   ├── tools/verify_e2e.py    #   端到端证据（TTFT / 成本实测）
    │   │   ├── docs/ADR.md            #   6 条架构决策记录
    │   │   ├── run.ps1                #   一键启动
    │   │   └── eval.ps1               #   评估入口（Windows 版 make eval）
    │   └── mcp-minimal/               # 🎯 MCP Server 最小实验（已完成）
    │       ├── mcp_server.py          #   纯标准库 JSON-RPC over stdio
    │       ├── mcp_client.py          #   自写客户端 + 协议演示
    │       └── tests/                 #   24 条协议测试（含 8 个注入载荷）
    ├── projects/project2-rag/         # 🎯 RAG 全链路（已完成，10 条 ADR）
    │   ├── rag/                       #   切块 / 双路召回 / 词法与 LLM 重排 / 生成
    │   ├── eval/                      #   56 条评估集 + 门禁 + 一堆诊断脚本
    │   ├── docs/ADR.md                #   10 条架构决策（含被实测推翻的判断）
    │   └── README.md                  #   量化指标 + 13 条反直觉发现
    ├── projects/project3-rag-chat/    # 🎯 演示应用（加餐，非计划里的项目三）
    │   ├── backend/main.py            #   FastAPI SSE 流式问答（复用项目二）
    │   ├── frontend/index.html        #   Vue 3（CDN），引用可点、出处并排
    │   └── tools/verify_e2e.py        #   19 项端到端验收（真进程 + 真 HTTP）
    ├── data/
    │   ├── schema.sql                 # Postgres 建表 + 六个数据陷阱说明
    │   └── out/                       # 生成的数据（git 忽略，可重建）
    ├── scripts/
    │   ├── generate_saas_data.py      # 自造 SaaS 业务库生成器（零依赖）
    │   ├── verify_dataset.py          # 把"六个雷"变成 31 条可执行断言
    │   ├── rebuild_dataset.py         # 生成 + 校验 一键入口
    │   └── load_to_postgres.sh        # 一键建表 + 导入（bash）
    └── semantics/
        └── metrics.yml                # 语义层定义（项目三的护城河）
```

三个递进的项目（详见[计划 §6](<前端转LLM应用工程师-学习计划.md#L340>)）：

| # | 项目 | 核心考点 | 完成周 | 状态 |
|---|---|---|---|---|
| 1 | [流式多模型对话](ai-lab/projects/project1-stream-chat/README.md) | 流式、中断、降级、成本可视化 | W3 | ✅ 后端+测试完成 |
| — | [MCP Server 最小实验](ai-lab/projects/mcp-minimal/README.md) | 协议、工具调用、工具安全 | 加餐 | ✅ 24 条协议测试通过 |
| 2 | [企业知识库问答](ai-lab/projects/project2-rag/README.md) | 切片、混合检索、rerank、评估、引用 | W7 | ✅ **全链路完成**（10 条 ADR + 量化指标 + 拒答） |
| — | [RAG 完整链路演示](ai-lab/projects/project3-rag-chat/README.md) | 把 1 和 2 接成一个**能演示的应用** | 加餐 | ✅ 端到端 19 项验收通过 |
| 3 | 对话式数据分析 Agent | schema 检索、语义层、EX 评估、图表、SQL 安全 | W12 | ⬜ |

> ⚠️ **命名说明（避免误解）**：计划里的「项目三」是 *对话式数据分析
> Agent*（Text-to-SQL + 语义层 + SQL 安全），**尚未开始**。
> 仓库里的 `project3-rag-chat/` 是**加餐性质的合并演示应用** ——
> 它把项目一的界面与项目二的检索链路接起来，便于**现场演示**，
> 不是计划里的项目三。两个名字容易混，所以在这里写清楚。

### 项目二 · RAG 全链路：关键指标

全部来自独立评估，且**每个指标都有 ADR 说明为什么这么做**：

| 环节 | 指标 | 值 |
|---|---|---|
| 检索（稀疏） | recall@5 | 0.9038 |
| 检索（LLM 重排） | recall@5 / hit@1 | **0.9808 / 0.7885** |
| 上下文 | 含答案的块进前 5 | **0.9423** |
| 生成 | 引用合法率（零编造编号） | **1.0000** |
| 生成 | 端到端 | **0.8974 ± 0.019**（3 次实测） |
| 拒答 | 对抗集（16 条全应弃答） | **16/16** |
| 校验 | 跨文档混淆 | **机械拦截，0 泄漏** |

---

## 环境要求

- **Python 3.12** —— 已装在本机 `G:\Python\Python312`
- **虚拟环境** —— 已建在本仓库根 `G:\转型\.venv`（依赖已装好）
- **git** —— 版本管理，也是"进度可验证"的前提
- **Docker Desktop** —— W5 之后跑 Postgres + pgvector 用
- 至少 2 个模型 API Key（一个闭源、一个开源）—— **不配也能学**，见下面"离线运行"

**一条命令完成环境初始化**（换机器、或环境坏了的时候用）：

```powershell
Set-Location G:\转型
.\setup.ps1
```

它会探测解释器 → 建 `.venv` → 从国内镜像装依赖 → 跑环境自检。
三种失败它都会给出可直接粘贴的修复命令。

自检（确认手边到底有什么）：

```powershell
& G:\转型\.venv\Scripts\python ai-lab\tools\env_report.py         # 环境总览
& G:\转型\.venv\Scripts\python ai-lab\python_basics\check_env.py  # 学习材料是否齐全
& G:\转型\.venv\Scripts\python ai-lab\scripts\verify_dataset.py   # 数据集 31 条断言
```

### 本机的四个环境事实（踩过才知道）

| 事实 | 说明 |
|---|---|
| `pypi.org` 不可达，清华镜像可达 | 直连 pypi 会**挂住几分钟**而非立刻报错，很像卡死。装包一律加 `--index-url` |
| **没有 `pwsh`**，只有 Windows PowerShell 5.1 | 5.1 读无 BOM 的 `.ps1` 会按 GBK 解码，中文注释会吃掉引号，报出误导性的语法错误。仓库的 `.ps1` 已全部带 BOM，`.gitattributes` 也做了保证 |
| `pip` 的配置文件在中文路径下不可用 | 路径含中文时 pip 报 `invalid cp936 characters`；改用命令行 `--index-url` |
| C 盘只剩约 30GB | 所以 Python 装到 G 盘 |

完整清单与排查方法见 [Windows 上手指南](ai-lab/docs/windows-quickstart.md#01-本机实测结论2026-10这台机器)。

### 离线运行（不需要任何 API Key）

`week01/`、`python_basics/` 和项目一**全部可以离线跑**，用仓库自带的 mock 服务假装模型：

```powershell
cd ai-lab\projects\project1-stream-chat
.\run.ps1          # 起 mock + 后端，自动打开浏览器
```

---

## 关于这个仓库的两个设计选择

**1. 为什么自造数据集，而不是用现成的？**
因为数据分析 Agent 方向最缺的是"业务张力"——公开数据集不会给你口径冲突。
自造数据可以**故意埋雷**（时区不一致、渠道大小写混乱、两个冲突的"活跃用户"定义），
每一颗雷都有明确教学目的，面试时可以逐条讲。详见 [ai-lab/README.md](ai-lab/README.md)。

**2. 为什么评估脚本和代码同等重要？**
因为"我的 RAG 召回率 0.82"和"效果还不错"是两个价位。
本仓库所有能评估的地方都要有可复现的脚本和数字，而不是感觉。
