"""
量化引擎 - 策略运行器
支持指定日期运行策略选股
"""
import sys
import os
import time
import argparse
from datetime import datetime, date
from sqlalchemy import text

# 环境路径适配
current_dir = os.path.dirname(os.path.abspath(__file__))
quant_engine_dir = os.path.dirname(current_dir)
backend_dir = os.path.dirname(quant_engine_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from quant_engine.common import setup_quant_path, setup_logger
from quant_engine.strategies.mrgc_strategy import MrgcStrategy
from app.core.database import get_engine

# 路径初始化
setup_quant_path()

# Logger配置
logger = setup_logger(__name__)


def ensure_strategy_run_log_table(engine) -> None:
    """Create the lightweight strategy audit table."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS strategy_run_log (
                run_id TEXT PRIMARY KEY,
                strategy_name TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                status TEXT NOT NULL,
                signal_count INTEGER DEFAULT 0,
                error_message TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_strategy_run_log_lookup "
            "ON strategy_run_log (strategy_name, trade_date, finished_at)"
        ))


def record_strategy_run(
    engine,
    *,
    run_id: str,
    strategy_name: str,
    trade_date: str,
    status: str,
    signal_count: int,
    error_message: str | None,
    started_at: str,
    finished_at: str,
) -> None:
    """Persist one strategy run audit row."""
    ensure_strategy_run_log_table(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT OR REPLACE INTO strategy_run_log (
                run_id, strategy_name, trade_date, status, signal_count,
                error_message, started_at, finished_at
            ) VALUES (
                :run_id, :strategy_name, :trade_date, :status, :signal_count,
                :error_message, :started_at, :finished_at
            )
        """), {
            "run_id": run_id,
            "strategy_name": strategy_name,
            "trade_date": trade_date,
            "status": status,
            "signal_count": int(signal_count),
            "error_message": error_message,
            "started_at": started_at,
            "finished_at": finished_at,
        })


# ================= 策略注册表 =================
STRATEGY_REGISTRY = {
    'mrgc': MrgcStrategy,
    # 未来可以在这里添加更多策略
    # 'oversold': OversoldStrategy,
    # 'breakout': BreakoutStrategy,
}
STRATEGY_AUDIT_NAMES = {
    "mrgc": "mrgc_v1",
}


class StrategyRunner:
    """策略运行器"""

    def __init__(self):
        """初始化运行器"""
        self.strategies = {}

    def has_successful_run(self, strategy_name: str, trade_date: str) -> bool:
        """Return whether a strategy date already has a successful audit row."""
        audit_name = STRATEGY_AUDIT_NAMES.get(strategy_name)
        if not audit_name:
            return False
        engine = get_engine()
        ensure_strategy_run_log_table(engine)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT 1 FROM strategy_run_log
                    WHERE strategy_name = :strategy_name
                      AND trade_date >= :trade_date
                      AND trade_date < DATE(:trade_date, '+1 day')
                      AND status = 'success'
                    LIMIT 1
                    """
                ),
                {"strategy_name": audit_name, "trade_date": trade_date},
            ).fetchone()
        return row is not None

    def run_many(
        self,
        strategy_name: str,
        trade_dates: list[str],
        *,
        missing_only: bool = False,
    ) -> dict:
        """Run one strategy for explicit dates, optionally skipping completed dates."""
        dates = sorted({str(value)[:10] for value in trade_dates if value})
        if not dates:
            return {"success": False, "error": "未提供有效交易日期", "dates": {}}

        results: dict[str, dict] = {}
        for trade_date in dates:
            if missing_only and self.has_successful_run(strategy_name, trade_date):
                logger.info("⏭️ 策略日期已完成，跳过: %s %s", strategy_name, trade_date)
                results[trade_date] = {"success": True, "skipped": True}
                continue
            results[trade_date] = self.run(strategy_name, trade_date=trade_date)

        return {
            "success": all(item.get("success") for item in results.values()),
            "dates": results,
        }

    def run(self, strategy_name, trade_date=None):
        """
        运行指定的策略

        Args:
            strategy_name: 策略名称，如 'mrgc'
            trade_date: 交易日期 (YYYY-MM-DD)，None表示最新交易日

        Returns:
            dict: 运行结果
        """
        # 验证策略名称
        if strategy_name not in STRATEGY_REGISTRY:
            logger.error(f"❌ 未找到策略: {strategy_name}")
            logger.info(f"   可用策略: {list(STRATEGY_REGISTRY.keys())}")
            return {'success': False, 'error': f'策略不存在: {strategy_name}'}

        # 确定运行日期
        if trade_date is None:
            trade_date = self._get_latest_trade_date()
            logger.info(f"📅 使用最新交易日: {trade_date}")
        else:
            # 验证日期格式
            try:
                datetime.strptime(trade_date, '%Y-%m-%d')
            except ValueError:
                logger.error(f"❌ 日期格式错误: {trade_date}，应为 YYYY-MM-DD")
                return {'success': False, 'error': '日期格式错误'}

        # 获取策略实例
        StrategyClass = STRATEGY_REGISTRY[strategy_name]
        strategy = StrategyClass()
        audit_engine = getattr(strategy, "engine", None) or get_engine()
        audit_run_id = (
            f"strategy_{strategy.strategy_name}_{str(trade_date)[:10]}_"
            f"{datetime.now():%Y%m%d_%H%M%S}"
        )

        # 执行策略
        start_time = time.time()
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        logger.info("=" * 80)
        logger.info(f"🚀 开始执行策略选股")
        logger.info(f"📋 策略名称: {strategy.strategy_name}")
        logger.info(f"📅 选股日期: {trade_date}")
        logger.info("=" * 80)

        try:
            signal_count = strategy.run(trade_date=trade_date)
            signal_count = int(signal_count or 0)

            elapsed = time.time() - start_time
            record_strategy_run(
                audit_engine,
                run_id=audit_run_id,
                strategy_name=strategy.strategy_name,
                trade_date=str(trade_date)[:10],
                status="success",
                signal_count=signal_count,
                error_message=None,
                started_at=started_at,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            logger.info(f"\n✅ 策略执行完成！耗时: {elapsed:.1f}秒")

            return {'success': True, 'elapsed': elapsed, 'signal_count': signal_count}

        except Exception as e:
            elapsed = time.time() - start_time
            record_strategy_run(
                audit_engine,
                run_id=audit_run_id,
                strategy_name=strategy.strategy_name,
                trade_date=str(trade_date)[:10],
                status="failed",
                signal_count=0,
                error_message=str(e),
                started_at=started_at,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            logger.error(f"\n❌ 策略执行失败: {e}")
            import traceback
            traceback.print_exc()

            return {'success': False, 'error': str(e), 'elapsed': elapsed}

    def _get_latest_trade_date(self):
        """获取数据库中最新的交易日期"""
        from app.core.database import get_engine
        from sqlalchemy import text

        try:
            engine = get_engine()
            with engine.connect() as conn:
                # 从K线表查询最新日期
                query = text("SELECT MAX(trade_date) FROM stock_daily_prices")
                latest_date = conn.execute(query).scalar()
                return str(latest_date)
        except Exception as e:
            logger.warning(f"⚠️ 无法获取最新日期，使用今天: {e}")
            return str(date.today())


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="EvoAlpha 量化引擎 - 策略选股运行器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 运行MRGC策略（使用最新交易日）
  python -m quant_engine.runner.strategy_runner --strategy mrgc

  # 运行MRGC策略（指定日期）
  python -m quant_engine.runner.strategy_runner --strategy mrgc --date 2026-01-19

  # 列出所有可用策略
  python -m quant_engine.runner.strategy_runner --list

可用的策略:
  mrgc    - 陶博士每日观察（MRGC + SXHCG）
        """
    )

    parser.add_argument(
        '--strategy', '-s',
        type=str,
        help='策略名称（必需）'
    )

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        '--date', '-d',
        type=str,
        help='选股日期 (YYYY-MM-DD)，默认为最新交易日'
    )
    date_group.add_argument(
        '--dates',
        nargs='+',
        help='明确指定一个或多个待选股日期',
    )
    parser.add_argument(
        '--missing-only',
        action='store_true',
        help='与 --dates 配合，仅运行尚无成功审计记录的日期',
    )

    parser.add_argument(
        '--list', '-l',
        action='store_true',
        help='列出所有可用策略'
    )

    args = parser.parse_args()

    # 列出策略
    if args.list:
        print("📋 可用策略列表:")
        for key, StrategyClass in STRATEGY_REGISTRY.items():
            # 实例化以获取策略名称
            strategy = StrategyClass()
            print(f"   - {key}: {strategy.strategy_name}")
        return 0

    # 验证必需参数
    if not args.strategy:
        parser.error("需要指定 --strategy 参数，或使用 --list 查看可用策略")

    # 运行策略
    runner = StrategyRunner()
    if args.dates:
        results = runner.run_many(
            strategy_name=args.strategy,
            trade_dates=args.dates,
            missing_only=args.missing_only,
        )
    else:
        results = runner.run(strategy_name=args.strategy, trade_date=args.date)

    # 返回退出码
    return 0 if results.get('success') else 1


if __name__ == "__main__":
    exit(main())
