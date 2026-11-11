"""Tongdaxin formula screeners evaluated on each stock's daily bar sequence."""

from typing import Literal

import numpy as np
import pandas as pd

STRATEGIES = {
    "rps_acceleration_v1": "RPS50 加速启动",
    "rps_platform_breakout_v1": "20 日启动点突破",
    "rps_long_pullback_v1": "年度主线回踩",
    "rps_resonance_v1": "多周期 RPS 共振",
    "monthly_reversal_v65": "月线反转 6.5",
    "near_year_high_v1": "接近一年新高",
}
# MA250 inside COUNT(...,45), evaluated at each of the prior 14 bars as well.
LOOKBACK_BARS = 250 + 44 + 14
_RPS_COLUMNS = ("rps_10", "rps_20", "rps_50", "rps_120", "rps_250")


class _DailyBars:
    """Group rolling windows by stock and keep unknown values in comparisons."""

    def __init__(self, frame: pd.DataFrame):
        self.symbols = frame.get("symbol")
        self.frame = frame

    def number(self, column: str, lower: float, upper: float = np.inf) -> pd.Series:
        values = pd.to_numeric(self.frame[column], errors="coerce").astype("Float64")
        return values.where(values.between(lower, upper) & np.isfinite(values))

    def ref(self, values: pd.Series, periods: int) -> pd.Series:
        if self.symbols is None:
            return values.shift(periods)
        return values.groupby(self.symbols, sort=False).shift(periods)

    def rolling(
        self,
        values: pd.Series,
        periods: int,
        operation: Literal["mean", "max", "min", "sum"],
    ) -> pd.Series:
        if self.symbols is None:
            return getattr(values.rolling(periods), operation)().astype("Float64")
        windows = values.groupby(self.symbols, sort=False).rolling(periods)
        result = getattr(windows, operation)().droplevel(0)
        return result.reindex(values.index).astype("Float64")


def _monthly_reversal(
    bars: _DailyBars,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    rps50: pd.Series,
    rps120: pd.Series,
    diagnostics: dict[str, pd.Series] | None,
) -> pd.Series:
    roll = bars.rolling
    ref = bars.ref
    ma20 = roll(close, 20, "mean")
    ma120 = roll(close, 120, "mean")
    ma200 = roll(close, 200, "mean")
    ma250 = roll(close, 250, "mean")
    low120 = roll(low, 120, "min")
    high120 = roll(high, 120, "max")

    strong = (rps50 >= 90) | (rps120 >= 90)
    strong_new_close = strong & (close >= roll(close, 70, "max"))
    fyx1 = (rps50 > 87) | (rps120 > 90)
    fyx2 = (
        ((roll(low, 50, "min") > roll(low, 200, "min")) & strong_new_close)
        | ((roll(low, 30, "min") > low120) & strong_new_close)
        | (roll(low, 20, "min") > roll(low, 50, "min"))
    )
    nh80 = high >= roll(high, 80, "max")
    fyx3 = (roll(nh80, 10, "sum") > 0) | (
        ((close >= roll(close, 50, "max")) | (high >= roll(high, 50, "max"))) & strong
    )
    fyx4 = (close > ma20) & (close > ma200) & (ma120 / ma200 > 0.9)

    aa200 = roll(close > ma200, 45, "sum")
    aa250 = roll(close > ma250, 45, "sum")
    laa200 = roll(low < ma200, 45, "sum")
    laa250 = roll(low < ma250, 45, "sum")
    fyx5 = (
        ((aa200 >= 2) & (aa200 < 45))
        | ((laa200 > 0) & (aa200 > 2))
        | ((laa250 > 0) & (aa250 > 2))
    )

    rising120_10 = ma120 >= ref(ma120, 10)
    rising120_15 = ma120 >= ref(ma120, 15)
    rising200_10 = ma200 >= ref(ma200, 10)
    rising200_15 = ma200 >= ref(ma200, 15)
    either_rising = rising120_10 | rising200_10 | rising120_15 | rising200_15
    both_rising = (rising120_10 & rising200_10) | (rising120_15 & rising200_15)
    range_ratio = roll(high, 30, "max") / low120
    fyx6 = (
        ((range_ratio < 1.50) & either_rising)
        | ((range_ratio < 1.55) & both_rising)
        | ((range_ratio < 1.65) & (ma120 > ma200) & either_rising & strong_new_close)
    )

    near_high = roll(high, 5, "max") / high120
    fyx7 = ((near_high > 0.85) | ((near_high > 0.8) & strong_new_close)) & (
        close / roll(high, 10, "max") > 0.9
    )
    yxfz = fyx1 & fyx2 & fyx3 & fyx4 & fyx5 & fyx6 & fyx7
    # Match the chart marker: today is the first hit in the last 15 bars.
    # An unknown earlier YXFZ cannot prove that there was no prior hit.
    prior_hits = roll(ref(yxfz, 1), 14, "sum")
    if diagnostics is not None:
        diagnostics.update(
            ma_200=ma200,
            closes_above_ma_200=aa200,
            closes_above_ma_250=aa250,
            high_5_to_high_120=near_high,
            prior_hits_14=prior_hits,
        )
    return yxfz & (prior_hits == 0)


def evaluate(
    frame: pd.DataFrame, *, metrics: dict[str, pd.DataFrame] | None = None
) -> dict[str, pd.Series]:
    """Evaluate one or more stocks, ordered by date within stock.

    Supply OHLCV and the five rps_* columns (already scaled to 0–100). An
    optional symbol column partitions REF and rolling operations. Preserve
    the input index and return False wherever a hit cannot be established.
    Optionally populate metrics with formula inputs indexed like frame.
    """
    if frame.empty:
        return {name: pd.Series(False, index=frame.index) for name in STRATEGIES}
    bars = _DailyBars(frame.reset_index(drop=True))
    roll = bars.rolling
    ref = bars.ref
    open_price, high, low, close = (
        bars.number(column, np.nextafter(0.0, 1.0))
        for column in ("open", "high", "low", "close")
    )
    volume = bars.number("volume", 0)
    rps10, rps20, rps50, rps120, rps250 = (
        bars.number(column, 0, 100) for column in _RPS_COLUMNS
    )

    close20 = ref(close, 20)
    close50 = ref(close, 50)
    open20 = ref(open_price, 20)
    high20 = ref(high, 20)
    rps50_20 = ref(rps50, 20)
    ma_volume5 = roll(volume, 5, "mean")
    volume5 = ref(volume, 5)
    ma_volume5_at5 = ref(ma_volume5, 5)
    volume20 = ref(volume, 20)
    ma_volume20_at20 = ref(roll(volume, 20, "mean"), 20)
    volume50 = ref(volume, 50)
    ma_volume50_at50 = ref(roll(volume, 50, "mean"), 50)
    body20 = (close20 - open20) / open20
    distance50 = (close - close50).abs() / close50
    acceleration = (
        (rps50 >= 90)
        & (rps50_20 < 80)
        & (volume5 > ma_volume5_at5 * 1.5)
        & (close > close20)
    )
    platform_breakout = (
        (rps20 >= 90)
        & (rps50 >= 85)
        & (close20 > open20)
        & (body20 > 0.02)
        & (volume20 > ma_volume20_at20 * 1.2)
        & (close > high20)
    )
    long_pullback = (
        (rps120 >= 90)
        & (rps250 >= 90)
        & (rps50 >= 85)
        & (distance50 < 0.05)
        & (ref(high, 50) > ref(open_price, 50))
        & (volume50 > ma_volume50_at50 * 1.3)
    )
    close5 = ref(close, 5)
    close10 = ref(close, 10)
    resonance = (
        (rps10 >= 90)
        & (rps20 >= 90)
        & (rps50 >= 90)
        & (close > close5)
        & (close > close10)
        & (close > close20)
        & (close > close50)
        & (volume > ma_volume5 * 1.2)
    )
    reversal_metrics: dict[str, pd.Series] = {}
    reversal = _monthly_reversal(
        bars, close, high, low, rps50, rps120,
        reversal_metrics if metrics is not None else None,
    )
    high250 = roll(high, 250, "max")
    year_high_ratio = close / high250
    near_year_high = year_high_ratio > 0.9

    if metrics is not None:
        raw_metrics = {
            "rps_acceleration_v1": {
                "rps_50_at_20": rps50_20, "close_20": close20,
                "volume_5": volume5, "ma_volume_5_at_5": ma_volume5_at5,
            },
            "rps_platform_breakout_v1": {
                "high_20": high20, "body_return_20": body20,
                "volume_20": volume20, "ma_volume_20_at_20": ma_volume20_at20,
            },
            "rps_long_pullback_v1": {
                "close_50": close50, "distance_to_close_50": distance50,
                "volume_50": volume50, "ma_volume_50_at_50": ma_volume50_at50,
            },
            "rps_resonance_v1": {
                "close_5": close5, "close_10": close10, "close_20": close20,
                "close_50": close50, "volume": volume, "ma_volume_5": ma_volume5,
            },
            "monthly_reversal_v65": reversal_metrics,
            "near_year_high_v1": {"high_250": high250, "close_to_high_250": year_high_ratio},
        }
        for name, columns in raw_metrics.items():
            values = pd.DataFrame(columns)
            values.index = frame.index
            metrics[name] = values

    results = {
        "rps_acceleration_v1": acceleration,
        "rps_platform_breakout_v1": platform_breakout,
        "rps_long_pullback_v1": long_pullback,
        "rps_resonance_v1": resonance,
        "monthly_reversal_v65": reversal,
        "near_year_high_v1": near_year_high,
    }
    return {
        name: pd.Series(result.fillna(False).to_numpy(dtype=bool), index=frame.index)
        for name, result in results.items()
    }
