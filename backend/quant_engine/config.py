import pandas as pd
import json
import argparse  # 引入参数解析库
from sqlalchemy import create_engine, text
import time
from datetime import datetime
from collections import defaultdict

# ================= 1. 引入你的策略 =================
from .strategies.mrgc_strategy import check_mrgc_signal
# from .strategies.oversold_strategy import check_oversold_signal (如果你创建了就解开注释)

# ================= 2. 注册策略配置 =================
# 在这里管理你的策略库
STRATEGY_REGISTRY = {
    "MRGC_SXHCG": check_mrgc_signal,
    # "OVERSOLD_BOUNCE": check_oversold_signal, 
    # "NEW_STRATEGY": check_new_signal
}

# ================= 配置区域 =================
# ⚠️ 注意：这个文件已被废弃，请使用 quant_engine/runner.py
# 为了兼容性，保留此文件但不再维护

import sys
import os

# 环境路径适配
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# 使用统一的数据库配置
from app.core.database import get_engine
from sqlalchemy import create_engine, text

# 兼容旧代码：提供 DB_URL 变量（已废弃）
engine = get_engine()
DB_URL = str(engine.url)
# ===========================================

def get_latest_trade_date(engine):
    query = "SELECT MAX(trade_date) FROM stock_daily_prices"
    with engine.connect() as conn:
        date = conn.execute(text(query)).scalar()
    return date

def save_results_to_db(engine, all_results, trade_date):
    if not all_results:
        return

    grouped_results = defaultdict(list)
    for item in all_results:
        grouped_results[item['strategy_name']].append(item)

    print(f"\n💾 正在保存结果到数据库...")
    
    with engine.begin() as conn:
        for strat_name, items in grouped_results.items():
            db_rows = []
            for item in items:
                meta_data_pack = {
                    "rps_250": item.get('rps_250'),
                    "pool_reason": item.get('pool_reason'),
                    "strategy_reason": item.get('strategy_reason')
                }
                
                db_rows.append({
                    "trade_date": trade_date,
                    "strategy_name": strat_name,
                    "code": item['code'],
                    "pool_name": "core_pool",
                    "meta_data": json.dumps(meta_data_pack)
                })
            
            df = pd.DataFrame(db_rows)
            
            # 删除旧记录
            conn.execute(text(f"""
                DELETE FROM quant_strategy_results 
                WHERE trade_date = '{trade_date}' 
                AND strategy_name = '{strat_name}'
            """))
            
            df.to_sql('quant_strategy_results', conn, if_exists='append', index=False, method='multi')
            print(f"   ✅ [{strat_name}] 保存 {len(df)} 条")

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="EvoQuant 选股执行器")
    
    # 添加 --strategy 参数
    parser.add_argument(
        '--strategy', '-s', 
        type=str, 
        help='指定运行的策略名称 (例如: MRGC_SXHCG)。如果不填，则默认运行所有策略。'
    )
    
    # 添加 --list 参数
    parser.add_argument(
        '--list', '-l', 
        action='store_true', 
        help='列出当前所有可用的策略名称'
    )
    
    return parser.parse_args()

def run():
    # 1. 解析用户指令
    args = parse_arguments()
    
    # 如果用户只是想看有哪些策略
    if args.list:
        print("📋 当前可用策略列表:")
        for name in STRATEGY_REGISTRY.keys():
            print(f"   - {name}")
        return

    # 确定要跑哪些策略
    target_strategies = {}
    
    if args.strategy:
        # 用户指定了策略
        strat_name = args.strategy
        if strat_name in STRATEGY_REGISTRY:
            target_strategies[strat_name] = STRATEGY_REGISTRY[strat_name]
            print(f"🎯 指定运行策略: [{strat_name}]")
        else:
            print(f"❌ 错误: 找不到名为 '{strat_name}' 的策略！")
            print(f"   可用策略: {list(STRATEGY_REGISTRY.keys())}")
            return
    else:
        # 用户没指定，默认跑所有
        target_strategies = STRATEGY_REGISTRY
        print(f"🚀 默认模式: 运行所有注册策略 ({len(target_strategies)}个)")

    engine = create_engine(DB_URL)
    target_date = get_latest_trade_date(engine)
    print(f"📅 选股基准日期: {target_date}")

    print("⏳ 正在拉取候选股票池...")
    # 这里根据需要，决定是否加上 RPS 过滤。如果是跑超跌策略，建议去掉 AND t1.rps_250 > 80
    sql_pool = f"""
    SELECT 
        t1.code, t1.rps_50, t1.rps_120, t1.rps_250, pool.reason as pool_reason 
    FROM quant_feature_rps t1
    INNER JOIN stock_pool_core pool ON t1.code::text = pool.code::text 
    WHERE t1.trade_date = '{target_date}' 
    """
    
    try:
        candidates = pd.read_sql(sql_pool, engine)
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return

    print(f"🔍 待扫描股票: {len(candidates)} 只")

    all_results = []
    start_time = time.time()
    
    # 遍历股票
    for i, row in candidates.iterrows():
        code = row['code']
        rps_dict = {
            'rps_50': row['rps_50'],
            'rps_120': row['rps_120'],
            'rps_250': row['rps_250']
        }
        
        # 拉取数据
        sql_daily = f"""
        SELECT open, close, high, low, volume, turnover_rate 
        FROM stock_daily_prices 
        WHERE code = '{code}' AND trade_date <= '{target_date}'
        ORDER BY trade_date DESC LIMIT 300
        """
        df = pd.read_sql(sql_daily, engine)
        if df.empty: continue
        df = df.iloc[::-1].reset_index(drop=True)
        
        # 🔥 只运行 target_strategies 里的策略
        for strat_name, strat_func in target_strategies.items():
            try:
                is_selected, reason = strat_func(df, rps_dict)
                if is_selected:
                    all_results.append({
                        'strategy_name': strat_name,
                        'code': code,
                        'rps_250': row['rps_250'],
                        'pool_reason': row['pool_reason'],
                        'strategy_reason': reason
                    })
            except Exception as e:
                pass # 忽略单次错误

        if i % 50 == 0:
            print(f"   进度: {i}/{len(candidates)}...", end='\r')

    cost = time.time() - start_time
    print(f"\n🏁 完成! 耗时 {cost:.1f}秒")
    
    if all_results:
        df_res = pd.DataFrame(all_results)
        print("\n📊 选中分布:")
        print(df_res['strategy_name'].value_counts())
        save_results_to_db(engine, all_results, target_date)
    else:
        print("🍃 未选中任何股票。")

if __name__ == "__main__":
    run()