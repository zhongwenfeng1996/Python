#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自造 SaaS 业务库数据集生成器（零依赖，纯标准库）

设计目标不是"生成一份干净数据"，而是生成一份**有真实业务张力**的数据：

  1. 埋了 4 组业务口径冲突 —— 同一个问题，两种口径，结论甚至相反
  2. 埋了真实的数据质量问题 —— 渠道名大小写不一、时区不一致、NULL、重复用户
  3. 最近 30 天人为压低活跃度 —— 登录口径显著下跌，但付费口径基本不动
     （这正是"滞后指标"的真实形态，也是"找出异常点"练习的核心）
     **实现要点**：必须同时给这批用户补齐历史登录基线，否则"下跌"根本无法观测——
     只在最后 30 天注入 login 会让最后一个月反而暴涨 2.6 倍，与设计意图相反。
  4. 存在 freemium 免费用户层 —— 活跃用户远多于付费用户，符合真实 SaaS 形态

用法::

    python3 scripts/generate_saas_data.py                                # 默认规模
    python3 scripts/generate_saas_data.py --users 800 --events 4000      # 快速验证
    python3 scripts/generate_saas_data.py --users 20000 --events 300000  # 生产规模

脚本跑完会直接打印**两种口径下的核心指标对比**与**最近/前 30 天趋势对照**，
后者用来验证异常点练习的前提是否成立。
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED_DEFAULT = 20260214
UTC = timezone.utc

# 数据集时间窗口固定（保证可复现：同 seed 永远得到同一份数据）
END = datetime(2026, 2, 1, tzinfo=UTC)
WINDOW_DAYS = 730
START = END - timedelta(days=WINDOW_DAYS)

RECENT_DAYS = 30     # "最近 30 天"窗口
RECENT_DROP = 0.40   # 最近 30 天活跃被人为压低的幅度（制造异常点，让趋势可归因）
SILENT_RATIO = 0.20  # 20% 的沉默用户

# 活跃用户每月大约产生多少条 login 事件。
# 这个常数同时决定"历史基线"和"最近 30 天"的注入密度 —— 两者必须同源，
# 否则注入行为本身就会制造出与设计相反的趋势（详见事件生成部分的注释）。
LOGINS_PER_ACTIVE_MONTH = 2.0

INDUSTRIES = ["SaaS", "电商", "教育", "金融", "制造", "医疗", "物流", "内容"]

# 陷阱 1：渠道名大小写不统一 + NULL
CHANNELS_RAW = [
    "Google", "google", "GOOGLE", "Bing", "bing",
    "Partner", "partner", "Organic", "referral", "Outbound", "event", None,
]

# 陷阱 2：计划与 MRR（单位：分）
PLANS = [("starter", 2900), ("growth", 9900), ("scale", 29900)]
PLAN_WEIGHTS = [0.55, 0.33, 0.12]

# freemium 结构：免费用户占多数，付费是少数 —— 这是活跃用户 >> 付费用户的原因
STATUS_WEIGHTS = [
    ("free", 0.70),      # 注册但未付费
    ("active", 0.18),    # 正式付费订阅
    ("trialing", 0.04),  # 试用中（不产生 payments）
    ("canceled", 0.08),  # 已取消
]

# 各状态下"最近 30 天有过登录"的基准概率（真实 SaaS：付了钱的人才会持续登录）
RECENT_LOGIN_RATE = {"free": 0.70, "active": 0.95, "trialing": 0.92, "canceled": 0.35}

# 事件频率权重
EVENT_BASE_W = {"free": 0.8, "active": 1.0, "trialing": 1.6, "canceled": 0.35}

EVENT_TYPES = ["login", "dashboard_view", "report_export", "api_call", "invite_member", "settings_change"]
EVENT_WEIGHTS = [0.42, 0.22, 0.12, 0.14, 0.05, 0.05]

COUNTRIES = ["CN", "US", "SG", "JP", "DE", "GB", None]

TZ_PLUS8 = timezone(timedelta(hours=8))


def weighted(rnd: random.Random, items: list, weights: list):
    return rnd.choices(items, weights=weights, k=1)[0]


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def event_ts(rnd: random.Random, base: datetime) -> str:
    """陷阱 3：30% 的事件用 +08:00 表示同一时刻（埋点写了本地时区）。"""
    if rnd.random() < 0.30:
        return base.astimezone(TZ_PLUS8).isoformat()
    return base.isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(description="生成自造 SaaS 业务库数据集")
    ap.add_argument("--users", type=int, default=5000, help="用户数（默认 5000）")
    ap.add_argument("--events", type=int, default=60000, help="事件数（默认 60000）")
    ap.add_argument("--tenants", type=int, default=40, help="租户数（默认 40）")
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT, help="随机种子")
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "out",
        help="输出目录",
    )
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    # ---------------- tenants ----------------
    tenants = []
    for tid in range(1, args.tenants + 1):
        created = START + timedelta(days=rnd.random() * WINDOW_DAYS)
        tenants.append({
            "tenant_id": tid,
            "name": f"tenant_{tid:03d}",
            "industry": rnd.choice(INDUSTRIES),
            "created_at": created.isoformat(),
        })
    # 租户规模不均（大客户用户多），模拟真实分布
    tenant_weights = [rnd.random() ** 2 + 0.05 for _ in tenants]
    tenant_ids = [t["tenant_id"] for t in tenants]

    # ---------------- users ----------------
    users = []
    for uid in range(1, args.users + 1):
        signup = START + timedelta(days=rnd.random() ** 0.75 * WINDOW_DAYS)
        users.append({
            "user_id": uid,
            "tenant_id": weighted(rnd, tenant_ids, tenant_weights),
            "email": f"user{uid:06d}@example.com",
            "channel": rnd.choice(CHANNELS_RAW),
            "country": rnd.choice(COUNTRIES),
            "signup_at": signup.isoformat(),
        })
    # 陷阱 4：制造"N 个看起来像重复"的记录（同邮箱不同大小写）。
    # 源用户从 signed_up 里取，保证重复邮箱一定对应一个已存在的真实用户；
    # 已用过的源不重复取，避免出现 3 条同邮箱（那会让"重复率"统计变得模糊）。
    n_dup = max(1, args.users // 200)
    used_sources: set[int] = set()
    while len(used_sources) < n_dup:
        src = rnd.choice(users)
        if src["user_id"] in used_sources:
            continue
        used_sources.add(src["user_id"])
        dup = dict(src)
        dup["user_id"] = len(users) + 1
        dup["email"] = src["email"].upper()
        users.append(dup)

    # ---------------- subscriptions ----------------
    # 注意：大小写重复用户（user_id > args.users）也在 users 表里，但**不给他们订阅**。
    # 为什么：陷阱 3 想要的是"COUNT(DISTINCT email) 会虚高"，只需要重复的用户行；
    # 如果连带复制订阅与付款，就会把 MRR / 收入也双计约 0.5%，把教学用的脏数据
    # 变成会污染评估集的错误数据。
    statuses = [s for s, _ in STATUS_WEIGHTS]
    status_w = [w for _, w in STATUS_WEIGHTS]

    subs = []
    for u in users:
        if u["user_id"] > args.users:
            continue
        signup = datetime.fromisoformat(u["signup_at"])
        # 非取消订阅的开始时间：注册后 0-7 天内开通（太靠近窗口末端就前移）
        started = signup + timedelta(days=rnd.uniform(0, 7))
        if started >= END:
            started = END - timedelta(days=1)

        status = weighted(rnd, statuses, status_w)
        if status == "free":
            plan, mrr = "free", 0
        else:
            plan, mrr = weighted(rnd, PLANS, PLAN_WEIGHTS)

        if status == "canceled":
            # 先抽"取消时刻"，再由它反推"开始时刻"，而不是从注册时间往后加天数再钳位。
            #
            # 为什么必须反过来算：如果写成 min(started + uniform(30,420), END)，
            # 那么只要"注册时间 + 420 天"超过窗口末端，结果就恒等于 END ——
            # 实测会让 41% 的取消全部堆在窗口最后一天，形成一条人为的"流失悬崖"，
            # 任何按日/按周的流失趋势题都会被它毁掉。
            # 反过来算则天然保证：取消时刻均匀散布在整个窗口，生命周期 ≥ 30 天。
            duration = timedelta(days=rnd.uniform(30, 420))
            latest_cancel = END - timedelta(days=rnd.uniform(0, 300))
            ended = latest_cancel
            started = ended - duration
            if started < START:
                started = START
        else:
            ended = None

        subs.append({
            "user_id": u["user_id"],
            "tenant_id": u["tenant_id"],
            "plan": plan,
            "mrr_cents": mrr,
            "status": status,
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat() if ended else "",
        })

    # 订阅 ID 在**最后**才分配，而且刻意打乱，避免出现 "subscription_id = user_id"。
    #
    # 为什么要打乱（真实踩过的坑）：
    #   第一版只是把 subs 按顺序重排了一遍，但 subs 本身就是按 user_id 顺序构建的，
    #   于是 subscription_id 依然逐行等于 user_id —— 等于没改。
    #   如果两者恒等，Text-to-SQL 模型会发现"用错列也能答对"，评估集 EX 虚高，
    #   面试被追问"你怎么保证模型不是靠列名巧合"时答不上来。
    sub_ids = list(range(1, len(subs) + 1))
    rnd.shuffle(sub_ids)
    for k, s in zip(sub_ids, subs):
        s["subscription_id"] = k
    # 列顺序固定，保证 CSV 表头与 schema.sql 一致
    subs = [{key: s[key] for key in
             ("subscription_id", "user_id", "tenant_id", "plan", "mrr_cents",
              "status", "started_at", "ended_at")} for s in subs]

    # ---------------- 近期登录用户集合 ----------------
    # 这里定义的是"最近 30 天**本来**会登录的人"（自然活跃度），先不扣减。
    # 关键修正：RECENT_DROP 绝不能作用在这一步。
    #   早先写成 `rnd.random() < rate and rnd.random() >= RECENT_DROP`，
    #   等于把"下跌"提前烧进了用户集合的规模里，之后再给他注入事件，
    #   下跌就永远观察不到了 —— 因为集合内外的人都拿同样的注入密度。
    #   正确的做法是：集合按自然活跃度定（用来算口径分母），
    #   再让这**同一批人**在最近 30 天产生更少的事件，这样趋势才真的掉下来。
    status_by_uid = {s["user_id"]: s["status"] for s in subs}
    recent_login_users: set[int] = set()
    for u in users:
        uid = u["user_id"]
        if uid not in status_by_uid:      # 重复记录用户不产生订阅，也不进入活跃口径
            continue
        rate = RECENT_LOGIN_RATE[status_by_uid[uid]]
        if rnd.random() < SILENT_RATIO:
            rate *= 0.05
        if rnd.random() < rate:
            recent_login_users.add(uid)

    recent_start = END - timedelta(days=RECENT_DAYS)

    # ---------------- events ----------------
    events = []
    eid = 0

    def add_event(uid: int, tid: int, etype: str, when: datetime) -> None:
        nonlocal eid
        eid += 1
        events.append({
            "event_id": eid,
            "user_id": uid,
            "tenant_id": tid,
            "event_type": etype,
            "occurred_at": event_ts(rnd, when),
        })

    def login_count(expected: float) -> int:
        """
        按期望条数抽样，而不是取整。

        为什么不能取整：如果写成 int(expected)，那么"活跃度 0.95"和"被压低后的
        0.57"在每月 2 条的量级下都会得到 1 条 —— 注入行为把要观察的下跌抹平了，
        趋势图上看不出任何异常。必须让"每月登录几次"对活跃度敏感。
        """
        whole = int(expected)
        return whole + (1 if rnd.random() < (expected - whole) else 0)

    def inject_logins(uid: int, tid: int, signup: datetime,
                      lo: datetime, hi: datetime, rate: float,
                      monthly: float, drop: float) -> None:
        """
        在 [lo, hi) 内按 (rate * monthly * (1 - drop)) 的密度注入 login 事件。

        历史基线与最近 30 天走的是**同一个函数**：早先两处各写一遍，
        密度不一致，结果"最近窗口"反而比历史高 —— 注入行为自己制造了假趋势。

        时间分布用"整段区间均匀采样"：从 epoch 对齐的月份边界逐月推进，
        每个月在 [max(月首, lo, signup), min(月尾, hi)) 内均匀取点。
        早先用"游标 + 固定 30 天跨度"采样，会把月内时间挤到前面、甚至重复，
        趋势图上的月度分布会失真。
        """
        if hi <= lo:
            return
        expected = rate * monthly * (1.0 - drop)
        if expected <= 0:
            return
        cursor = max(lo, signup)
        while cursor < hi:
            month_end = min(cursor + timedelta(days=RECENT_DAYS), hi)
            span = int((month_end - cursor).total_seconds())
            for _ in range(login_count(expected)):
                if span > 0:
                    add_event(uid, tid, "login",
                              cursor + timedelta(seconds=rnd.randrange(span)))
            cursor = month_end

    # 1) 先按活跃权重把 args.events 条背景事件铺满整个时间窗口（不含重复记录用户）。
    #
    #    顺序很重要：这一步必须在"注入登录"**之前**跑完。
    #    早先注入在前，注入出来的上万条历史登录直接把 eid 顶到 9000 以上，
    #    于是 remaining = max(9000 - len(events), 0) 恒为 0 —— 背景事件一条都不生成，
    #    "最近 30 天 vs 前 30 天"就只剩注入事件在互相比较，趋势失去意义。
    real_users = [u for u in users if u["user_id"] in status_by_uid]
    weights = []
    for u in real_users:
        w = EVENT_BASE_W[status_by_uid[u["user_id"]]]
        if rnd.random() < SILENT_RATIO:
            w *= 0.03
        weights.append(max(w, 0.001))

    for u in rnd.choices(real_users, weights=weights, k=args.events):
        signup = datetime.fromisoformat(u["signup_at"])
        signup_day = (signup - START).total_seconds() / 86400
        span = max(WINDOW_DAYS - signup_day, 1.0)
        offset = signup_day + (rnd.random() ** 0.85) * span
        ts = START + timedelta(days=offset, seconds=rnd.randrange(86400))
        if ts > END:
            ts = END - timedelta(hours=rnd.randrange(1, 48))

        etype = weighted(rnd, EVENT_TYPES, EVENT_WEIGHTS)
        # 不让"非近期活跃用户"意外产生近期 login，否则口径集合会被污染
        if (etype == "login" and ts >= recent_start
                and u["user_id"] not in recent_login_users):
            etype = "dashboard_view"

        add_event(u["user_id"], u["tenant_id"], etype, ts)

    # 2) 给每个"近期登录用户"补历史登录基线（最近 30 天之前）。
    #
    #    为什么必须补（这是本数据集最关键的一处修正）：
    #      早先的实现只给这些人在**最后 30 天**注入 login，此前 700 天一条都不补。
    #      结果最后 30 天的 login 是前 30 天的 2.6 倍，README 声称的
    #      "登录口径明显下跌"在数据里完全相反，异常检测练习(§W10)直接失效。
    for u in users:
        uid = u["user_id"]
        if uid not in recent_login_users:
            continue
        inject_logins(uid, u["tenant_id"],
                      datetime.fromisoformat(u["signup_at"]),
                      START, recent_start,
                      RECENT_LOGIN_RATE[status_by_uid[uid]],
                      LOGINS_PER_ACTIVE_MONTH, drop=0.0)

    # 3) 最近 30 天：同一批人、同一密度，但活跃度按 RECENT_DROP 压低 —— 这就是异常点本身。
    for u in users:
        uid = u["user_id"]
        if uid not in recent_login_users:
            continue
        inject_logins(uid, u["tenant_id"],
                      datetime.fromisoformat(u["signup_at"]),
                      recent_start, END,
                      RECENT_LOGIN_RATE[status_by_uid[uid]],
                      LOGINS_PER_ACTIVE_MONTH, drop=RECENT_DROP)

    # 按真实时刻排序，而不是按 ISO 字符串排序。
    # 字符串排序只保证"本地时间"单调（约 30% 的事件带 +08:00），会把 UTC 顺序打乱，
    # 于是任何依赖行序 / 流式读取 / LIMIT 不带 ORDER BY 的分析都会被误导。
    events.sort(key=lambda e: datetime.fromisoformat(e["occurred_at"]))

    # ---------------- payments ----------------
    # free 与 trialing 不产生任何付款记录
    payments = []
    pid = 0
    for s in subs:
        if s["status"] not in ("active", "canceled"):
            continue
        started = datetime.fromisoformat(s["started_at"])
        ended = datetime.fromisoformat(s["ended_at"]) if s["ended_at"] else END
        stop = min(ended, END)
        cur = started
        while cur < stop:
            pid += 1
            r = rnd.random()
            if r < 0.93:
                pay_status = "paid"
            elif r < 0.97:
                pay_status = "refunded"
            else:
                pay_status = "failed"
            payments.append({
                "payment_id": pid,
                "user_id": s["user_id"],
                "tenant_id": s["tenant_id"],
                "subscription_id": s["subscription_id"],
                "amount_cents": s["mrr_cents"],
                "status": pay_status,
                "paid_at": cur.isoformat(),
            })
            cur += timedelta(days=30)
    payments.sort(key=lambda p: datetime.fromisoformat(p["paid_at"]))

    # ---------------- 落盘 ----------------
    write_csv(args.out / "tenants.csv", tenants, list(tenants[0].keys()))
    write_csv(args.out / "users.csv", users, list(users[0].keys()))
    write_csv(args.out / "subscriptions.csv", subs, list(subs[0].keys()))
    write_csv(args.out / "events.csv", events, list(events[0].keys()))
    write_csv(args.out / "payments.csv", payments, list(payments[0].keys()))

    # ---------------- 口径冲突对照 ----------------
    def parse_ts(raw: str) -> datetime:
        return datetime.fromisoformat(raw)

    login_active = recent_login_users
    paying_active = {
        p["user_id"] for p in payments
        if p["status"] == "paid" and parse_ts(p["paid_at"]) >= recent_start
    }
    active_subs = [s for s in subs if s["status"] == "active"]
    trial_subs = [s for s in subs if s["status"] == "trialing"]
    free_cnt = sum(1 for s in subs if s["status"] == "free")

    mrr_active_only = sum(s["mrr_cents"] for s in active_subs) / 100
    mrr_include_trial = (sum(s["mrr_cents"] for s in active_subs)
                         + sum(s["mrr_cents"] for s in trial_subs)) / 100

    gross = sum(p["amount_cents"] for p in payments if p["status"] == "paid") / 100
    refunded = sum(p["amount_cents"] for p in payments if p["status"] == "refunded") / 100

    canceled = sum(1 for s in subs if s["status"] == "canceled")
    churn_by_sub = canceled / len(subs) * 100
    silent_start = END - timedelta(days=60)
    # 口径 B 统计的是"60 天内**没有产生任何事件**的用户"，不是"没有登录"。
    # 标签必须写准 —— 数据分析题里口径标签写错比算错更致命。
    active_60d = {e["user_id"] for e in events if parse_ts(e["occurred_at"]) >= silent_start}
    churn_by_silent = (1 - len(active_60d) / len(users)) * 100

    # ---- 窗口趋势对照：验证"最近 30 天登录下跌、付费持平"确实成立 ----
    def count_events_between(event_type: str, lo: datetime, hi: datetime) -> int:
        return sum(
            1 for e in events
            if e["event_type"] == event_type and lo <= parse_ts(e["occurred_at"]) < hi
        )

    prev_start = recent_start - timedelta(days=RECENT_DAYS)
    login_prev = count_events_between("login", prev_start, recent_start)
    login_recent = count_events_between("login", recent_start, END)
    paid_prev = sum(1 for p in payments
                    if p["status"] == "paid" and prev_start <= parse_ts(p["paid_at"]) < recent_start)
    paid_recent = sum(1 for p in payments
                      if p["status"] == "paid" and recent_start <= parse_ts(p["paid_at"]) < END)

    def pct_change(new: int, old: int) -> str:
        if old == 0:
            return "n/a"
        return f"{(new / old - 1) * 100:+.1f}%"

    print(f"[OK] 输出目录: {args.out}")
    print(f"     tenants={len(tenants)}  users={len(users)}  subscriptions={len(subs)}"
          f"  events={len(events)}  payments={len(payments)}")
    print(f"     用户结构: free={free_cnt}  active={len(active_subs)}"
          f"  trialing={len(trial_subs)}  canceled={canceled}")
    print()
    print("=" * 64)
    print("口径冲突对照表（这份数据集的核心价值）")
    print("=" * 64)
    print(f"{'活跃用户（登录口径, 近30天）':<32}{len(login_active):>10,}")
    print(f"{'活跃用户（付费口径, 近30天）':<32}{len(paying_active):>10,}")
    print(f"{'→ 倍数（登录 / 付费）':<32}{len(login_active) / max(len(paying_active), 1):>10.2f}x")
    print("-" * 64)
    print(f"{'MRR 口径 A：仅 active（元）':<32}{mrr_active_only:>10,.2f}")
    print(f"{'MRR 口径 B：含 trialing（元）':<32}{mrr_include_trial:>10,.2f}")
    print(f"{'→ 高估幅度':<32}{(mrr_include_trial / max(mrr_active_only, 1e-9) - 1) * 100:>9.1f}%")
    print("-" * 64)
    print(f"{'收入口径 A：gross（元）':<32}{gross:>10,.2f}")
    print(f"{'收入口径 B：net 扣退款（元）':<32}{gross - refunded:>10,.2f}")
    print("-" * 64)
    print(f"{'流失率口径 A：订阅取消占比':<32}{churn_by_sub:>9.1f}%")
    print(f"{'流失率口径 B：60天无任何事件占比':<32}{churn_by_silent:>9.1f}%")
    print("=" * 64)
    print("窗口趋势对照（最近 30 天 vs 前 30 天）—— 异常检测练习的依据")
    print("=" * 64)
    print(f"{'login 事件  前窗口 → 近期窗口':<32}{login_prev:>8,} →{login_recent:>8,}   {pct_change(login_recent, login_prev)}")
    print(f"{'paid 付款   前窗口 → 近期窗口':<32}{paid_prev:>8,} →{paid_recent:>8,}   {pct_change(paid_recent, paid_prev)}")
    print("=" * 64)
    print(f"异常点：最近 30 天的 login 被人为压低约 {int(RECENT_DROP * 100)}%（相比历史基线）。")
    print("      付费口径基本不动 —— 这是典型的「滞后指标」，")
    print("      适合练习「先确认是数据问题还是业务问题」。")
    print("      注意：login 是**事件条数**，不是去重用户数；SQL 里要用 COUNT(DISTINCT user_id)。")


if __name__ == "__main__":
    main()
