-- ============================================================
-- 自造 SaaS 业务库 · Postgres DDL
-- ============================================================
-- 用法：
--   createdb saas_lab
--   psql -d saas_lab -f data/schema.sql
--   然后按 README 导入 data/out/*.csv
--
-- 设计要点：这个库是给 Text-to-SQL / 数据 Agent 练手用的，
-- 所以字段刻意保留了真实系统里的"脏"（见文件末尾的陷阱说明）。
-- ============================================================

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   INTEGER PRIMARY KEY,
    name        TEXT        NOT NULL,
    industry    TEXT,
    created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    tenant_id   INTEGER     NOT NULL REFERENCES tenants (tenant_id),
    email       TEXT        NOT NULL,
    channel     TEXT,                                   -- 陷阱：大小写不统一 + NULL
    country     TEXT,                                   -- 陷阱：存在 NULL
    signup_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    subscription_id INTEGER PRIMARY KEY,
    user_id         INTEGER     NOT NULL REFERENCES users (user_id),
    tenant_id       INTEGER     NOT NULL REFERENCES tenants (tenant_id),
    plan            TEXT        NOT NULL,               -- free / starter / growth / scale
    mrr_cents       INTEGER     NOT NULL,
    status          TEXT        NOT NULL,               -- free / active / trialing / canceled
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ                         -- NULL 表示仍在订阅
);

CREATE TABLE IF NOT EXISTS events (
    event_id    BIGINT PRIMARY KEY,
    user_id     INTEGER     NOT NULL REFERENCES users (user_id),
    tenant_id   INTEGER     NOT NULL REFERENCES tenants (tenant_id),
    event_type  TEXT        NOT NULL,                   -- login / dashboard_view / ...
    occurred_at TIMESTAMPTZ NOT NULL                    -- 去掉 timestamptz 就会踩时区陷阱
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id      BIGINT PRIMARY KEY,
    user_id         INTEGER     NOT NULL REFERENCES users (user_id),
    tenant_id       INTEGER     NOT NULL REFERENCES tenants (tenant_id),
    subscription_id INTEGER     NOT NULL REFERENCES subscriptions (subscription_id),
    amount_cents    INTEGER     NOT NULL,
    status          TEXT        NOT NULL,               -- paid / refunded / failed
    paid_at         TIMESTAMPTZ NOT NULL
);

-- ---------- 索引 ----------
CREATE INDEX IF NOT EXISTS idx_events_user_time   ON events (user_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_type_time   ON events (event_type, occurred_at);
CREATE INDEX IF NOT EXISTS idx_payments_paid_at   ON payments (paid_at);
CREATE INDEX IF NOT EXISTS idx_payments_tenant    ON payments (tenant_id, paid_at);
CREATE INDEX IF NOT EXISTS idx_subs_status        ON subscriptions (status);
CREATE INDEX IF NOT EXISTS idx_users_tenant       ON users (tenant_id);

-- ---------- few-shot 示例库（第 9 周启用） ----------
-- 存 (自然语言问题, 正确 SQL, 用到的表) 三元组，按问题向量检索后注入提示词。
-- 需要 pgvector 扩展；第 9 周之前保持注释状态，否则本文件无法执行。
--
-- CREATE EXTENSION IF NOT EXISTS vector;
--
-- CREATE TABLE IF NOT EXISTS sql_examples (
--     example_id  SERIAL PRIMARY KEY,
--     question    TEXT NOT NULL,
--     sql         TEXT NOT NULL,
--     tables_used TEXT[],
--     tags        TEXT[],
--     embedding   VECTOR(1024)
-- );
--
-- CREATE INDEX IF NOT EXISTS idx_sql_examples_embedding
--     ON sql_examples USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- 已知数据陷阱（第 9–11 周会逐个处理）
-- ============================================================
-- 1. events.occurred_at 约 30% 带 +08:00 偏移，其余为 UTC。
--    用 TIMESTAMPTZ 导入会自动归一到 UTC；若用 TEXT 存储，跨时区聚合必错。
-- 2. users.channel 存在 'Google' / 'google' / 'GOOGLE' 与 NULL，
--    统计渠道前必须 lower(trim(channel))，否则同一渠道被拆成多行。
-- 3. users 表有 6 行（约 0.5%）邮箱是已有用户的大写版本，COUNT(DISTINCT email) 会虚高。
--    注意：这些重复行**不带自己的订阅与付款** —— 只有 users 表里有重复，
--    subscriptions / payments 不会因此双计。（早期版本会连带复制，已修。）
-- 4. subscriptions.status 为 'free' 或 'trialing' 的订阅没有对应 payments 记录。
--    MRR 含试用会高估；收入统计若用 subscriptions 而非 payments 会算到没收到的钱。
--    另外 'free' 用户会登录、会活跃，但不产生收入 —— 这是登录口径远大于付费口径的原因。
--    注意 plan 字段也有 'free' 取值（此时 mrr_cents = 0），与 status = 'free' 同义；
--    只按 plan 过滤会漏掉免费层。
-- 5. payments.status = 'refunded' 的记录金额为正数，需显式扣除；
--    'failed' 从未真正收款，必须排除。
-- 6. 最近 30 天的 login 事件被人为压低约 40%（异常点）：
--    前 30 天 2,037 条 → 最近 30 天 1,203 条（−40.9%），
--    而同期 paid 付款 192 → 202（+5.2%）基本不动 —— 典型的滞后指标形态。
--    窗口统一定义为 [END-30天, END) = 2026-01-03 ~ 2026-02-01。
--    做趋势对比时必须先确认是数据问题还是业务问题。
--
-- 以上六条都由 scripts/verify_dataset.py 逐条断言校验（31 条断言）。
-- 改生成器后请运行：python scripts/rebuild_dataset.py
