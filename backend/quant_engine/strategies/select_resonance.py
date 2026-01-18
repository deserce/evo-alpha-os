# backend/quant_engine/strategies/select_resonance.py

import sys
import os
import pandas as pd
import json
from datetime import date
from sqlalchemy import text

# 路径适配
current_dir = os.path.dirname(os.path.abspath(__file__))
engine_root = os.path.abspath(os.path.join(current_dir, "../"))
if engine_root not in sys.path:
    sys.path.append(engine_root)

from strategies.base_strategy import BaseStrategy

class ResonanceStrategy(BaseStrategy):
    def __init__(self):
        # 策略ID: sector_resonance_v1
        super().__init__("sector_resonance_v1")
        
        # 参数配置
        self.SECTOR_RPS_THRESHOLD = 90  # 板块强度阈值
        self.STOCK_RPS_THRESHOLD = 85   # 个股强度阈值

    def get_strong_sectors(self, trade_date):
        """1. 找出当日 RPS > 90 的强势板块"""
        print(f"   📊 正在筛选强势板块 ({trade_date})...")
        
        # 我们用 quant_feature_sector_rps 表
        query = text(f"""
            SELECT sector_name, rps_20 
            FROM quant_feature_sector_rps 
            WHERE trade_date = '{trade_date}' 
              AND rps_20 > {self.SECTOR_RPS_THRESHOLD}
        """)
        
        df = pd.read_sql(query, self.engine)
        if df.empty:
            print("   ⚠️ 今日无强势板块 (RPS_20 > 90)")
            return []
            
        sectors = df['sector_name'].tolist()
        print(f"   ✅ 发现 {len(sectors)} 个强势板块: {sectors[:5]}...")
        return sectors

    def get_stocks_in_sectors(self, sectors):
        """2. 找出这些板块里的成分股"""
        if not sectors: return []
        
        sec_str = "'" + "','".join(sectors) + "'"
        
        # 关联 stock_sector_map 表
        # 注意：这里我们只选 'Industry' (行业) 还是 Industry+Concept (概念)？
        # 通常共振策略看行业更稳，概念更爆。这里全选。
        query = text(f"""
            SELECT DISTINCT symbol, sector_name
            FROM stock_sector_map
            WHERE sector_name IN ({sec_str})
        """)
        
        df = pd.read_sql(query, self.engine)
        return df

    def run(self, trade_date=None):
        if not trade_date:
            trade_date = str(date.today())
            
        print(f"🚀 正在执行策略 [{self.strategy_name}] 日期: {trade_date}")

        # --- 第一步：找板块 ---
        strong_sectors = self.get_strong_sectors(trade_date)
        if not strong_sectors: return

        # --- 第二步：找成分股 ---
        df_map = self.get_stocks_in_sectors(strong_sectors)
        if df_map.empty: return
        
        candidate_symbols = df_map['symbol'].unique().tolist()
        print(f"   🔍 涉及成分股 {len(candidate_symbols)} 只，准备通过 RPS 过滤...")

        # --- 第三步：找强势股 (利用基类方法) ---
        # 这里的 candidate_symbols 可能很多，如果超过 2000 只，get_daily_features 可能会慢
        # 但既然是强势板块，通常数量可控。
        
        rps_df = self.get_daily_features(trade_date, candidate_symbols)
        if rps_df.empty: return

        # --- 第四步：双重筛选 (板块强 + 个股强) ---
        results = []
        
        # 为了输出“属于哪个板块”，我们做个映射字典
        # 一个股票可能属于多个强势板块，我们取其中一个或join
        stock_to_sector = df_map.groupby('symbol')['sector_name'].apply(lambda x: ','.join(x)).to_dict()

        for _, row in rps_df.iterrows():
            symbol = row['symbol']
            stock_rps_20 = row.get('rps_20', 0) or 0
            
            # 核心条件：个股 RPS_20 也要强
            if stock_rps_20 > self.STOCK_RPS_THRESHOLD:
                
                belong_sectors = stock_to_sector.get(symbol, '')
                
                # 记录结果
                results.append({
                    'trade_date': trade_date,
                    'symbol': symbol,
                    # name 字段 quant_feature_rps 表里没有，如果有 stock_info 可以 join，
                    # 或者基类 get_daily_features 没返回 name。这里暂时留空或后续补。
                    'name': '', 
                    'signal_type': 'BUY',
                    'meta_info': json.dumps({
                        'reason': f"板块共振: [{belong_sectors}] 强于90, 个股强于{int(stock_rps_20)}",
                        'sector': belong_sectors,
                        'stock_rps': stock_rps_20
                    })
                })

        # 补全股票名称 (可选优化)
        if results:
            sym_list = [r['symbol'] for r in results]
            names = self.get_stock_names(sym_list) # 借用基类或者自己查
            for r in results:
                r['name'] = names.get(r['symbol'], '未知')

        print(f"✅ 策略执行完毕，发现 {len(results)} 只共振牛股。")
        
        if results:
            self.save_results(pd.DataFrame(results))

    def get_stock_names(self, symbols):
        """辅助：查名字"""
        if not symbols: return {}
        s_str = "'" + "','".join(symbols) + "'"
        try:
            df = pd.read_sql(text(f"SELECT symbol, name FROM stock_info WHERE symbol IN ({s_str})"), self.engine)
            return dict(zip(df['symbol'], df['name']))
        except:
            return {}

if __name__ == "__main__":
    strategy = ResonanceStrategy()
    # 手动指定有数据的日期测试，或者用默认今天
    strategy.run()