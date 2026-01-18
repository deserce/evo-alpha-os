import pandas as pd
import numpy as np
from ..core.tdx_lib import TdxFuncs  # 注意这里引用了上一层的 tdx_lib

def calc_dynamic_drawdown(high_series, low_series, window=120):
    """
    计算“创新高后的最大回撤”
    """
    # 获取最后 window 天的数据
    h_data = high_series.tail(window).values
    l_data = low_series.tail(window).values
    
    if len(h_data) == 0: return 1.0 # 数据不足

    # 1. 找到最高点位置
    max_h_idx = np.argmax(h_data)
    max_h_val = h_data[max_h_idx]

    # 2. 如果最高点就是今天
    if max_h_idx == len(h_data) - 1:
        return 0.0 # 回撤为0

    # 3. 在最高点之后的区域找最低点
    after_high_lows = l_data[max_h_idx:]
    min_l_val = np.min(after_high_lows)
    
    drawdown = (max_h_val - min_l_val) / max_h_val
    return drawdown

def check_mrgc_signal(df, rps_dict):
    """
    【核心函数】执行 MRGC + SXHCG 选股策略
    """
    if df.empty or len(df) < 250:
        return False, "数据不足250天"

    # 初始化工具箱
    T = TdxFuncs(df)
    
    # 提取 RPS
    RPS50  = rps_dict.get('rps_50', 0)
    RPS120 = rps_dict.get('rps_120', 0)
    RPS250 = rps_dict.get('rps_250', 0)

    # ================= MRGC 模块 =================
    # 换手率 < 25% (取最后一天)
    mrgc00 = T.TURNOVER.iloc[-1] < 25

    # 计算动态回撤 (针对过去120天)
    dd_120 = calc_dynamic_drawdown(T.H, T.L, 120)
    
    # MRGC001: 回撤 <= 0.5
    mrgc001 = dd_120 <= 0.5

    # MRGC002: 收盘价 > 1年最高价的 0.7
    hhv_c_250 = T.HHV(T.C, 250).iloc[-1]
    mrgc002 = (T.C.iloc[-1] / hhv_c_250) > 0.7

    mrgc01 = mrgc001 and mrgc002

    # MRGC回撤HC
    mrgc_hc = (dd_120 <= 0.35) and ((T.C.iloc[-1] / hhv_c_250) > 0.8)

    # --- XG 子条件 ---
    # XG11: 过去5天至少1天收盘价创250日新高
    is_new_high = T.C == T.HHV(T.C, 250)
    xg11 = T.COUNT(is_new_high, 5).iloc[-1] >= 1
    
    xg12 = (RPS120 > 95.99) or (RPS250 > 95.99)
    xg13 = (RPS120 > 94.99) and (RPS50 > 94.99)
    xg1 = xg11 and (xg12 or xg13)

    # XG2
    hhv_h_250 = T.HHV(T.H, 250).iloc[-1]
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

    # ================= SXHCG 模块 =================
    sxhcg1 = (RPS120 + RPS250) > 185

    # 均线
    ma10 = T.MA(T.C, 10)
    ma20 = T.MA(T.C, 20)
    ma200 = T.MA(T.C, 200)
    ma250 = T.MA(T.C, 250)

    sxhcg20 = T.C.iloc[-1] > ma20.iloc[-1]
    sxhcg21 = T.COUNT(T.C > ma250, 30).iloc[-1] >= 25
    sxhcg22 = T.COUNT(T.C > ma200, 30).iloc[-1] >= 25
    sxhcg23 = T.COUNT(T.C > ma20, 10).iloc[-1] >= 9
    
    cond_ma10 = T.COUNT(T.C > ma10, 4).iloc[-1] >= 3
    cond_ma20 = T.COUNT(T.C > ma20, 4).iloc[-1] >= 3
    sxhcg24 = cond_ma10 and cond_ma20

    sxhcg2 = sxhcg20 and sxhcg21 and sxhcg22 and (sxhcg23 or sxhcg24)

    # SXHCG3: 20天回撤
    dd_20 = calc_dynamic_drawdown(T.H, T.L, 20)
    sxhcg31 = dd_20 <= 0.25
    sxhcg32 = (T.C.iloc[-1] / hhv_c_250) > 0.8
    sxhcg3 = sxhcg31 and sxhcg32

    # SXHCG4: 均线形态
    ma20_up = ma20 >= T.REF(ma20, 1)
    sxhcg411 = T.EVERY(ma20_up, 5).iloc[-1]
    sxhcg412 = T.EVERY(ma10 >= ma20, 5).iloc[-1]
    sxhcg41 = sxhcg411 and sxhcg412

    sxhcg421 = ma10.iloc[-1] >= T.REF(ma10, 1).iloc[-1]
    sxhcg422 = ma20.iloc[-1] >= T.REF(ma20, 1).iloc[-1]
    sxhcg423 = ma10.iloc[-1] >= ma20.iloc[-1]
    sxhcg42 = sxhcg421 and sxhcg422 and sxhcg423
    
    sxhcg4 = sxhcg41 or sxhcg42

    sxhcg5 = T.TURNOVER.iloc[-1] < 15
    sxhcg6 = mrgc001

    SXHCG_SIGNAL = sxhcg1 and sxhcg2 and sxhcg3 and sxhcg4 and sxhcg5 and sxhcg6

    # ================= 最终判定 =================
    final_signal = MRGC_SIGNAL or SXHCG_SIGNAL
    
    return final_signal, "MRGC/SXHCG触发" if final_signal else "未触发"