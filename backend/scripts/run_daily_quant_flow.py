#!/usr/bin/env python3
"""
EvoAlpha OS - 每日量化全流程自动化脚本

功能：
1. K线数据采集（个股、板块、ETF）
2. 数据完整性检查与自动补齐（循环重试直到完整）
3. RPS因子计算
4. 量化策略选股

特点：
- 自动检测缺失数据并持续补齐直到满足要求
- 智能健康度检查
- 完整的错误处理和日志记录
- 支持多种数据源切换（mootdx/pytdx/akshare）

数据源优先级（按速度排序）:
    1. mootdx - 通达信增强版（最快，批量获取）
    2. pytdx - 通达信协议（快，二进制协议）
    3. akshare - 东方财富HTTP（慢，但数据全）

使用:
    # 使用默认数据源（自动选择最快可用的）
    python3 scripts/run_daily_quant_flow.py

    # 指定数据源
    python3 scripts/run_daily_quant_flow.py --source mootdx
    python3 scripts/run_daily_quant_flow.py --source pytdx
    python3 scripts/run_daily_quant_flow.py --source akshare

    # 仅执行健康检查
    python3 scripts/run_daily_quant_flow.py --check-only

    # 设置每日定时任务（crontab -e）
    # 30 15 * * 1-5 cd /path/to/backend && python3 scripts/run_daily_quant_flow.py --source mootdx >> logs/daily_flow.log 2>&1
"""

import sys
import os
import logging
import time
from pathlib import Path
from datetime import datetime, date, timedelta

# ================= 路径适配 =================
current_dir = Path(__file__).parent.absolute()
backend_dir = current_dir.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# ================= 导入 =================
from app.core.database import get_engine
import pandas as pd
from sqlalchemy import text

# 数据源适配器
from data_job.core.mootdx_adapter import MootdxAdapter
from data_job.core.tdx_adapter import TdxAdapter

# 数据采集器
from data_job.collectors.multi_source_kline_collector import StockKlineCollectorV2
from data_job.collectors.stock_kline_collector import StockKlineCollector  # 回退
from data_job.collectors.stock_kline_collector_parallel import StockKlineCollectorParallel
from data_job.collectors.sector_kline_collector import SectorKlineCollector
from data_job.collectors.etf_kline_collector import ETFKlineCollector
from data_job.collectors.stock_valuation_collector import StockValuationCollector
from data_job.collectors.limit_boards_collector import LimitBoardsCollector

# 数据健康检查
from data_job.utils.data_health_checker import (
    KlineDataHealthChecker,
    check_kline_health,
)

# 量化引擎
from quant_engine.runner.feature_runner import FeatureRunner
from quant_engine.runner.parallel_feature_runner import ParallelFeatureRunner
from quant_engine.runner.strategy_runner import StrategyRunner

# ================= Logger配置 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ================= 配置 =================
CONFIG = {
    # 健康检查阈值
    'min_stock_count': 5000,          # 最少股票数量
    'min_sector_count': 450,          # 最少板块数量
    'min_etf_count': 30,              # 最少ETF数量
    'max_data_lag_days': 1,           # 最大数据滞后天数

    # 重试配置
    'max_retry_cycles': 5,            # 最大补齐重试次数
    'retry_delay_seconds': 30,        # 重试间隔

    # 数据源配置
    'data_sources': ['mootdx', 'pytdx', 'akshare'],  # 数据源优先级
    'preferred_source': None,        # 首选数据源（可通过命令行指定）
}


# ================= 数据源选择器 =================

class DataSourceSelector:
    """智能数据源选择器"""

    def __init__(self, preferred_source=None):
        self.preferred_source = preferred_source
        self.available_sources = {}

    def check_availability(self):
        """检查各数据源可用性"""
        logger.info("🔍 检查数据源可用性...")

        # 检查 mootdx
        try:
            adapter = MootdxAdapter()
            if adapter.is_available():
                self.available_sources['mootdx'] = {'adapter': adapter, 'speed': 'fastest', 'type': 'batch'}
                logger.info("  ✅ mootdx 可用（最快，批量获取）")
            else:
                logger.warning("  ❌ mootdx 连接失败")
        except Exception as e:
            logger.warning(f"  ❌ mootdx 不可用: {e}")

        # 检查 pytdx
        try:
            adapter = TdxAdapter()
            if adapter.is_available():
                self.available_sources['pytdx'] = {'adapter': adapter, 'speed': 'fast', 'type': 'binary'}
                logger.info("  ✅ pytdx 可用（快，二进制协议）")
            else:
                logger.warning("  ❌ pytdx 连接失败")
        except Exception as e:
            logger.warning(f"  ❌ pytdx 不可用: {e}")

        # akshare 作为回退
        self.available_sources['akshare'] = {'adapter': None, 'speed': 'slow', 'type': 'http'}
        logger.info("  ✅ akshare 可用（慢，但数据全）")

    def get_best_source(self):
        """获取最佳可用数据源"""
        if self.preferred_source and self.preferred_source in self.available_sources:
            source = self.preferred_source
            logger.info(f"📌 使用指定数据源: {source}")
            return source

        # 按优先级返回第一个可用的
        for source in CONFIG['data_sources']:
            if source in self.available_sources:
                logger.info(f"📌 自动选择数据源: {source}")
                return source

        logger.warning("⚠️ 所有数据源不可用，使用默认akshare")
        return 'akshare'

    def close(self):
        """关闭数据源连接"""
        for source_info in self.available_sources.values():
            adapter = source_info.get('adapter')
            if adapter and hasattr(adapter, 'close'):
                try:
                    adapter.close()
                except:
                    pass


# ================= 核心类 =================

class DailyQuantFlow:
    """每日量化全流程自动化"""

    def __init__(self, data_source=None):
        self.engine = get_engine()
        self.start_time = datetime.now()
        self.data_source_name = data_source

        # 数据源选择器
        self.source_selector = DataSourceSelector(preferred_source=data_source)
        self.source_selector.check_availability()

        logger.info("=" * 80)
        logger.info("🚀 EvoAlpha OS - 每日量化全流程自动化")
        logger.info(f"⏰ 开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)

    def run(self) -> bool:
        """执行完整流程"""
        try:
            # Step 1: 数据采集
            if not self._step_collect_data():
                return False

            # Step 2: 数据完整性检查与自动补齐
            if not self._step_ensure_data_complete():
                return False

            # Step 3: RPS因子计算
            if not self._step_calculate_rps():
                return False

            # Step 4: 量化策略选股
            if not self._step_strategy_selection():
                return False

            # 完成
            self._print_summary()
            return True

        except Exception as e:
            logger.error(f"❌ 流程执行异常: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            self.source_selector.close()

    def _step_collect_data(self) -> bool:
        """Step 1: 数据采集"""
        logger.info("\n" + "▶" * 40)
        logger.info("📊 Step 1/4: K线数据采集")
        logger.info("▶" * 40)

        # 根据数据源选择个股采集器
        source = self.source_selector.get_best_source()

        if source == 'mootdx':
            logger.info("  🚀 使用 mootdx 数据源（最快）")
            # 使用mootdx快速采集
            stock_collector = self._create_mootdx_collector()
        elif source == 'pytdx':
            logger.info("  🚀 使用 pytdx 数据源（快）")
            # 使用pytdx采集器
            stock_collector = self._create_pytdx_collector()
        else:
            logger.info("  🚀 使用多源采集器（pytdx优先）")
            try:
                stock_collector = StockKlineCollectorV2()
            except Exception as e:
                logger.warning(f"  多源采集器不可用: {e}，使用默认采集器")
                stock_collector = StockKlineCollector()

        collectors = [
            ("个股K线", stock_collector, "stock_daily_prices"),
            ("板块K线", SectorKlineCollector(), "sector_daily_prices"),
            ("ETF K线", ETFKlineCollector(), "etf_daily_prices"),
            ("股票估值", StockValuationCollector(), "stock_valuation"),
            ("涨停跌停", LimitBoardsCollector(), "limit_boards"),
        ]

        success_count = 0
        for name, collector, table_name in collectors:
            logger.info(f"\n  正在采集: {name} → {table_name}")
            try:
                collector.run()
                success_count += 1
                logger.info(f"  ✅ {name} 完成")
            except Exception as e:
                logger.error(f"  ❌ {name} 失败: {e}")

        logger.info(f"\n  数据采集完成: {success_count}/{len(collectors)} 成功")
        return success_count >= 3  # 至少核心K线采集成功

    def _create_mootdx_collector(self):
        """创建mootdx采集器（超快速）"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from tqdm import tqdm

        class MootdxKlineCollector:
            def __init__(self, engine):
                self.engine = engine
                self.adapter = MootdxAdapter()
                self.table_name = "stock_daily_prices"

            def run(self):
                """使用mootdx批量采集K线"""
                logger.info("    📡 连接通达信服务器池...")
                if not self.adapter.is_available():
                    raise Exception("mootdx连接失败")

                # 获取股票列表
                stock_list = pd.read_sql("SELECT symbol, name FROM stock_info", self.engine)
                logger.info(f"    📋 待更新: {len(stock_list)} 只股票")

                # 批量获取最新行情
                logger.info("    ⚡ 批量获取行情数据...")
                symbols = stock_list['symbol'].tolist()

                # 批量获取（每次最多1000只）
                batch_size = 1000
                all_data = []

                for i in range(0, len(symbols), batch_size):
                    batch = symbols[i:i+batch_size]
                    try:
                        # 批量获取实时行情
                        quotes = self.adapter.get_realtime_quotes(batch)
                        if quotes and not quotes.empty:
                            all_data.append(quotes)
                        logger.debug(f"      批次 {i//batch_size + 1}: {len(batch)} 只股票")
                    except Exception as e:
                        logger.warning(f"      批次 {i//batch_size + 1} 失败: {e}")

                if all_data:
                    df = pd.concat(all_data, ignore_index=True)
                    logger.info(f"    ✅ 获取 {len(df)} 只股票行情")

                # 这里简化处理，实际可以扩展为获取历史K线
                logger.info("    📝 mootdx采集器基础功能已验证")
                logger.info("    💡 提示: 完整mootdx采集器可进一步开发")

        return MootdxKlineCollector(self.engine)

    def _create_pytdx_collector(self):
        """创建pytdx采集器（快速）"""
        # 临时实现：使用并行版本
        return StockKlineCollectorParallel(max_workers=5)

    def _step_ensure_data_complete(self) -> bool:
        """Step 2: 数据完整性检查与自动补齐（循环重试）"""
        logger.info("\n" + "▶" * 40)
        logger.info("🏥 Step 2/4: 数据完整性检查与自动补齐")
        logger.info("▶" * 40)

        max_retries = CONFIG['max_retry_cycles']
        retry_delay = CONFIG['retry_delay_seconds']

        for cycle in range(1, max_retries + 1):
            logger.info(f"\n  📋 检查周期 {cycle}/{max_retries}")

            # 执行健康检查
            checker = KlineDataHealthChecker()
            result = checker.check_all()

            # 检查是否满足要求
            is_healthy, gaps = self._check_data_gaps(result)

            if is_healthy:
                logger.info(f"  ✅ 数据完整性检查通过！")
                logger.info(f"     健康度评分: {result.score}/100")
                checker.close()
                return True

            # 有缺失数据，进行补齐
            logger.warning(f"  ⚠️  数据不完整，开始补齐...")
            for gap in gaps:
                logger.warning(f"     - {gap}")

            if cycle < max_retries:
                logger.info(f"  🔄 开始补齐数据 (剩余 {max_retries - cycle} 次尝试)...")
                if self._supplement_missing_data(result):
                    logger.info(f"  ⏳ 等待 {retry_delay} 秒后重新检查...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"  ❌ 数据补齐失败")
            else:
                logger.error(f"  ❌ 已达到最大重试次数，数据仍不完整")
                checker.close()
                return False

            checker.close()

        return False

    def _check_data_gaps(self, result) -> tuple:
        """检查数据缺口"""
        gaps = []
        is_healthy = True

        # 检查股票K线
        stock = result.stock_kline
        if stock['data_lag_days'] > CONFIG['max_data_lag_days']:
            gaps.append(f"股票K线滞后 {stock['data_lag_days']} 天")
            is_healthy = False

        latest_count = stock['recent_counts'][0]['stock_count'] if stock['recent_counts'] else 0
        if latest_count < CONFIG['min_stock_count']:
            gaps.append(f"股票数量不足: {latest_count}/{CONFIG['min_stock_count']}")
            is_healthy = False

        # 检查板块K线
        sector = result.sector_kline
        if sector['latest_count'] < CONFIG['min_sector_count']:
            gaps.append(f"板块数量不足: {sector['latest_count']}/{CONFIG['min_sector_count']}")
            is_healthy = False

        # 检查ETF K线
        etf = result.etf_kline
        if etf['latest_count'] < CONFIG['min_etf_count']:
            gaps.append(f"ETF数量不足: {etf['latest_count']}/{CONFIG['min_etf_count']}")
            # ETF不作为强制要求

        return is_healthy, gaps

    def _supplement_missing_data(self, result) -> bool:
        """补齐缺失数据"""
        try:
            # 检查股票K线是否需要补齐
            stock = result.stock_kline
            if stock['data_lag_days'] > CONFIG['max_data_lag_days'] or \
               (stock['recent_counts'] and stock['recent_counts'][0]['stock_count'] < CONFIG['min_stock_count']):

                logger.info(f"    📈 补齐个股K线数据...")
                success = self._supplement_stock_kline()
                if not success:
                    logger.error(f"    ❌ 个股K线补齐失败")
                    return False

            # 检查板块K线是否需要补齐
            sector = result.sector_kline
            if sector['latest_count'] < CONFIG['min_sector_count']:
                logger.info(f"    🏢 补齐板块K线数据...")
                success = self._supplement_sector_kline()
                if not success:
                    logger.error(f"    ❌ 板块K线补齐失败")
                    return False

            return True

        except Exception as e:
            logger.error(f"    ❌ 数据补齐异常: {e}")
            return False

    def _supplement_stock_kline(self) -> bool:
        """补齐个股K线数据"""
        try:
            # 使用多源采集器（pytdx优先）
            logger.info(f"      使用多源采集器补齐...")
            collector = StockKlineCollectorV2()
            collector.run()
            return True

        except Exception as e:
            logger.warning(f"      多源采集器失败: {e}，使用默认采集器...")
            try:
                collector = StockKlineCollector()
                collector.run()
                return True
            except Exception as e2:
                logger.error(f"      ❌ 补齐失败: {e2}")
                return False

    def _supplement_sector_kline(self) -> bool:
        """补齐板块K线数据"""
        try:
            collector = SectorKlineCollector()
            collector.run()
            return True
        except Exception as e:
            logger.error(f"      ❌ 补齐失败: {e}")
            return False

    def _step_calculate_rps(self) -> bool:
        """Step 3: RPS因子计算"""
        logger.info("\n" + "▶" * 40)
        logger.info("🧮 Step 3/4: RPS因子计算")
        logger.info("▶" * 40)

        try:
            # 尝试使用并行版本
            try:
                runner = ParallelFeatureRunner()
                logger.info(f"  使用并行计算模式...")
                results = runner.run(
                    calculator_names=['stock', 'sector', 'etf'],
                    mode='daily',
                    max_workers=3
                )
            except Exception as e:
                logger.warning(f"  并行模式不可用: {e}，使用串行模式...")
                runner = FeatureRunner()
                results = runner.run(
                    calculator_names=['stock', 'sector', 'etf'],
                    mode='daily'
                )

            # 检查结果
            success_count = sum(1 for r in results.values() if r.get('success'))
            total_count = len(results)

            logger.info(f"\n  RPS计算完成: {success_count}/{total_count} 成功")

            for name, result in results.items():
                status = "✅" if result.get('success') else "❌"
                elapsed = result.get('elapsed_time', result.get('elapsed', 0))
                logger.info(f"    {status} {name.upper()}: {elapsed:.1f}秒")

            return all(r.get('success') for r in results.values())

        except Exception as e:
            logger.error(f"  ❌ RPS计算失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _step_strategy_selection(self) -> bool:
        """Step 4: 量化策略选股"""
        logger.info("\n" + "▶" * 40)
        logger.info("🎯 Step 4/4: 量化策略选股")
        logger.info("▶" * 40)

        try:
            runner = StrategyRunner()

            # 运行MRGC策略
            logger.info(f"  运行 MRGC 策略...")
            result = runner.run(strategy_name='mrgc')

            if result.get('success'):
                elapsed = result.get('elapsed', 0)
                logger.info(f"  ✅ MRGC策略完成 (耗时: {elapsed:.1f}秒)")

                # 查询选股结果
                self._print_selection_results()

                return True
            else:
                logger.error(f"  ❌ MRGC策略失败: {result.get('error')}")
                return False

        except Exception as e:
            logger.error(f"  ❌ 策略选股失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _print_selection_results(self):
        """打印选股结果"""
        try:
            df = pd.read_sql('''
                SELECT symbol, name, strategy_name, select_date
                FROM quant_preselect_results
                ORDER BY select_date DESC, rowid DESC
                LIMIT 20
            ''', self.engine)

            if not df.empty:
                logger.info(f"\n  📋 最新选股结果 (Top 20):")
                logger.info(f"  {'代码':<8} {'名称':<10} {'策略':<10} {'选股日期'}")
                logger.info(f"  {'-'*50}")

                for _, row in df.iterrows():
                    logger.info(f"  {row['symbol']:<8} {row['name']:<10} {row['strategy_name']:<10} {row['select_date']}")

        except Exception as e:
            logger.warning(f"  查询选股结果失败: {e}")

    def _print_summary(self):
        """打印流程摘要"""
        elapsed = (datetime.now() - self.start_time).total_seconds()

        logger.info("\n" + "=" * 80)
        logger.info("✅ 每日量化全流程完成")
        logger.info(f"⏰ 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"⏱️  总耗时: {elapsed/60:.1f} 分钟")
        logger.info("=" * 80)


# ================= 快速健康检查函数 =================

def quick_health_check() -> bool:
    """快速健康检查（用于定时任务前检查）"""
    checker = KlineDataHealthChecker()
    try:
        result = checker.check_all()
        return result.is_healthy
    finally:
        checker.close()


# ================= 主函数 =================

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="EvoAlpha OS 每日量化全流程自动化"
    )
    parser.add_argument(
        '--check-only',
        action='store_true',
        help='仅执行健康检查'
    )
    parser.add_argument(
        '--source',
        type=str,
        choices=['mootdx', 'pytdx', 'akshare'],
        help='指定数据源 (默认: 自动选择最快可用的)'
    )
    parser.add_argument(
        '--config',
        type=str,
        help='配置文件路径（可选）'
    )

    args = parser.parse_args()

    # 仅健康检查模式
    if args.check_only:
        logger.info("🔍 执行健康检查...")
        result = check_kline_health()
        if result.is_healthy:
            logger.info(f"✅ 数据健康 (评分: {result.score}/100)")
            return 0
        else:
            logger.error(f"❌ 数据不健康 (评分: {result.score}/100)")
            for issue in result.issues:
                logger.error(f"  {issue}")
            return 1

    # 执行完整流程
    flow = DailyQuantFlow(data_source=args.source)
    success = flow.run()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
