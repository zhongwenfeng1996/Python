# ai-lab

前端转 LLM 应用工程师的实战实验室。所有练习、项目和评估脚本都放这里。

- 学习计划全文：见上一级目录 [前端转LLM应用工程师-学习计划.md](<../前端转LLM应用工程师-学习计划.md>)
- 当前进度：**全职模式 W0（环境与基线）**

---

## 目录结构

```
ai-lab/
├── data/
│   ├── schema.sql              # Postgres 建表 + 已知陷阱说明
│   ├── out/                    # 生成的数据（git 忽略，可重建）
│   └── ...
├── tools/                      # 质量检查工具（分工见下）
│   ├── env_report.py           # 手边有什么（解释器/依赖/编码）
│   ├── check_doc_env.py        # 文档说的是不是真的（22 项一致性断言）
│   ├── check_docs_readability.py  # 教程有没有"没头没尾"的代码片段
│   ├── check_docs_duplication.py  # 同一个知识点是不是在两章各讲了一遍
│   └── check_doc_prerequisites.py # 第 N 章有没有用到第 M>N 章才教的东西（两层）
├── python_basics/              # Python 补给线（两层）
│   ├── docs/                   #   第一层·基础篇：10 章，读懂 AI 应用代码
│   │   ├── 01..10 章
│   │   └── 附-A-读懂一次模型API调用.md
│   └── backend/                #   第二层·后端篇：6 章，写出 FastAPI 服务
│       ├── README.md
│       ├── demos/              #     6 个可运行示例（每次提交都被实际跑一遍）
│       └── verify_backend_demos.py
├── projects/
│   ├── project1-stream-chat/   # 🎯 项目一：流式多模型对话
│   └── mcp-minimal/            # 🎯 MCP Server 最小实验
├── scripts/
│   ├── generate_saas_data.py   # 自造 SaaS 业务库生成器（零依赖）
│   ├── verify_dataset.py       # 校验"六个雷"是否真的在数据里（把文档断言变成可执行测试）
│   ├── rebuild_dataset.py      # 生成 + 校验 一键入口
│   └── load_to_postgres.sh     # 一键建表 + 导入（bash；Windows 见 docs/windows-quickstart.md §5.3）
├── semantics/
│   └── metrics.yml             # 语义层定义（项目三的护城河）
└── README.md
```

### 检查工具的分工

它们回答的是不同的问题，不要混：

| 脚本 | 回答的问题 | 什么时候跑 |
|---|---|---|
| `tools/env_report.py` | **手边有什么**（解释器、依赖、编码、目录） | 换机器 / 环境出问题时 |
| `scripts/verify_dataset.py` | **数据里有什么**（31 条断言，"六个雷"在不在） | 改完数据生成器 |
| `tools/check_doc_env.py` | **文档说的是不是真的**（22 项，下面四个 + 后端示例都在里面） | 改完文档或路径之后 |
| `tools/check_docs_readability.py` | **有没有没头没尾的代码片段**（悬空变量、未定义函数） | 加/改教程示例后 |
| `tools/check_docs_duplication.py` | **同一个知识点是不是讲了两遍** | 往教程里补内容后 |
| `tools/check_doc_prerequisites.py` | **零前置**：第 N 章有没有用到第 M>N 章才教的（两层都查） | 调整章节顺序或加示例后 |
| `python_basics/backend/verify_backend_demos.py` | **后端篇 6 个示例能不能跑出预期输出** | 改后端示例后 |

```powershell
$py = "G:\转型\.venv\Scripts\python.exe"
& $py ai-lab\tools\env_report.py
& $py ai-lab\scripts\verify_dataset.py
& $py ai-lab\tools\check_doc_env.py              # 一键兜住下面全部 + 环境断言
& $py ai-lab\tools\check_docs_readability.py
& $py ai-lab\tools\check_docs_duplication.py
& $py ai-lab\tools\check_doc_prerequisites.py
& $py ai-lab\python_basics\backend\verify_backend_demos.py
```

> **为什么需要"重复检测"这种看起来多余的检查**：往教程里补内容时，
> 最容易犯的错不是写错，而是**把别处已经讲过的又讲一遍** ——
> 两处说法不一致时读者会彻底困惑，而文件存在性检查、悬空变量检查都发现不了。
> 这个仓库真出过一次：86 行的 bytes/str 说明被加进第 04 章，而第 10 章本来就有。

## 快速开始

```bash
# 1. 生成数据集（小规模先验证，几秒完成）
python3 scripts/generate_saas_data.py --users 1200 --events 9000 --tenants 12

# 生产规模（脚本默认 5000 用户 / 6 万背景事件；这套参数是其 4 倍，跑之前确认磁盘与内存）
# python3 scripts/generate_saas_data.py --users 20000 --events 300000 --tenants 200

# 1b. 校验"六个雷"是否真的在数据里（31 条断言，改完生成器必跑）
python3 scripts/rebuild_dataset.py

# 2. 导入 Postgres（需要本地 Postgres 已启动；Windows 见 docs/windows-quickstart.md §5.3）
./scripts/load_to_postgres.sh

# 3. 验证
psql -d saas_lab -c "SELECT COUNT(*) FROM events;"

# 4. 亲手验证口径冲突（这是这份数据集存在的意义）
#    窗口与 verify_dataset.py 的断言保持一致：2026-01-03 起、到 2026-02-01 之前
psql -d saas_lab -c "
SELECT '登录口径' AS metric, COUNT(DISTINCT user_id) AS 活跃用户 FROM events
  WHERE event_type='login'
    AND occurred_at >= '2026-01-03 00:00:00+00' AND occurred_at < '2026-02-01 00:00:00+00'
UNION ALL
SELECT '付费口径', COUNT(DISTINCT user_id) FROM payments
  WHERE status='paid'
    AND paid_at >= '2026-01-03 00:00:00+00' AND paid_at < '2026-02-01 00:00:00+00';"
```

> ⚠️ **时间窗口是固定的**（2024-02-02 ~ 2026-02-01），所有查询请用显式时间字面量。
> 用 `now()` 会得到空结果或错误区间 —— 这是刻意设计：**评估集的稳定性优先于"数据看起来是新的"**。
> 如果你的 Text-to-SQL 解析"最近 30 天"，必须把它映射到 `:start_date` / `:end_date` 参数，而不是数据库的 `now()`。
>
> **"最近 30 天"到底是从哪天到哪天？** 统一按 `[END - 30 天, END)` 算，
> 即 `>= '2026-01-03' AND < '2026-02-01'`。生成器打印的趋势对照表、
> `verify_dataset.py` 的断言、以及这里的手工查询用的是同一个窗口 —— 三处必须一致，
> 否则你会花半天时间去查一个"数字对不上"的假问题。

**没有 Postgres？** 数据本身是 CSV，可以先用 DuckDB 或 pandas 读，不影响 W1–W2 的学习。
pgvector 是可选项，`schema.sql` 里相关段落已默认注释，第 9 周再启用。

---

## 为什么自造数据集

你目前没有行业积累和现成语料，这在别的方向是硬伤，在**数据分析 Agent** 方向恰恰不是问题：

| 你缺的 | 其他方向 | 这个方向 |
|---|---|---|
| 行业语料 | 法律/医疗没语料就做不出说服力 | 公开基准 + 自造业务库即可 |
| 评估标准 | RAG 只能靠 LLM 打分，不稳定 | **标准 SQL 比对执行结果，能写单测** |
| 业务张力 | 公开数据集不会给你口径冲突 | **可以自己埋，而且埋得比真实业务更干净** |

自造数据集的最大优势：**你能控制"哪里埋雷"**。真实业务里口径冲突是一团乱麻，这里每一颗雷都有明确的教学目的，面试时可以逐条讲。

---

## 数据集说明

### 五张表

| 表 | 行数（示例规模） | 说明 |
|---|---|---|
| `tenants` | 12 | 租户（B 端客户），带行业属性 |
| `users` | 1,206 | 用户，带获客渠道、国家、注册时间（含 6 行大小写重复邮箱） |
| `subscriptions` | 1,200 | 订阅关系，状态含 free / active / trialing / canceled |
| `events` | 约 20,800 | 行为事件（login、dashboard_view、api_call 等），见下方说明 |
| `payments` | 3,171 | 付款流水，状态含 paid / refunded / failed |

时间窗口固定为 **2024-02-02 ~ 2026-02-01**（730 天），相同 seed 永远生成同一份数据。

> **`--events 9000` 生成出来的 events 为什么有两万多行？**
> 9000 是**背景事件**的条数。为了让"最近 30 天活跃下跌"这个异常点真的可观测，
> 生成器还会给活跃用户补齐历史登录基线（约 1.1 万条），这部分是额外叠加的。
> 这是刻意设计：没有历史基线，趋势图就没有可对比的基准，异常点也就无从谈起。
> 想让总量严格等于 9000，把 `--events` 调小即可（背景事件会等比减少）。

### 用户结构（freemium 模型）

```
free      70%   注册未付费 —— 会登录、会活跃，但不产生收入
active    18%   正式付费订阅
trialing   4%   试用中（不产生付款记录）
canceled   8%   已取消
```

> 这个结构是"活跃用户 >> 付费用户"的根源，也是绝大多数 SaaS 的真实形态。
> 第一版数据集没有免费层，导致登录口径反而小于付费口径，与直觉相反，已修正。

### 四组口径冲突（核心教学点）

以下数字来自 `--users 1200 --events 9000` 的示例规模，你自己的规模会不同：

| 指标 | 口径 A | 口径 B | 差异 |
|---|---|---|---|
| 活跃用户 | 登录口径 **722** | 付费口径 **202** | **3.57x** |
| MRR | 仅 active **16,642 元** | 含 trialing **19,728 元** | 高估 **18.5%** |
| 收入 | gross **247,347 元** | net 扣退款 **238,434 元** | 差 **3.6%** |
| 流失率 | 订阅取消 **9.1%** | 60 天无任何事件 **14.4%** | 差 **1.6 倍** |

> 这四组数字由 `scripts/generate_saas_data.py` 每次运行后直接打印，
> 并由 `scripts/verify_dataset.py` 断言校验 —— **文档里的数字必须能跑出来**。
> 换了规模或种子之后，请以脚本输出为准并同步更新本表。

**同一个问题，换个口径结论就变。** 这就是为什么需要语义层（`semantics/metrics.yml`）。

### 埋的六个雷

1. **时区不一致** —— 30.4% 的事件以 `+08:00` 表示（与 UTC 表示同一时刻）。用字符串比较时间必错。
2. **渠道名大小写不一** —— 11 种原始写法实际只有 7 个渠道（`google` 98 / `Google` 100 / `GOOGLE` 109），还有 98 行 NULL。不 `lower(trim())` 归一化就会把一个渠道拆成多行。
3. **重复用户** —— 6 行（0.50%）邮箱是已有用户的大写版本，`COUNT(DISTINCT email)` 会虚高。**注意**：这些重复行**不携带自己的订阅与付款**——早先的版本会连带复制，导致 MRR/收入双计 0.5%，那已经不是"教学用的脏数据"而是"会污染评估集的错误数据"。
4. **免费与试用用户无付款记录** —— 收入统计若走 `subscriptions` 而非 `payments`，会算到没收到的钱。
5. **退款与失败付款** —— `refunded` 金额为正数需显式扣除，`failed` 从未收款必须排除。
6. **近期活跃被人为压低 40%** —— 登录事件 **2,037 → 1,203（−40.9%）**，同期付费 **192 → 202（+5.2%）基本不动**。这就是典型的滞后指标形态，适合练习"先确认是数据问题还是业务问题"。

> **第 6 条曾经是坏的，值得单独说一句。** 早先的实现只在最后 30 天给活跃用户注入登录，
> 此前 700 天一条都不补，结果最后一个月 login 反而是上月的 **2.6 倍** —— 文档说"下跌"，
> 数据说"上涨"，方向完全相反，用这份数据练异常检测会被自己的数据骗。
> 现在改为"历史按月补基线 + 最后 30 天按密度压低"，两处走**同一个注入函数**，
> 并加了 `scripts/verify_dataset.py` 把这条写成断言。
> 教训：**凡是文档做出的断言，都要有一条校验规则盯着。**

---

## 与学习计划的对应

| 计划周次 | 用到本仓库的什么 |
|---|---|
| W3–W5 | `generate_saas_data.py` 产出的 CSV 作为 RAG 的知识库素材（产品文档、FAQ 可与数据一起用） |
| W6 | `schema.sql` 建库，练习 SQL 与检索 |
| W8 | Text-to-SQL 基线：用 `payments` / `events` 跑通自然语言 → SQL → 执行 |
| W9 | `semantics/metrics.yml` 接入提示词，实测 EX 从基线提升到 ≥ 80% |
| W10 | 用"最近 30 天活跃下跌"练习异常检测与归因 |
| W11 | `schema.sql` 末尾的安全白名单 + `metrics.yml` 的 `safety` 段做防护 |

---

## 重建与校验

**改完生成器一定要跑这个**，它会重新生成数据并把"六个雷"逐条断言一遍：

```bash
python3 scripts/rebuild_dataset.py            # 生成 + 校验（31 条断言）
python3 scripts/rebuild_dataset.py --check-only   # 只校验，不重新生成
python3 scripts/verify_dataset.py             # 等价于只跑校验那一步
```

退出码 0 表示全部断言通过。**有断言失败时先改生成器，不要改文档** ——
文档与数据不一致时，让文档去迁就数据是最省事但最危险的做法：
它会让"这份数据能练什么"这件事失去可信度，而可信度正是评估集唯一的价值来源。

> 当前状态：`31/31 通过`（时区 30.4%、重复邮箱 0.50%、登录 −40.9%、付费 +5.2%、
> 取消订阅 0% 堆在最后一天、外键零孤儿、`subscription_id ≠ user_id` …）

```bash
# 换种子生成另一份数据（用于验证你的评估集没有过拟合）
python3 scripts/generate_saas_data.py --seed 99999 --users 1200 --events 9000

# 生产规模
python3 scripts/generate_saas_data.py --users 20000 --events 300000 --tenants 200
```

> Windows 下把上面的 `python3` 换成 `py -3`，见 [docs/windows-quickstart.md](docs/windows-quickstart.md)。
