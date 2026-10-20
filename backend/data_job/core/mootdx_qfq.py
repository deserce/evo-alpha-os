"""
mootdx 前复权计算模块。

基于 mootdx 返回的 xdxr(除权除息) 数据，对日线价格执行前复权。
默认只调整价格列，并重新计算 pct_chg；成交量和成交额保持原值，
以避免影响现有下游统计逻辑。

换手率计算：
    turnover_rate = volume_in_shares / float_shares * 100

    - volume 单位通过 `amount / (close * volume)` 自动检测（股 or 手）
    - float_shares 来自 xdxr 的 `panqianliutong / panhouliutong`（万股）
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PRICE_COLUMNS = ("open", "high", "low", "close")
ACTION_COLUMNS = ("fenhong", "peigu", "peigujia", "songzhuangu")
FLOAT_SHARE_COLUMNS = ("panqianliutong", "panhouliutong")

# volume 单位推断常量
_VOLUME_UNIT_SHARE = 1.0      # 1 手 = 1 股（mootdx 已换算）
_VOLUME_UNIT_HAND = 100.0     # 1 手 = 100 股（原始通达信协议）
_VOLUME_SCALE_CANDIDATES = (_VOLUME_UNIT_SHARE, _VOLUME_UNIT_HAND)


def _empty_actions() -> pd.DataFrame:
    return pd.DataFrame(columns=["trade_date", *ACTION_COLUMNS])


def _empty_float_shares() -> pd.DataFrame:
    return pd.DataFrame(columns=["trade_date", "float_shares"])


def _parse_trade_date(df: pd.DataFrame) -> pd.DataFrame | None:
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    elif "date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
    elif {"year", "month", "day"}.issubset(df.columns):
        df["trade_date"] = pd.to_datetime(df[["year", "month", "day"]]).dt.date
    else:
        return None
    return df


def normalize_xdxr(xdxr_df: pd.DataFrame | None) -> pd.DataFrame:
    """标准化 xdxr，只保留前复权需要的字段。"""
    if xdxr_df is None or xdxr_df.empty:
        return _empty_actions()

    df = _parse_trade_date(xdxr_df.copy())
    if df is None:
        logger.warning("xdxr 数据缺少日期列，跳过前复权")
        return _empty_actions()

    if "category" in df.columns:
        df = df[df["category"] == 1].copy()

    if df.empty:
        return _empty_actions()

    for column in ACTION_COLUMNS:
        if column not in df.columns:
            df[column] = 0.0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)

    df = (
        df[["trade_date", *ACTION_COLUMNS]]
        .groupby("trade_date", as_index=False)
        .sum()
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    return df


def normalize_xdxr_float_shares(xdxr_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    标准化 xdxr 中的流通股本变更信息。

    mootdx 的 panqianliutong / panhouliutong 单位是"万股"。
    """
    if xdxr_df is None or xdxr_df.empty:
        return _empty_float_shares()

    df = _parse_trade_date(xdxr_df.copy())
    if df is None:
        logger.warning("xdxr 数据缺少日期列，跳过换手率计算")
        return _empty_float_shares()

    for column in FLOAT_SHARE_COLUMNS:
        if column not in df.columns:
            df[column] = np.nan
        df[column] = pd.to_numeric(df[column], errors="coerce")

    for column in ("qianzongguben", "houzongguben"):
        if column not in df.columns:
            df[column] = np.nan
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df[df[list(FLOAT_SHARE_COLUMNS)].notna().any(axis=1)].copy()
    if df.empty:
        return _empty_float_shares()

    before_positive = df["panqianliutong"] > 0
    after_positive = df["panhouliutong"] > 0
    total_known = df[["qianzongguben", "houzongguben"]].notna().any(axis=1)
    total_positive = (df["qianzongguben"] > 0) | (df["houzongguben"] > 0)

    df = df[(before_positive | after_positive) & (~total_known | total_positive)].copy()
    if df.empty:
        return _empty_float_shares()

    def positive_max(series: pd.Series) -> float:
        values = pd.to_numeric(series, errors="coerce")
        values = values[values > 0]
        return float(values.max()) if not values.empty else np.nan

    df = (
        df[["trade_date", *FLOAT_SHARE_COLUMNS]]
        .groupby("trade_date", as_index=False)
        .agg({"panqianliutong": positive_max, "panhouliutong": positive_max})
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    return df


def _normalize_kline(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    df = _parse_trade_date(raw_df.copy())
    if df is None:
        raise ValueError("K线数据缺少 trade_date/datetime/date 列")

    for column in PRICE_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    for column in ("volume", "amount"):
        if column not in df.columns:
            continue
        df[column] = pd.to_numeric(df[column], errors="coerce")
        zero_sentinel = df[column].gt(0) & df[column].lt(1)
        df.loc[zero_sentinel, column] = 0.0

    df = df.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last")
    df = df.reset_index(drop=True)
    return df


def detect_volume_scale(kline_df: pd.DataFrame) -> float:
    """
    从 amount / (close * volume) 推断 volume 的单位乘数（换算到股）。

    返回值：
        1.0    — volume 已经是「股」
        100.0  — volume 是「手」（需乘 100 换算为股）

    样本不足或数据异常时，默认返回 1.0（保守处理）。
    """
    required = {"close", "volume", "amount"}
    if not required.issubset(kline_df.columns):
        return _VOLUME_UNIT_SHARE

    close = pd.to_numeric(kline_df["close"], errors="coerce")
    volume = pd.to_numeric(kline_df["volume"], errors="coerce")
    amount = pd.to_numeric(kline_df["amount"], errors="coerce")

    mask = (volume > 0) & (close > 0) & (amount > 0)
    if mask.sum() < 3:
        return _VOLUME_UNIT_SHARE

    # ratio = amount / (close * volume)，理论应接近某个固定 scale
    ratio = (amount[mask] / (close[mask] * volume[mask])).median()
    if not np.isfinite(ratio) or ratio <= 0:
        return _VOLUME_UNIT_SHARE

    # 选择距离最近的候选值
    return min(_VOLUME_SCALE_CANDIDATES, key=lambda s: abs(np.log(ratio / s)))


def build_float_share_series(raw_df: pd.DataFrame, xdxr_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    基于 xdxr 构建每个交易日对应的流通股本序列（单位：股）。

    策略：
        1. 每个 xdxr 事件日取 `panhouliutong` 作为变更后流通股本
        2. 事件之间用 merge_asof backward 向前填充
        3. 首个事件之前用该事件的 `panqianliutong`（或 panhouliutong）作为历史基准
        4. 完全无 xdxr 变更数据时，整列 NaN（由调用方处理）
    """
    data = _normalize_kline(raw_df)
    if data.empty:
        return _empty_float_shares()

    changes = normalize_xdxr_float_shares(xdxr_df)
    if changes.empty:
        result = data[["trade_date"]].copy()
        result["float_shares"] = np.nan
        return result

    changes = changes.copy()
    # 事件日生效的流通股本：优先 panhouliutong，否则 panqianliutong
    changes["float_shares"] = changes["panhouliutong"].fillna(changes["panqianliutong"])
    changes["float_shares"] = changes["float_shares"] * 10000.0  # 万股 -> 股
    changes = changes[changes["float_shares"] > 0].copy()

    if changes.empty:
        result = data[["trade_date"]].copy()
        result["float_shares"] = np.nan
        return result

    # 历史基准（首个事件日之前）
    first_event = changes.iloc[0]
    baseline = first_event["panqianliutong"]
    if pd.isna(baseline) or baseline <= 0:
        baseline = first_event["panhouliutong"]
    baseline = float(baseline) * 10000.0 if pd.notna(baseline) and baseline > 0 else np.nan

    base = data[["trade_date"]].copy()
    base["trade_date"] = pd.to_datetime(base["trade_date"])
    events = changes[["trade_date", "float_shares"]].copy()
    events["trade_date"] = pd.to_datetime(events["trade_date"])

    result = pd.merge_asof(
        base.sort_values("trade_date"),
        events.sort_values("trade_date"),
        on="trade_date",
        direction="backward",
    )
    if pd.notna(baseline):
        result["float_shares"] = result["float_shares"].fillna(baseline)

    result["float_shares"] = result["float_shares"].mask(result["float_shares"] <= 0, np.nan)
    result["trade_date"] = result["trade_date"].dt.date
    return result


def compute_turnover_rate(kline_df: pd.DataFrame, xdxr_df: pd.DataFrame | None) -> pd.Series:
    """
    计算每日换手率（%）。返回一个与 kline_df 行对齐的 Series。

    公式：turnover_rate = volume_in_shares / float_shares * 100
    """
    if kline_df is None or kline_df.empty:
        return pd.Series(dtype=float)

    original_dates = _parse_trade_date(kline_df.copy())
    if original_dates is None:
        return pd.Series([np.nan] * len(kline_df), index=kline_df.index, dtype=float)

    data = _normalize_kline(kline_df)
    if data.empty or "volume" not in data.columns:
        return pd.Series([np.nan] * len(kline_df), index=kline_df.index, dtype=float)

    scale = detect_volume_scale(data)
    if scale != _VOLUME_UNIT_SHARE:
        logger.debug("volume 单位检测为 %s（乘数 %s）", "手" if scale == 100 else "未知", scale)

    float_shares_df = build_float_share_series(data, xdxr_df)
    merged = data.merge(float_shares_df, on="trade_date", how="left")

    volume_shares = pd.to_numeric(merged["volume"], errors="coerce") * scale
    shares = pd.to_numeric(merged["float_shares"], errors="coerce")

    rate = (volume_shares / shares) * 100.0
    rate = rate.mask(~np.isfinite(rate), np.nan).mask(shares <= 0, np.nan)

    by_date = pd.Series(rate.values, index=merged["trade_date"])
    aligned = original_dates["trade_date"].map(by_date)
    return pd.Series(aligned.to_numpy(), index=kline_df.index, dtype=float)


def apply_turnover_rate_from_xdxr(raw_df: pd.DataFrame, xdxr_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    使用 xdxr 的流通股本变更信息，计算并写回 turnover_rate 列。

    若输入已有 turnover_rate（例如来自其他数据源），新计算结果仅用于填空。
    """
    data = _normalize_kline(raw_df)
    if data.empty:
        return data

    computed = compute_turnover_rate(data, xdxr_df)

    if "turnover_rate" in data.columns:
        existing = pd.to_numeric(data["turnover_rate"], errors="coerce")
        data["turnover_rate"] = existing.fillna(computed)
    else:
        data["turnover_rate"] = computed

    return data


def build_qfq_factors(raw_df: pd.DataFrame, xdxr_df: pd.DataFrame | None) -> pd.DataFrame:
    """计算每个交易日对应的前复权因子。"""
    data = _normalize_kline(raw_df)
    if data.empty:
        return pd.DataFrame(columns=["trade_date", "adj_factor"])

    if "close" not in data.columns:
        raise ValueError("K线数据缺少 close 列，无法计算前复权")

    actions = normalize_xdxr(xdxr_df)
    if actions.empty:
        factors = data[["trade_date"]].copy()
        factors["adj_factor"] = 1.0
        return factors

    data = data.merge(actions, on="trade_date", how="left")
    for column in ACTION_COLUMNS:
        data[column] = data[column].fillna(0.0)

    denominator = 10.0 + data["peigu"] + data["songzhuangu"]
    preclose = ((data["close"].shift(1) * 10.0) - data["fenhong"] + (data["peigu"] * data["peigujia"])) / denominator
    ratio = (preclose.shift(-1) / data["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    ratio = ratio.mask(ratio <= 0, 1.0)

    factors = ratio.iloc[::-1].cumprod().iloc[::-1]
    factors = factors.replace([np.inf, -np.inf], np.nan).fillna(1.0)

    result = data[["trade_date"]].copy()
    result["adj_factor"] = factors.astype(float)
    return result


def apply_mootdx_qfq(raw_df: pd.DataFrame, xdxr_df: pd.DataFrame | None) -> pd.DataFrame:
    """对原始日线数据应用前复权，并回填换手率。"""
    data = _normalize_kline(raw_df)
    if data.empty:
        return data

    # 先算换手率（基于未复权 volume；qfq 不改量）
    data["turnover_rate"] = compute_turnover_rate(data, xdxr_df)

    # 再调整价格
    factors = build_qfq_factors(data, xdxr_df)
    data = data.merge(factors, on="trade_date", how="left")
    data["adj_factor"] = data["adj_factor"].fillna(1.0)

    for column in PRICE_COLUMNS:
        if column in data.columns:
            data[column] = (pd.to_numeric(data[column], errors="coerce") * data["adj_factor"]).astype(float)

    if "close" in data.columns:
        data["pct_chg"] = data["close"].pct_change() * 100

    data["adjust_type"] = "qfq"
    data = data.drop(columns=["adj_factor"], errors="ignore")
    return data
