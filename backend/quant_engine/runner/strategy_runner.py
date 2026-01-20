"""
量化引擎 - 策略运行器
支持指定日期运行策略选股
"""
import sys
import os
import time
import argparse
from datetime import datetime, date

# 环境路径适配
current_dir = os.path.dirname(os.path.abspath(__file__))
quant_engine_dir = os.path.dirname(current_dir)
backend_dir = os.path.dirname(quant_engine_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from quant_engine.common import setup_quant_path, setup_logger
from quant_engine.strategies.mrgc_strategy import MrgcStrategy

# 路径初始化
setup_quant_path()

# Logger配置
logger = setup_logger(__name__)


# ================= 策略注册表 =================
STRATEGY_REGISTRY = {
    'mrgc': MrgcStrategy,
    # 未来可以在这里添加更多策略
    # 'oversold': OversoldStrategy,
    # 'breakout': BreakoutStrategy,
}


class StrategyRunner:
    """策略运行器"""

    def __init__(self):
        """初始化运行器"""
        self.strategies = {}

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

        # 执行策略
        start_time = time.time()

        logger.info("=" * 80)
        logger.info(f"🚀 开始执行策略选股")
        logger.info(f"📋 策略名称: {strategy.strategy_name}")
        logger.info(f"📅 选股日期: {trade_date}")
        logger.info("=" * 80)

        try:
            strategy.run(trade_date=trade_date)

            elapsed = time.time() - start_time
            logger.info(f"\n✅ 策略执行完成！耗时: {elapsed:.1f}秒")

            return {'success': True, 'elapsed': elapsed}

        except Exception as e:
            elapsed = time.time() - start_time
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

    parser.add_argument(
        '--date', '-d',
        type=str,
        help='选股日期 (YYYY-MM-DD)，默认为最新交易日'
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
    results = runner.run(strategy_name=args.strategy, trade_date=args.date)

    # 返回退出码
    return 0 if results.get('success') else 1


if __name__ == "__main__":
    exit(main())
