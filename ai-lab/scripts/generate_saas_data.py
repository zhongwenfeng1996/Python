#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自造 SaaS 业务库数据集生成器（零依赖，纯标准库）

设计目标不是"生成一份干净数据"，而是生成一份**有真实业务张力**的数据：

  1. 埋了 4 组业务口径冲突 —— 同一个问题，两种口径，结论甚至相反
  2. 埋了真实的数据质量问题 —— 渠道名大小写不一、时区不一致、NULL、重复用户
  3. 最近 30 天人为压低活跃度 —— 登录口径显著下跌，但付费口径基本不动
     （这正是"滞后指标"的真实形态，也是"找出异常点"练习的核心）
  4. 存在 freemium 免费用户层 —— 活跃用户远多于付费用户，符合真实 SaaS 形态

用法::

    python3 scripts/generate_saas_data.py                                # 默认规模
    python3 scripts/generate_saas_data.py --users 800 --events 4000      # 快速验证
    python3 scripts/generate_saas_data.py --users 20000 --events 300000  # 生产规模

脚本跑完会直接打印**两种口径下的核心指标对比**，这是本数据集最重要的教学点。
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
RECENT_DROP = 0.40   # 近期活跃被人为抹除的概率（制造异常点）
SILENT_RATIO = 0.20  # 20% 的沉默用户

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
    # 陷阱 4：制造少量"看起来像重复"的记录（同邮箱不同大小写）
    for _ in range(max(1, args.users // 200)):
        src = rnd.choice(users)
        dup = dict(src)
        dup["user_id"] = len(users) + 1
        dup["email"] = src["email"].upper()
        users.append(dup)

    # ---------------- subscriptions ----------------
    statuses = [s for s, _ in STATUS_WEIGHTS]
    status_w = [w for _, w in STATUS_WEIGHTS]

    subs = []
    for sid, u in enumerate(users, start=1):
        signup = datetime.fromisoformat(u["signup_at"])
        started = signup + timedelta(days=rnd.uniform(0, 7))
        if started >= END:
            started = END - timedelta(days=1)

        status = weighted(rnd, statuses, status_w)
        if status == "free":
            plan, mrr = "free", 0
        else:
            plan, mrr = weighted(rnd, PLANS, PLAN_WEIGHTS)

        if status == "canceled":
            ended = started + timedelta(days=rnd.uniform(30, 420))
            if ended >= END:
                ended = END - timedelta(days=1)
        else:
            ended = None

        subs.append({
            "subscription_id": sid,
            "user_id": u["user_id"],
            "tenant_id": u["tenant_id"],
            "plan": plan,
            "mrr_cents": mrr,
            "status": status,
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat() if ended else "",
        })

    # ---------------- 近期登录用户集合 ----------------
    # 精确控制口径差异与异常点：先把"谁在最近 30 天登录过"定下来，再据此生成事件
    recent_login_users: set[int] = set()
    for u, s in zip(users, subs):
        rate = RECENT_LOGIN_RATE[s["status"]]
        if rnd.random() < SILENT_RATIO:
            rate *= 0.05
        if rnd.random() < rate and rnd.random() >= RECENT_DROP:
            recent_login_users.add(u["user_id"])

    recent_start = END - timedelta(days=RECENT_DAYS)

    # ---------------- events ----------------
    events = []
    eid = 0

    # 1) 保证"近期登录用户"确实在最近 30 天有 login 事件
    for u in users:
        if u["user_id"] not in recent_login_users:
            continue
        for _ in range(rnd.randint(1, 3)):
            eid += 1
            ts = END - timedelta(days=rnd.random() * RECENT_DAYS,
                                 seconds=rnd.randrange(86400))
            events.append({
                "event_id": eid,
                "user_id": u["user_id"],
                "tenant_id": u["tenant_id"],
                "event_type": "login",
                "occurred_at": event_ts(rnd, ts),
            })

    # 2) 其余事件按活跃权重铺满整个时间窗口
    remaining = max(args.events - len(events), 0)
    if remaining:
        weights = []
        for u, s in zip(users, subs):
            w = EVENT_BASE_W[s["status"]]
            if rnd.random() < SILENT_RATIO:
                w *= 0.03
            weights.append(max(w, 0.001))

        for u in rnd.choices(users, weights=weights, k=remaining):
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

            eid += 1
            events.append({
                "event_id": eid,
                "user_id": u["user_id"],
                "tenant_id": u["tenant_id"],
                "event_type": etype,
                "occurred_at": event_ts(rnd, ts),
            })

    events.sort(key=lambda e: e["occurred_at"])

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
    payments.sort(key=lambda p: p["paid_at"])

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
    login_60d = {e["user_id"] for e in events if parse_ts(e["occurred_at"]) >= silent_start}
    churn_by_silent = (1 - len(login_60d) / len(users)) * 100

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
    print(f"{'流失率口径 B：60天无登录占比':<32}{churn_by_silent:>9.1f}%")
    print("=" * 64)
    print("异常点：最近 30 天活跃被人为压低 40%。")
    print("      登录口径明显下跌，付费口径基本不动 —— 典型的滞后指标，")
    print("      适合练习「先确认是数据问题还是业务问题」。")


if __name__ == "__main__":
    main()
