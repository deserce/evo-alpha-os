"""
为 EvoAlpha OS 数据库添加性能优化索引

运行方式:
    cd backend && python scripts/add_performance_indexes.py

功能:
    为高频查询表添加缺失的复合索引，显著加速 JOIN 和 WHERE 查询
"""

import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "local_quant.db"

# PRIMARY KEY/UNIQUE auto-indexes already cover the same key order.  Keep only
# indexes that add a query shape or sort direction; storage maintenance removes
# any legacy copies left by older versions of this script.
INDEXES = [
    # index_daily — Dashboard 指数数据（之前完全无索引！）
    ("idx_index_code_date", "index_daily", "index_code, trade_date DESC",
     "加速指数 K 线查询，之前完全无索引"),

    # timing_signals_daily — 信号查询
    ("idx_signals_date_symbol", "timing_signals_daily", "trade_date, symbol",
     "加速按日期+代码查询买点信号"),

    # trade_plans — 交易计划
    ("idx_plans_date_symbol", "trade_plans", "trade_date, symbol",
     "加速按日期+代码查询交易计划"),

    # quant_feature_rps — RPS 因子表
    ("idx_rps_date_symbol", "quant_feature_rps", "trade_date, symbol",
     "加速按日期+代码查询 RPS 因子"),

    # stock_daily_prices — 个股日 K（数据量最大的表）
    ("idx_prices_date_symbol", "stock_daily_prices", "trade_date, symbol",
     "加速按日期+代码查询个股价格"),

    # research_stock_daily_snapshot — 研究池快照
    ("idx_research_date_theme", "research_stock_daily_snapshot", "trade_date, theme_name",
     "加速按日期+主题查询研究池快照"),
]


def main():
    if not DB_PATH.exists():
        print(f"[ERROR] 数据库不存在: {DB_PATH}")
        sys.exit(1)

    db_size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"[INFO] 数据库: {DB_PATH}")
    print(f"[INFO] 大小: {db_size_mb:.0f} MB")
    print()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    existing_indexes = set()
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    for row in rows:
        existing_indexes.add(row["name"])

    created = 0
    skipped = 0
    failed = 0

    for index_name, table_name, columns, description in INDEXES:
        if index_name in existing_indexes:
            print(f"  [SKIP] {index_name} 已存在 — {description}")
            skipped += 1
            continue

        table_check = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()

        if not table_check:
            print(f"  [SKIP] {index_name} — 表 {table_name} 不存在")
            skipped += 1
            continue

        sql = f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns})"

        try:
            t0 = time.time()
            conn.execute(sql)
            conn.commit()
            elapsed = time.time() - t0
            print(f"  [OK]   {index_name} on {table_name}({columns}) — {elapsed:.1f}s — {description}")
            created += 1
        except Exception as exc:
            print(f"  [FAIL] {index_name} — {exc}")
            failed += 1

    conn.close()

    print()
    print(f"[DONE] 创建: {created}  跳过: {skipped}  失败: {failed}")


if __name__ == "__main__":
    main()
