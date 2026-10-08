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
├── scripts/
│   ├── generate_saas_data.py   # 自造 SaaS 业务库生成器（零依赖）
│   └── load_to_postgres.sh     # 一键建表 + 导入
├── semantics/
│   └── metrics.yml             # 语义层定义（项目三的护城河）
└── README.md
```

## 快速开始

```bash
# 1. 生成数据集（小规模先验证，几秒完成）
python3 scripts/generate_saas_data.py --users 1200 --events 9000 --tenants 12

# 生产规模（约 5 万用户 / 60 万事件，跑之前确认磁盘与内存）
# python3 scripts/generate_saas_data.py

# 2. 导入 Postgres（需要本地 Postgres 已启动）
./scripts/load_to_postgres.sh

# 3. 验证
psql -d saas_lab -c "SELECT COUNT(*) FROM events;"

# 4. 亲手验证口径冲突（这是这份数据集存在的意义）
psql -d saas_lab -c "
SELECT '登录口径' AS metric, COUNT(DISTINCT user_id) AS 活跃用户 FROM events
  WHERE event_type='login'
    AND occurred_at >= '2026-01-02 00:00:00+00' AND occurred_at < '2026-02-01 00:00:00+00'
UNION ALL
SELECT '付费口径', COUNT(DISTINCT user_id) FROM payments
  WHERE status='paid'
    AND paid_at >= '2026-01-02 00:00:00+00' AND paid_at < '2026-02-01 00:00:00+00';"
```

> ⚠️ **时间窗口是固定的**（2024-02-02 ~ 2026-02-01），所有查询请用显式时间字面量。
> 用 `now()` 会得到空结果或错误区间 —— 这是刻意设计：**评估集的稳定性优先于"数据看起来是新的"**。
> 如果你的 Text-to-SQL 解析"最近 30 天"，必须把它映射到 `:start_date` / `:end_date` 参数，而不是数据库的 `now()`。

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
| `users` | 1,206 | 用户，带获客渠道、国家、注册时间 |
| `subscriptions` | 1,206 | 订阅关系，状态含 free / active / trialing / canceled |
| `events` | 9,000 | 行为事件（login、dashboard_view、api_call 等） |
| `payments` | 2,862 | 付款流水，状态含 paid / refunded / failed |

时间窗口固定为 **2024-02-02 ~ 2026-02-01**（730 天），相同 seed 永远生成同一份数据。

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
| 活跃用户 | 登录口径 **429** | 付费口径 **241** | **1.78x** |
| MRR | 仅 active **17,144 元** | 含 trialing **21,183 元** | 高估 **23.6%** |
| 收入 | gross **209,550 元** | net 扣退款 **200,024 元** | 差 **4.5%** |
| 流失率 | 订阅取消 **8.5%** | 60 天无登录 **21.5%** | 差 **2.5 倍** |

**同一个问题，换个口径结论就变。** 这就是为什么需要语义层（`semantics/metrics.yml`）。

### 埋的六个雷

1. **时区不一致** —— 约 30% 的事件以 `+08:00` 表示同一时刻，其余走 UTC。用字符串比较时间必错。
2. **渠道名大小写不一** —— `Google` / `google` / `GOOGLE` / `NULL` 并存，不归一化会把一个渠道拆成四行。
3. **重复用户** —— 约 0.5% 的邮箱存在大小写重复记录，`COUNT(DISTINCT email)` 会虚高。
4. **免费与试用用户无付款记录** —— 收入统计若走 `subscriptions` 而非 `payments`，会算到没收到的钱。
5. **退款与失败付款** —— `refunded` 金额为正数需显式扣除，`failed` 从未收款必须排除。
6. **近期活跃被人为压低 40%** —— 登录口径明显下跌，付费口径基本不动（滞后指标），适合练习"先确认是数据问题还是业务问题"。

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

## 复现命令

```bash
# 换种子生成另一份数据（用于验证你的评估集没有过拟合）
python3 scripts/generate_saas_data.py --seed 99999 --users 1200 --events 9000

# 生产规模
python3 scripts/generate_saas_data.py --users 20000 --events 300000 --tenants 200
```
