#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据集校验：验证 data/out/*.csv 是否真的具备 README 声称的那些"教学陷阱"。

为什么需要这个脚本：
    这份数据集的设计意图是"埋雷"——时区不一致、渠道大小写混乱、重复用户、
    口径冲突、异常点。但雷是**在数据里**还是**只在文档里**，必须能自动验证。
    早先就出过一次事故：README 声称"最近 30 天活跃被人为压低，登录口径明显下跌"，
    而实际数据里最后一个月 login 是上月的 2.6 倍，方向完全相反 —— 用这份数据练
    "异常检测"的人会被自己的数据骗。

    所以：**凡是文档做出的断言，都要有一条校验规则盯着。**

用法::

    python ai-lab/scripts/verify_dataset.py
    python ai-lab/scripts/verify_dataset.py --data-dir ai-lab/data/out

零依赖，只用标准库。退出码 0 = 全部通过，1 = 有断言不成立。
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
END = datetime(2026, 2, 1, tzinfo=UTC)
WINDOW_DAYS = 730
START = END - timedelta(days=WINDOW_DAYS)
RECENT_DAYS = 30

OK = "[PASS]"
FAIL = "[FAIL]"
INFO = "[info]"

# 校验结果：(断言描述, 是否通过, 细节)
Results = list[tuple[str, bool, str]]


def load(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def parse_ts(raw: str) -> datetime:
    """解析 ISO-8601 时间戳并归一到 UTC（+08:00 与 +00:00 表示同一时刻）。"""
    return datetime.fromisoformat(raw).astimezone(UTC)


def check(results: Results, desc: str, passed: bool, detail: str = "") -> None:
    results.append((desc, passed, detail))


def main() -> int:
    ap = argparse.ArgumentParser(description="校验自造数据集的教学陷阱是否真实存在")
    ap.add_argument("--data-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "data" / "out")
    args = ap.parse_args()
    d = args.data_dir

    missing = [n for n in ("tenants", "users", "subscriptions", "events", "payments")
               if not (d / f"{n}.csv").exists()]
    if missing:
        print(f"{FAIL} 缺少数据文件：{missing}\n     先运行：python scripts/generate_saas_data.py")
        return 1

    tenants = load(d / "tenants.csv")
    users = load(d / "users.csv")
    subs = load(d / "subscriptions.csv")
    events = load(d / "events.csv")
    payments = load(d / "payments.csv")

    results: Results = []

    print("=" * 78)
    print("  数据集校验 · 验证 README 声称的每个陷阱是否真的在数据里")
    print("=" * 78)
    print(f"  数据目录：{d}")
    print(f"  规模：tenants={len(tenants)} users={len(users)} subscriptions={len(subs)}"
          f" events={len(events)} payments={len(payments)}")
    print()

    # ---------------------------------------------------------------- 结构
    print("—— 结构与类型 ——")
    expected_cols = {
        "tenants": ["tenant_id", "name", "industry", "created_at"],
        "users": ["user_id", "tenant_id", "email", "channel", "country", "signup_at"],
        "subscriptions": ["subscription_id", "user_id", "tenant_id", "plan",
                          "mrr_cents", "status", "started_at", "ended_at"],
        "events": ["event_id", "user_id", "tenant_id", "event_type", "occurred_at"],
        "payments": ["payment_id", "user_id", "tenant_id", "subscription_id",
                     "amount_cents", "status", "paid_at"],
    }
    actual_cols = {
        "tenants": list(tenants[0].keys()), "users": list(users[0].keys()),
        "subscriptions": list(subs[0].keys()), "events": list(events[0].keys()),
        "payments": list(payments[0].keys()),
    }
    for table, cols in expected_cols.items():
        check(results, f"{table}.csv 列名与 schema.sql 一致",
              actual_cols[table] == cols,
              f"实际 {actual_cols[table]}")

    # 空值必须是"空字符串"（未加引号的空字段），否则 \copy 会读成 '' 而不是 NULL
    ended_empty = sum(1 for s in subs if s["ended_at"] == "")
    check(results, "subscriptions.ended_at 空值用空字符串表示（\\copy 可读成 NULL）",
          ended_empty > 0, f"{ended_empty} 行为空")

    # 外键完整性：子表的每个引用都要能找到父行，否则导入 Postgres 会失败
    real_ids = {u["user_id"] for u in users}
    sub_uids = {s["user_id"] for s in subs}
    pay_uids = {p["user_id"] for p in payments}
    sub_ids = {s["subscription_id"] for s in subs}
    pay_sids = {p["subscription_id"] for p in payments}
    check(results, "订阅的 user_id 都存在于 users 表（无孤儿外键）",
          sub_uids <= real_ids, f"孤儿 {len(sub_uids - real_ids)} 个")
    check(results, "付款的 user_id 都存在于 users 表（无孤儿外键）",
          pay_uids <= real_ids, f"孤儿 {len(pay_uids - real_ids)} 个")
    check(results, "付款的 subscription_id 都存在于 subscriptions 表（外键不会炸）",
          pay_sids <= sub_ids, f"孤儿 {len(pay_sids - sub_ids)} 个")

    # ---------------------------------------------------------------- 陷阱 1
    print("—— 陷阱 1：时区不一致（约 30% 事件带 +08:00）——")
    tz_counter: Counter[str] = Counter()
    for e in events:
        tz_counter[e["occurred_at"][-6:]] += 1
    plus8 = tz_counter.get("+08:00", 0)
    ratio = plus8 / len(events) * 100 if events else 0
    other_tz = {k: v for k, v in tz_counter.items() if k not in ("+08:00", "+00:00")}
    check(results, "事件中约 30% 带 +08:00 偏移", 25 <= ratio <= 35,
          f"{plus8}/{len(events)} = {ratio:.2f}%，分布 {dict(tz_counter)}")
    check(results, "只有 +08:00 与 +00:00 两种偏移（无第三种）", not other_tz,
          f"异常偏移 {other_tz}" if other_tz else "干净")
    non_event_plus8 = sum(
        1 for rows, col in ((tenants, "created_at"), (users, "signup_at"),
                            (subs, "started_at"), (payments, "paid_at"))
        for r in rows if r[col].endswith("+08:00")
    )
    check(results, "只有 events 表有本地偏移，其它表一律 UTC", non_event_plus8 == 0,
          f"{non_event_plus8} 行带偏移")

    # 字符串排序 vs 真实时刻排序（决定 LIMIT/流式分析是否可信）
    raw_sorted = [e["occurred_at"] for e in events]
    instants = [parse_ts(t) for t in raw_sorted]
    inversions = sum(1 for i in range(1, len(instants)) if instants[i] < instants[i - 1])
    check(results, "events.csv 按真实时刻有序（不是只按本地时间字符串排序）",
          inversions == 0, f"{inversions} 处逆序")

    # ---------------------------------------------------------------- 陷阱 2
    print("—— 陷阱 2：渠道大小写不统一 + NULL ——")
    channels = Counter(u["channel"] for u in users)
    non_null = {k: v for k, v in channels.items() if k}
    normalized = defaultdict(int)
    for k, v in non_null.items():
        normalized[k.strip().lower()] += v
    check(results, "渠道名存在大小写变体（需 lower(trim()) 归一化）",
          len(normalized) < len(non_null),
          f"原始 {len(non_null)} 种 → 归一化后 {len(normalized)} 种，"
          f"例如 google={channels.get('google', 0)}/Google={channels.get('Google', 0)}"
          f"/GOOGLE={channels.get('GOOGLE', 0)}")
    check(results, "channel 存在 NULL", channels.get("", 0) > 0, f"{channels.get('', 0)} 行为空")
    countries_null = sum(1 for u in users if not u["country"])
    check(results, "country 存在 NULL", countries_null > 0, f"{countries_null} 行为空")

    # ---------------------------------------------------------------- 陷阱 3
    print("—— 陷阱 3：邮箱大小写重复（约 0.5%）——")
    emails_lower = Counter(u["email"].lower() for u in users)
    dup_emails = {e: c for e, c in emails_lower.items() if c > 1}
    dup_rows = sum(c - 1 for c in dup_emails.values())
    dup_ratio = dup_rows / len(users) * 100
    check(results, "存在大小写重复邮箱（COUNT(DISTINCT email) 会虚高）",
          dup_rows > 0, f"{dup_rows} 条重复行 = {dup_ratio:.2f}%")

    # 重复用户不应连带复制订阅/付款（否则 MRR 与收入会被双计）。
    # 关键：重复邮箱会出现两次，其中**原始用户是应该持有订阅的**，
    # 只有"额外插入的重复行"才不该有。所以先找出原始用户的最大 user_id，
    # 只对超出这个范围的行做断言 —— 否则就是校验脚本自己误报。
    emails_multi = {e for e, c in emails_lower.items() if c > 1}
    original_max_uid = max(
        (int(u["user_id"]) for u in users if u["email"].lower() not in emails_multi),
        default=0,
    )
    ghost_uids = {u["user_id"] for u in users
                  if int(u["user_id"]) > original_max_uid}
    ghost_with_subs = ghost_uids & sub_uids
    ghost_with_pays = ghost_uids & pay_uids
    check(results, "额外插入的重复用户不持有订阅（避免 MRR 双计）",
          not ghost_with_subs,
          f"原始用户最大 id={original_max_uid}，多余行 {len(ghost_uids)} 个，"
          f"其中 {len(ghost_with_subs)} 个持有订阅")
    check(results, "额外插入的重复用户不产生付款（避免收入双计）",
          not ghost_with_pays, f"{len(ghost_with_pays)} 个产生付款")

    # ---------------------------------------------------------------- 陷阱 4
    print("—— 陷阱 4：free / trialing 不产生付款 ——")
    status_by_uid = {s["user_id"]: s["status"] for s in subs}
    sub_status_by_sid = {s["subscription_id"]: s["status"] for s in subs}
    paid_sids = {p["subscription_id"] for p in payments}
    paid_statuses = {sub_status_by_sid.get(sid) for sid in paid_sids}
    check(results, "没有任何付款指向 free / trialing 订阅",
          not (paid_statuses & {"free", "trialing"}),
          f"出现 {sorted(x for x in paid_statuses if x in ('free', 'trialing'))}")
    free_cnt = sum(1 for s in subs if s["status"] == "free")
    active_cnt = sum(1 for s in subs if s["status"] == "active")
    check(results, "存在 freemium 免费层（登录口径 > 付费口径的根源）",
          free_cnt > active_cnt, f"free={free_cnt} active={active_cnt}")

    # ---------------------------------------------------------------- 陷阱 5
    print("—— 陷阱 5：退款与失败付款 ——")
    pay_status = Counter(p["status"] for p in payments)
    refunded = [p for p in payments if p["status"] == "refunded"]
    failed = [p for p in payments if p["status"] == "failed"]
    check(results, "存在 refunded 记录且金额为正（需显式扣除）",
          bool(refunded) and all(int(p["amount_cents"]) > 0 for p in refunded),
          f"{len(refunded)} 条")
    check(results, "存在 failed 记录（从未真正收款，必须排除）",
          bool(failed), f"{len(failed)} 条")
    check(results, "不存在 0 元流水", all(int(p["amount_cents"]) > 0 for p in payments),
          f"状态分布 {dict(pay_status)}")

    # ---------------------------------------------------------------- 陷阱 6
    print("—— 陷阱 6：最近 30 天登录下跌、付费持平（异常检测练习的前提）——")
    recent_start = END - timedelta(days=RECENT_DAYS)
    prev_start = recent_start - timedelta(days=RECENT_DAYS)

    def count_in(rows: list[dict], col: str, lo: datetime, hi: datetime,
                 type_col: str = "", type_val: str = "") -> int:
        n = 0
        for r in rows:
            if type_col and r[type_col] != type_val:
                continue
            if lo <= parse_ts(r[col]) < hi:
                n += 1
        return n

    login_prev = count_in(events, "occurred_at", prev_start, recent_start,
                          "event_type", "login")
    login_recent = count_in(events, "occurred_at", recent_start, END,
                            "event_type", "login")
    paid_prev = count_in([p for p in payments if p["status"] == "paid"],
                         "paid_at", prev_start, recent_start)
    paid_recent = count_in([p for p in payments if p["status"] == "paid"],
                           "paid_at", recent_start, END)

    login_change = (login_recent / login_prev - 1) * 100 if login_prev else 0
    paid_change = (paid_recent / paid_prev - 1) * 100 if paid_prev else 0

    # 登录必须"明显下跌"：允许 -20% ~ -60%
    check(results, "login 事件在最近 30 天明显下跌（-20% ~ -60%）",
          -60 <= login_change <= -20,
          f"前窗口 {login_prev} → 近期 {login_recent} = {login_change:+.1f}%")
    # 付费必须"基本不动"：允许 ±15%
    check(results, "paid 付款在最近 30 天基本持平（±15%）",
          abs(paid_change) <= 15,
          f"前窗口 {paid_prev} → 近期 {paid_recent} = {paid_change:+.1f}%")
    # 付费跌幅不能超过登录跌幅（否则不是滞后指标）
    check(results, "登录跌幅显著大于付费跌幅（构成滞后指标对照）",
          login_change < paid_change,
          f"login {login_change:+.1f}% vs paid {paid_change:+.1f}%")

    # ---------------------------------------------------------------- 数据卫生
    print("—— 数据卫生（会影响评估集可信度）——")
    # 取消订阅不应全部堆在窗口最后一天
    canceled = [s for s in subs if s["status"] == "canceled"]
    ended_instants = [parse_ts(s["ended_at"]) for s in canceled if s["ended_at"]]
    if ended_instants:
        last_day = sum(1 for t in ended_instants if t >= END - timedelta(days=1))
        ratio_last = last_day / len(ended_instants) * 100
        check(results, "取消订阅不堆在窗口最后一天（无人工流失悬崖）",
              ratio_last < 20, f"{last_day}/{len(ended_instants)} = {ratio_last:.1f}% 落在最后一天")
    # 订阅生命周期
    lifetimes = [(parse_ts(s["ended_at"]) - parse_ts(s["started_at"])).days
                 for s in canceled if s["ended_at"]]
    if lifetimes:
        check(results, "取消订阅的生命周期 ≥ 30 天",
              min(lifetimes) >= 30, f"最短 {min(lifetimes)} 天，中位 {statistics.median(lifetimes):.0f} 天")
    # 所有时间戳都落在固定窗口内
    out_of_window = sum(
        1 for r in events if not (START <= parse_ts(r["occurred_at"]) <= END)
    )
    check(results, "所有事件落在固定时间窗口内", out_of_window == 0,
          f"{out_of_window} 条越界")
    # subscription_id 与 user_id 不应恒等（否则 Text-to-SQL 会有"用错列也对"的捷径）
    same = sum(1 for s in subs if s["subscription_id"] == s["user_id"])
    check(results, "subscription_id 与 user_id 不恒等（避免列相关性泄漏）",
          same < len(subs), f"{same}/{len(subs)} 行相同")

    # ---------------------------------------------------------------- 结论
    print()
    print("=" * 78)
    print("  校验结果")
    print("=" * 78)
    for desc, passed, detail in results:
        mark = OK if passed else FAIL
        print(f"{mark} {desc}")
        if detail:
            print(f"       {detail}")

    failed_count = sum(1 for _, p, _ in results if not p)
    print("=" * 78)
    if failed_count:
        print(f"{FAIL} {failed_count}/{len(results)} 条断言不成立。")
        print("     文档与数据不一致时，先改数据生成器 —— 不要让文档说谎。")
    else:
        print(f"{OK} 全部 {len(results)} 条断言通过，数据集可以放心用于评估与面试。")
    print("=" * 78)
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
