#!/usr/bin/env python3
"""
使用 Clash 代理轮询采集股票数据
自动测速 + 数据清洗 + 保存到数据库
"""

import sys
from pathlib import Path

# 添加路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from datetime import datetime, timedelta
import logging
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """主函数"""
    from data_job.collectors.clash_proxy_collector import ClashAKShareCollector
    from app.core.database import get_engine
    from sqlalchemy import text

    print("=" * 80)
    print("🚀 使用 Clash 代理采集股票数据")
    print("=" * 80)

    # 配置参数
    CONFIG = {
        'base_port': 42000,
        'proxy_count': 52,
        'min_delay': 0.5,
        'max_delay': 1.5,
        'max_retries': 15,
        'max_latency_ms': 1000,  # 超过 1 秒的代理将被过滤
        'auto_speed_test': True,  # 启动时自动测速
        'days_back': 10,  # 采集最近 N 天的数据
        'batch_size': 50,  # 每批采集的股票数量
    }

    logger.info(f"配置参数:")
    logger.info(f"  代理端口：42000-42051 (共 52 个)")
    logger.info(f"  延迟设置：{CONFIG['min_delay']}-{CONFIG['max_delay']}秒")
    logger.info(f"  最大重试：{CONFIG['max_retries']}次")
    logger.info(f"  最大延迟：≤{CONFIG['max_latency_ms']}ms")
    logger.info(f"  自动测速：是")
    logger.info(f"  采集天数：最近{CONFIG['days_back']}天")

    # 创建采集器
    logger.info("\n初始化采集器...")
    collector = ClashAKShareCollector(
        base_port=CONFIG['base_port'],
        proxy_count=CONFIG['proxy_count'],
        min_delay=CONFIG['min_delay'],
        max_delay=CONFIG['max_delay'],
        max_retries=CONFIG['max_retries'],
        max_latency_ms=CONFIG['max_latency_ms'],
        auto_speed_test=CONFIG['auto_speed_test'],
    )

    # 获取股票列表
    logger.info("\n获取股票列表...")
    engine = get_engine()
    with engine.connect() as conn:
        query = text("""
            SELECT DISTINCT symbol FROM quant_stock_pool
            WHERE pool_name = 'core_pool' AND is_active = 1
            ORDER BY symbol
            LIMIT :limit
        """)
        result = conn.execute(query, {"limit": CONFIG['batch_size']})
        symbols = [row[0] for row in result]

    logger.info(f"需要采集 {len(symbols)} 只股票")

    # 日期范围
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=CONFIG['days_back'])).strftime('%Y%m%d')
    logger.info(f"日期范围：{start_date} ~ {end_date}")

    # 开始采集
    logger.info("\n开始采集数据...")
    results = collector.collect_batch(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        adjust='qfq',
        progress_callback=lambda sym, success: logger.info(f"  {sym}: {'✅' if success else '❌'}")
    )

    # 保存数据到数据库
    logger.info("\n保存数据到数据库...")
    success_count = 0
    failed_count = 0

    with engine.connect() as conn:
        for symbol, df in results.items():
            if df is not None and not df.empty:
                try:
                    # 使用 SQLAlchemy 插入数据
                    for _, row in df.iterrows():
                        # 检查是否已存在
                        check_query = text("""
                            SELECT 1 FROM stock_daily_prices
                            WHERE symbol = :symbol AND trade_date = :trade_date
                        """)
                        exists = conn.execute(check_query, {
                            'symbol': row['symbol'],
                            'trade_date': row['trade_date']
                        }).first()

                        if exists:
                            # 更新现有记录
                            update_query = text("""
                                UPDATE stock_daily_prices
                                SET open = :open, high = :high, low = :low, close = :close,
                                    volume = :volume, amount = :amount
                                WHERE symbol = :symbol AND trade_date = :trade_date
                            """)
                            conn.execute(update_query, {
                                'symbol': row['symbol'],
                                'trade_date': row['trade_date'],
                                'open': row['open'],
                                'high': row['high'],
                                'low': row['low'],
                                'close': row['close'],
                                'volume': row['volume'],
                                'amount': row['amount']
                            })
                        else:
                            # 插入新记录
                            insert_query = text("""
                                INSERT INTO stock_daily_prices
                                (symbol, trade_date, open, high, low, close, volume, amount)
                                VALUES
                                (:symbol, :trade_date, :open, :high, :low, :close, :volume, :amount)
                            """)
                            conn.execute(insert_query, {
                                'symbol': row['symbol'],
                                'trade_date': row['trade_date'],
                                'open': row['open'],
                                'high': row['high'],
                                'low': row['low'],
                                'close': row['close'],
                                'volume': row['volume'],
                                'amount': row['amount']
                            })

                    success_count += 1
                except Exception as e:
                    logger.error(f"保存 {symbol} 失败：{e}")
                    failed_count += 1

        conn.commit()

    logger.info(f"\n✅ 数据保存完成!")
    logger.info(f"  成功保存：{success_count} 只股票")
    logger.info(f"  保存失败：{failed_count} 只股票")

    # 最终报告
    logger.info("\n" + "=" * 80)
    logger.info("📊 采集报告")
    logger.info("=" * 80)
    logger.info(f"采集股票：{len(symbols)} 只")
    logger.info(f"成功采集：{sum(1 for v in results.values() if v is not None)} 只")
    logger.info(f"成功保存：{success_count} 只")
    logger.info(f"日期范围：{start_date} ~ {end_date}")

    proxy_stats = collector.rotator.get_stats()
    logger.info(f"快速代理：{proxy_stats['fast_available']} 个")
    logger.info(f"慢速过滤：{proxy_stats['slow_filtered']} 个")
    logger.info(f"代理切换：{collector.stats['proxy_switches']} 次")
    logger.info("=" * 80)

    logger.info("\n下一步操作:")
    logger.info("  1. 验证数据：python3 scripts/check_db_data.py")
    logger.info("  2. 计算 RPS: python3 -m quant_engine.runner.feature_runner --mode daily")
    logger.info("  3. 运行策略：python3 -m quant_engine.runner.strategy_runner --strategy mrgc")


if __name__ == "__main__":
    main()
