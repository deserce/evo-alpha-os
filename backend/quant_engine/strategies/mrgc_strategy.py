# backend/quant_engine/strategies/mrgc_strategy.py

import sys
import os
import pandas as pd
import json
from datetime import datetime, date

# ================= 环境路径适配 (保留) =================
current_dir = os.path.dirname(os.path.abspath(__file__))
engine_root = os.path.abspath(os.path.join(current_dir, "../../")) 
if engine_root not in sys.path:
    sys.path.append(engine_root)

# 模块导入
from quant_engine.core.tdx_lib import TdxFuncs, calc_dynamic_drawdown
from quant_engine.strategies.base_strategy import BaseStrategy

class MrgcStrategy(BaseStrategy):
    def __init__(self):
        # 初始化基类
        super().__init__("mrgc_v1")

        # ✅ 策略元数据（将在预选结果中展示）
        self.strategy_display_name = "陶博士MRGC"
        self.strategy_description = """
        基于陶博士MRGC（Mini Ryuko）形态的趋势跟踪策略。

        核心思想：寻找处于上升趋势、回调幅度适中、即将突破的股票。
        适合：强势股回调后的二次启动机会。

        来源：陶博士每日观察
        """
        self.strategy_logic = """
        【核心逻辑】

        1. MRGC基础条件：
           - 换手率 < 25%（排除过度炒作）
           - 120日回撤 <= 50%（趋势未破坏）
           - 收盘价 > 250日高点的70%（趋势向上）

        2. 四个触发信号（XG1-XG4）：
           XG1: 新高突破（最近5天创新高）+ RPS极强
           XG2: 接近新高（>85%距新高）+ RPS极强
           XG3: 深度回调后反弹（>70%距新高）+ RPS极强
           XG4: 回调幅度小（<35%）、位置高（>80%）+ RPS强势

        3. SXHCG信号：
           - RPS120 + RPS250 > 185（双RPS强势）
           - 均线多头排列
           - 股价站稳均线
        """
        self.filter_criteria = """
        【筛选条件】

        1. 股票池：核心股票池
           - 基金持股比例 ≥ 5%（最近3季度任意）
           - 北向资金 ≥ 1亿元

        2. 技术指标：
           - RPS250 > 85（相对强势）
           - 成交量活跃
           - 趋势向上

        3. 排除条件：
           - 换手率过高（> 25%）
           - 趋势破坏（120日回撤>50%）
           - 位置过低（距250日高点<70%）
        """
        self.load_days = 400 

    def _check_signal(self, df, rps_row):
        """核心选股逻辑"""
        if df.empty or len(df) < 250:
            return False, "K线数据不足"

        # 1. 列名标准化
        df.columns = [c.lower() for c in df.columns]

        # 2. 初始化工具
        try:
            T = TdxFuncs(df)
        except Exception as e:
            return False, f"指标计算错误: {e}"
        
        # 3. 提取 RPS
        def get_rps(k):
            val = rps_row.get(k, 0)
            try: return float(val) if pd.notnull(val) else 0.0
            except: return 0.0

        RPS50  = get_rps('rps_50')
        RPS120 = get_rps('rps_120')
        RPS250 = get_rps('rps_250')

        # === MRGC ===
        curr_turnover = T.TURNOVER.iloc[-1] if hasattr(T, 'TURNOVER') else 0
        mrgc00 = curr_turnover < 25
        dd_120 = calc_dynamic_drawdown(T.H, T.L, 120)
        mrgc001 = dd_120 <= 0.5
        hhv_c_250 = T.HHV(T.C, 250).iloc[-1]
        if hhv_c_250 == 0: return False, "异常HHV"
        mrgc002 = (T.C.iloc[-1] / hhv_c_250) > 0.7
        mrgc01 = mrgc001 and mrgc002
        mrgc_hc = (dd_120 <= 0.35) and ((T.C.iloc[-1] / hhv_c_250) > 0.8)

        # XG1
        is_new_high = T.C >= T.HHV(T.C, 250)
        xg11 = T.COUNT(is_new_high, 5).iloc[-1] >= 1
        xg12 = (RPS120 > 95.99) or (RPS250 > 95.99)
        xg13 = (RPS120 > 94.99) and (RPS50 > 94.99)
        xg1 = xg11 and (xg12 or xg13)

        # XG2
        hhv_h_250 = T.HHV(T.H, 250).iloc[-1]
        if hhv_h_250 == 0: hhv_h_250 = 1
        xg21 = (T.C.iloc[-1] / hhv_h_250) >= 0.85
        xg22 = (RPS120 > 96.99) or (RPS250 > 96.99)
        xg2 = xg21 and xg22

        # XG3
        xg31 = (T.C.iloc[-1] / hhv_h_250) >= 0.70
        xg32 = (RPS120 > 97.99) or (RPS250 > 97.99)
        xg3 = xg31 and xg32

        # XG4
        xg41 = mrgc_hc
        xg42 = (RPS120 > 94.99) or (RPS250 > 94.99)
        xg4 = xg41 and xg42

        MRGC_SIGNAL = mrgc00 and mrgc01 and (xg1 or xg2 or xg3 or xg4)

        # === SXHCG ===
        sxhcg1 = (RPS120 + RPS250) > 185
        ma10, ma20, ma200, ma250 = T.MA(T.C, 10), T.MA(T.C, 20), T.MA(T.C, 200), T.MA(T.C, 250)

        try:
            sxhcg20 = T.C.iloc[-1] > ma20.iloc[-1]
            sxhcg21 = T.COUNT(T.C > ma250, 30).iloc[-1] >= 25
            sxhcg22 = T.COUNT(T.C > ma200, 30).iloc[-1] >= 25
            sxhcg23 = T.COUNT(T.C > ma20, 10).iloc[-1] >= 9
            cond_ma10 = T.COUNT(T.C > ma10, 4).iloc[-1] >= 3
            cond_ma20 = T.COUNT(T.C > ma20, 4).iloc[-1] >= 3
            sxhcg24 = cond_ma10 and cond_ma20
            sxhcg2 = sxhcg20 and sxhcg21 and sxhcg22 and (sxhcg23 or sxhcg24)

            dd_20 = calc_dynamic_drawdown(T.H, T.L, 20)
            sxhcg31 = dd_20 <= 0.25
            sxhcg32 = (T.C.iloc[-1] / hhv_c_250) > 0.8
            sxhcg3 = sxhcg31 and sxhcg32

            ma20_up = ma20 >= T.REF(ma20, 1)
            sxhcg411 = T.EVERY(ma20_up, 5).iloc[-1]
            sxhcg412 = T.EVERY(ma10 >= ma20, 5).iloc[-1]
            sxhcg41 = sxhcg411 and sxhcg412

            sxhcg421 = ma10.iloc[-1] >= T.REF(ma10, 1).iloc[-1]
            sxhcg422 = ma20.iloc[-1] >= T.REF(ma20, 1).iloc[-1]
            sxhcg423 = ma10.iloc[-1] >= ma20.iloc[-1]
            sxhcg42 = sxhcg421 and sxhcg422 and sxhcg423
            sxhcg4 = sxhcg41 or sxhcg42

            sxhcg5 = curr_turnover < 15
            sxhcg6 = mrgc001
            
            SXHCG_SIGNAL = sxhcg1 and sxhcg2 and sxhcg3 and sxhcg4 and sxhcg5 and sxhcg6
        except:
            SXHCG_SIGNAL = False

        if MRGC_SIGNAL: return True, "MRGC触发"
        if SXHCG_SIGNAL: return True, "SXHCG触发"
        
        return False, ""

    def run(self, trade_date=None):
        """执行策略"""
        if not trade_date: trade_date = str(date.today())
        print(f"🚀 正在执行策略 [{self.strategy_name}] 日期: {trade_date}")

        # 1. 获取股票池
        pool_df = self.get_stock_pool(pool_name='core_pool')
        if pool_df.empty:
            print("⚠️ 股票池为空")
            return
        target_symbols = pool_df['symbol'].tolist()
        
        # 2. 获取 RPS
        rps_df = self.get_daily_features(trade_date, target_symbols)
        rps_dict = rps_df.set_index('symbol').to_dict('index') if not rps_df.empty else {}

        # 3. 加载 K 线
        print(f"⏳ 加载 K 线 ({len(target_symbols)} 只)...")
        start_dt = (pd.to_datetime(trade_date) - pd.Timedelta(days=self.load_days)).strftime('%Y-%m-%d')
        symbols_str = "'" + "','".join(target_symbols) + "'"
        
        sql_kline = f"""
            SELECT symbol, trade_date, open, high, low, close, volume, turnover_rate
            FROM stock_daily_prices 
            WHERE trade_date >= '{start_dt}' AND trade_date <= '{trade_date}'
            AND symbol IN ({symbols_str}) 
            ORDER BY trade_date
        """
        
        try:
            kline_all = pd.read_sql(sql_kline, self.engine)
        except Exception as e:
            print(f"❌ K线读取失败: {e}")
            return

        if kline_all.empty:
            print("⚠️ K线为空")
            return
            
        # 4. 遍历计算
        results = []
        grouped = kline_all.groupby('symbol')
        total = len(target_symbols)
        count = 0
        
        for symbol in target_symbols:
            count += 1
            if count % 50 == 0: print(f"   进度: {count}/{total}...", end="\r")
            
            if symbol not in grouped.groups: continue
            df_k = grouped.get_group(symbol).copy().sort_values('trade_date')
            rps_row = rps_dict.get(symbol, {})
            
            try:
                is_signal, reason = self._check_signal(df_k, rps_row)
                if is_signal:
                    stock_name = pool_df.loc[pool_df['symbol'] == symbol, 'name'].values[0]
                    results.append({
                        'trade_date': trade_date,
                        'symbol': symbol,
                        'name': stock_name,
                        'signal_type': 'BUY',
                        'meta_info': json.dumps({
                            'reason': reason,
                            'rps_250': rps_row.get('rps_250', 0)
                        })
                    })
            except: continue

        print(f"\n✅ 发现 {len(results)} 个信号")
        if results:
            self.save_results(pd.DataFrame(results))

if __name__ == "__main__":
    MrgcStrategy().run()