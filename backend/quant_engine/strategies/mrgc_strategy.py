"""
EvoAlpha OS - 策略：MRGC策略
结合趋势（RPS）、波动率、基本面的综合选股策略
"""

import pandas as pd
from loguru import logger
from typing import List, Dict
from dataclasses import dataclass


@dataclass
class Signal:
    """交易信号"""
    symbol: str
    signal_type: str  # "BUY" | "SELL"
    confidence: float  # 置信度 0-1
    reason: str
    timestamp: str


class MRGCStrategy:
    """
    MRGC策略（Momentum + Risk + Growth + Combination）

    策略逻辑：
    1. 趋势（Momentum）：RPS > 90
    2. 波动率（Risk）：动态回撤控制
    3. 基本面（Growth）：营收/净利润增长
    4. 组合（Combination）：多个条件综合判断
    """

    def __init__(
        self,
        rps_threshold: float = 90.0,
        min_growth_rate: float = 0.3,
        max_drawdown: float = 0.15,
    ):
        self.rps_threshold = rps_threshold
        self.min_growth_rate = min_growth_rate
        self.max_drawdown = max_drawdown

    def scan(self, symbols: List[str]) -> List[Signal]:
        """
        扫描股票池，生成交易信号

        Args:
            symbols: 股票代码列表

        Returns:
            交易信号列表
        """
        logger.info(f"开始MRGC策略扫描，股票数量: {len(symbols)}")

        signals = []

        for symbol in symbols:
            try:
                signal = self._evaluate_stock(symbol)
                if signal and signal.signal_type == "BUY":
                    signals.append(signal)
            except Exception as e:
                logger.error(f"评估股票 {symbol} 失败: {e}")
                continue

        logger.info(f"MRGC策略扫描完成，找到 {len(signals)} 个信号")
        return signals

    def _evaluate_stock(self, symbol: str) -> Signal:
        """
        评估单只股票

        Args:
            symbol: 股票代码

        Returns:
            交易信号
        """
        # TODO: 从数据库读取数据
        # 1. RPS数据
        # 2. 财务数据
        # 3. 价格数据

        # 占位逻辑
        rps_20 = 95.0  # 假设RPS为95
        growth_rate = 0.35  # 假设增长率为35%
        current_price = 100.0

        # 策略条件判断
        conditions = {
            "rps": rps_20 >= self.rps_threshold,
            "growth": growth_rate >= self.min_growth_rate,
            # "drawdown": drawdown <= self.max_drawdown,
        }

        # 计算置信度
        confidence = sum(conditions.values()) / len(conditions)

        # 生成理由
        reasons = []
        if conditions["rps"]:
            reasons.append(f"RPS({rps_20:.1f}) > {self.rps_threshold}")
        if conditions["growth"]:
            reasons.append(f"增长率({growth_rate*100:.1f}%) > {self.min_growth_rate*100:.1f}%")

        # 判断是否买入
        if confidence >= 0.5:  # 至少满足一半条件
            return Signal(
                symbol=symbol,
                signal_type="BUY",
                confidence=confidence,
                reason="；".join(reasons),
                timestamp=pd.Timestamp.now().isoformat(),
            )

        return None


def run_mrgc_strategy(symbols: List[str] = None):
    """
    运行MRGC策略

    Args:
        symbols: 股票代码列表，如果为空则从核心池获取
    """
    logger.info("开始运行MRGC策略...")

    if symbols is None:
        # TODO: 从核心股票池获取
        symbols = ["000001", "600000"]  # 占位

    strategy = MRGCStrategy()
    signals = strategy.scan(symbols)

    logger.info(f"MRGC策略运行完成，生成 {len(signals)} 个信号")

    # TODO: 保存信号到数据库
    # quant_strategy_results 表

    return signals


if __name__ == "__main__":
    signals = run_mrgc_strategy()
    for signal in signals:
        logger.info(f"{signal.symbol}: {signal.signal_type} ({signal.confidence:.2f})")
