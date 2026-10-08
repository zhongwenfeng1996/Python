# 转型 AI 大模型应用开发

一个前端工程师转 LLM 应用工程师的完整转型记录：学习计划、实战代码、数据集、评估脚本，
以及每周的进度与复盘。**所有产出公开，进度可验证。**

> **当前状态：W0 → W1 过渡**
> 教材与数据底座已就绪，正在落地项目一（流式多模型对话应用）。

---

## 从这里开始

| 你想做什么 | 去哪里 |
|---|---|
| **看懂整体规划**（8 周全职 / 12 周在职） | [前端转LLM应用工程师-学习计划.md](<前端转LLM应用工程师-学习计划.md>) |
| **配环境**（Windows 专用，命令可复制） | [ai-lab/docs/windows-quickstart.md](ai-lab/docs/windows-quickstart.md) |
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
    ├── data/
    │   ├── schema.sql                 # Postgres 建表 + 六个数据陷阱说明
    │   └── out/                       # 生成的数据（git 忽略，可重建）
    ├── scripts/
    │   ├── generate_saas_data.py      # 自造 SaaS 业务库生成器（零依赖）
    │   └── load_to_postgres.sh        # 一键建表 + 导入
    └── semantics/
        └── metrics.yml                # 语义层定义（项目三的护城河）
```

三个递进的项目（详见[计划 §6](<前端转LLM应用工程师-学习计划.md#L340>)）：

| # | 项目 | 核心考点 | 完成周 | 状态 |
|---|---|---|---|---|
| 1 | 流式多模型对话 | 流式、中断、降级、成本可视化 | W3 | 🚧 进行中 |
| 2 | 企业知识库问答 | 切片、混合检索、rerank、评估、引用 | W7 | ⬜ |
| 3 | 对话式数据分析 Agent | schema 检索、语义层、EX 评估、图表、SQL 安全 | W12 | ⬜ |

---

## 环境要求

- **Python 3.11+** —— 系统上还没装的话，先看 [windows-quickstart](ai-lab/docs/windows-quickstart.md)
- **git** —— 版本管理，也是"进度可验证"的前提
- **Docker Desktop** —— W5 之后跑 Postgres + pgvector 用
- 至少 2 个模型 API Key（一个闭源、一个开源）

快速自检（确认手边到底有什么）：

```powershell
py -3 ai-lab\tools\env_report.py       # 环境总览
py -3 ai-lab\python_basics\check_env.py # 学习材料是否齐全
```

`week01/` 和 `python_basics/` 里的脚本**全部零依赖**，不需要 API Key 也能验证：

```powershell
# 终端 1：起本地 mock
py -3 ai-lab\week01\mock_server.py
# 终端 2：跑流式对话
$env:OPENAI_BASE_URL = "http://127.0.0.1:8765/v1"; $env:OPENAI_API_KEY = "test"
py -3 ai-lab\week01\chat.py
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
