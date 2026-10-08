#!/usr/bin/env bash
# ============================================================
# 一键导入自造数据集到 Postgres
# ============================================================
# 用法：
#   ./scripts/load_to_postgres.sh            # 默认库名 saas_lab
#   ./scripts/load_to_postgres.sh my_db      # 指定库名
#
# 前置条件：本地 Postgres 已启动，且 DATABASE 已创建（脚本会自动尝试 createdb）
# ============================================================
set -euo pipefail

DB="${1:-saas_lab}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/data/out"

if [ ! -f "$OUT/tenants.csv" ]; then
  echo "[!] 未找到数据文件，请先运行："
  echo "    python3 scripts/generate_saas_data.py"
  exit 1
fi

echo "[1/3] 确保数据库存在: $DB"
createdb "$DB" 2>/dev/null || echo "     (已存在或无需创建)"

echo "[2/3] 建表"
psql -q -d "$DB" -f "$ROOT/data/schema.sql"

echo "[3/3] 导入 CSV（顺序受外键约束，不能打乱）"
psql -q -d "$DB" -c "TRUNCATE tenants, users, subscriptions, events, payments RESTART IDENTITY CASCADE;"
for t in tenants users subscriptions events payments; do
  printf '     %-14s' "$t"
  psql -q -d "$DB" -c "\copy $t FROM '$OUT/$t.csv' WITH (FORMAT csv, HEADER true)"
  psql -tA -d "$DB" -c "SELECT '→ ' || COUNT(*) || ' 行' FROM $t;"
done

echo
echo "[OK] 导入完成。试一下（时间窗口是固定的，不要用 now()）："
echo "     psql -d $DB -c \"SELECT COUNT(DISTINCT user_id) FROM events"
echo "       WHERE event_type='login'"
echo "         AND occurred_at >= '2026-01-02 00:00:00+00'"
echo "         AND occurred_at <  '2026-02-01 00:00:00+00';\""
