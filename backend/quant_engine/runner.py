# backend/quant_engine/runner.py

import argparse
import sys
import os
from datetime import date
from sqlalchemy import text

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# ================= 1. 导入策略类 =================
# 注意：这里导入的是类 (MrgcStrategy)，而不是函数
from .strategies.mrgc_strategy import MrgcStrategy

# ================= 2. 策略注册表 =================
# 格式: "策略名称": 策略类
STRATEGY_REGISTRY = {
    "MRGC_SXHCG": MrgcStrategy,
    # "OVERSOLD": OversoldStrategy, # 以后加新策略写在这里
}

# ================= 数据库配置 (统一使用 get_engine) =================
# 注意：具体的策略执行会使用策略类内部的数据库连接，这里仅用于获取日期
from app.core.database import get_engine

def get_latest_trade_date():
    """获取数据库中最新的交易日期"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            # 从日线表查最新的日期
            query = text("SELECT MAX(trade_date) FROM stock_daily_prices")
            latest_date = conn.execute(query).scalar()
            return str(latest_date)
    except Exception as e:
        print(f"⚠️ 无法获取最新日期，默认使用今天: {e}")
        return str(date.today())

def parse_arguments():
    parser = argparse.ArgumentParser(description="EvoAlpha 策略调度器")
    parser.add_argument(
        '--strategy', '-s', 
        type=str, 
        help='指定运行的策略名称 (例如: MRGC_SXHCG)'
    )
    parser.add_argument(
        '--date', '-d',
        type=str,
        help='指定回测日期 (格式 YYYY-MM-DD)，不填则默认为数据库最新交易日'
    )
    parser.add_argument(
        '--list', '-l', 
        action='store_true', 
        help='列出所有可用策略'
    )
    return parser.parse_args()

def run():
    args = parse_arguments()

    # 1. 列出策略
    if args.list:
        print("📋 可用策略列表:")
        for name in STRATEGY_REGISTRY.keys():
            print(f"   - {name}")
        return

    # 2. 确定运行日期
    target_date = args.date if args.date else get_latest_trade_date()
    print(f"📅 运行目标日期: {target_date}")

    # 3. 确定要运行的策略列表
    strategies_to_run = []
    
    if args.strategy:
        if args.strategy in STRATEGY_REGISTRY:
            strategies_to_run.append(STRATEGY_REGISTRY[args.strategy])
        else:
            print(f"❌ 错误: 未找到策略 '{args.strategy}'")
            return
    else:
        # 没指定则运行所有
        print("🚀 未指定策略，将运行所有注册策略...")
        strategies_to_run = list(STRATEGY_REGISTRY.values())

    # 4. 依次执行策略
    for StrategyClass in strategies_to_run:
        try:
            # 实例化策略对象
            strategy_instance = StrategyClass()
            print(f"\n▶️ 启动策略: {strategy_instance.strategy_name} ...")
            
            # 调用策略类的 run 方法，让它自己去数据库取数、计算、存库
            strategy_instance.run(trade_date=target_date)
            
        except Exception as e:
            print(f"❌ 策略执行异常: {e}")
            import traceback
            traceback.print_exc()

    print("\n🏁 所有任务执行完毕。")

if __name__ == "__main__":
    run()