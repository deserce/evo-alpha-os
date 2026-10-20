"""
EvoAlpha OS - 市场/行业强度计算器

正式封装 L0/L1 强度计算，复用现有 scripts/calculate_strength_features.py
中的计算口径，供量化 runner 和每日生产入口统一调用。
"""

import sqlite3
import sys
import time
import importlib.util
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from quant_engine.common import setup_logger
from quant_engine.utils.calculation_dates import resolve_dense_output_dates


STRENGTH_SCRIPT = BACKEND_DIR / "scripts" / "calculate_strength_features.py"
spec = importlib.util.spec_from_file_location("calculate_strength_features", STRENGTH_SCRIPT)
if spec is None or spec.loader is None:
    raise ImportError(f"无法加载强度计算脚本: {STRENGTH_SCRIPT}")
strength_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strength_module)

DB_PATH = strength_module.DB_PATH
calculate_industry_strength = strength_module.calculate_industry_strength
calculate_market_strength = strength_module.calculate_market_strength
create_tables = strength_module.create_tables
get_load_start = strength_module.get_load_start
get_output_dates = strength_module.get_output_dates
save_frame = strength_module.save_frame


logger = setup_logger(__name__)


class StrengthCalculator:
    """市场强度 M0-M4 与行业强度 I0-I4 计算器。"""

    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.target_tables = ("market_strength_daily", "industry_strength_daily")

    def run_daily(
        self,
        days: int = 3,
        target_dates: list[str] | None = None,
        missing_only: bool = False,
    ) -> dict:
        return self.run(
            mode="daily",
            days=days,
            target_dates=target_dates,
            missing_only=missing_only,
        )

    def run_init(self, days: int = 3) -> dict:
        return self.run(mode="init", days=days)

    def run(
        self,
        mode: str = "daily",
        days: int = 3,
        target_dates: list[str] | None = None,
        missing_only: bool = False,
    ) -> dict:
        start_time = time.time()
        if mode not in {"daily", "init"}:
            raise ValueError(f"未知运行模式: {mode}")

        if not self.db_path.exists():
            return {"success": False, "error": f"数据库不存在: {self.db_path}", "records": 0}

        logger.info("=" * 80)
        logger.info(f"🚀 [StrengthCalculator] 启动强度计算: mode={mode}")
        logger.info("=" * 80)

        try:
            with sqlite3.connect(self.db_path) as conn:
                create_tables(conn)
                output_dates = resolve_dense_output_dates(
                    conn,
                    target_dates=target_dates,
                    fallback=lambda: get_output_dates(conn, mode, days),
                    target_tables=self.target_tables,
                    missing_only=missing_only,
                )
                load_start = get_load_start(output_dates)
                if not output_dates and target_dates:
                    return {"success": True, "records": 0, "elapsed": time.time() - start_time}
                if not output_dates or load_start is None:
                    return {"success": False, "error": "stock_daily_prices 没有交易日期", "records": 0}

                logger.info(
                    "📅 强度计算窗口: %s..%s, load_start=%s",
                    output_dates[0],
                    output_dates[-1],
                    load_start,
                )
                market_df = calculate_market_strength(conn, output_dates, load_start)
                industry_df = calculate_industry_strength(conn, output_dates, load_start)
                save_frame(conn, "market_strength_daily", market_df, "index_code")
                save_frame(conn, "industry_strength_daily", industry_df, "sector_name")

            records = len(market_df) + len(industry_df)
            elapsed = time.time() - start_time
            logger.info(f"✅ 强度计算完成: records={records}, elapsed={elapsed:.1f}s")
            return {"success": True, "records": records, "elapsed": elapsed}
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ 强度计算失败: {e}")
            return {"success": False, "error": str(e), "records": 0, "elapsed": elapsed}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EvoAlpha 市场/行业强度计算器")
    parser.add_argument("--mode", choices=("daily", "init"), default="daily")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    result = StrengthCalculator().run(mode=args.mode, days=args.days)
    raise SystemExit(0 if result.get("success") else 1)
