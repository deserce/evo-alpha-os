# backend/quant_engine/core/tdx_lib.py

import pandas as pd
import numpy as np

class TdxFuncs:
    """
    通达信公式的 Python Pandas 向量化实现
    传入的 df 必须包含: close, open, high, low, volume, turnover_rate
    """
    def __init__(self, df):
        self.df = df
        # 统一转为 float 防止类型错误
        self.C = df['close'].astype(float)
        self.H = df['high'].astype(float)
        self.L = df['low'].astype(float)
        self.O = df['open'].astype(float)
        self.V = df['volume'].astype(float)
        
        # 换手率容错处理
        if 'turnover_rate' in df.columns:
            self.TURNOVER = df['turnover_rate'].fillna(0).astype(float)
        else:
            self.TURNOVER = pd.Series(0, index=df.index)

    def REF(self, series, n):
        """引用N天前的数据"""
        return series.shift(n)

    def HHV(self, series, n):
        """N天内最高值"""
        return series.rolling(n).max()

    def LLV(self, series, n):
        """N天内最低值"""
        return series.rolling(n).min()

    def MA(self, series, n):
        """N日简单移动平均"""
        return series.rolling(n).mean()

    def COUNT(self, condition, n):
        """统计N天中满足条件的天数"""
        return condition.rolling(n).sum()

    def EVERY(self, condition, n):
        """一直满足: N天内条件一直成立"""
        return condition.rolling(n).sum() == n

    def HHVBARS(self, series, n):
        """N周期内最高价到当前的周期数"""
        def _argmax_dist(x):
            return len(x) - 1 - np.argmax(x)
        return series.rolling(n).apply(_argmax_dist, raw=True)
        
    def LLVBARS(self, series, n):
        """N周期内最低价到当前的周期数"""
        def _argmin_dist(x):
            return len(x) - 1 - np.argmin(x)
        return series.rolling(n).apply(_argmin_dist, raw=True)

def calc_dynamic_drawdown(high_series, low_series, window=120):
    """
    计算“创新高后的最大回撤” (通用函数)
    """
    # 转换为 numpy 数组加速
    h_data = high_series.tail(window).values
    l_data = low_series.tail(window).values
    
    if len(h_data) == 0: return 1.0

    # 1. 找到最高点位置
    max_h_idx = np.argmax(h_data)
    max_h_val = h_data[max_h_idx]

    if max_h_val == 0: return 0.0 # 防止除以0

    # 2. 如果最高点就是今天
    if max_h_idx == len(h_data) - 1:
        return 0.0

    # 3. 在最高点之后的区域找最低点
    after_high_lows = l_data[max_h_idx:]
    if len(after_high_lows) == 0: return 0.0
    
    min_l_val = np.min(after_high_lows)
    
    drawdown = (max_h_val - min_l_val) / max_h_val
    return drawdown