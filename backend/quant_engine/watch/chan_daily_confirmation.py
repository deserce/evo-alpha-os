"""First-visible dates for the chart's Zhihu ZIG formula, not causal trade signals.

Port of stockChartChanOverlay.ts ZIG10/ZIG20 buy/sell only. Every prefix is
computed independently. Historical plotted dates must not masquerade as alerts.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from functools import lru_cache, wraps
from threading import RLock
import math

MAX_BARS = 520
WARMUP_BARS = 60


def _zig(close: tuple[float, ...], percent: int) -> list[float]:
    n = len(close)
    result = [math.nan] * n
    pivots = [(0, close[0])]
    direction = candidate = initial_high = initial_low = 0
    threshold = percent / 100
    for i in range(1, n):
        if direction == 0:
            if close[i] >= close[initial_high]:
                initial_high = i
            if close[i] <= close[initial_low]:
                initial_low = i
            if close[i] >= close[initial_low] * (1 + threshold):
                direction = 1
                if initial_low > 0:
                    pivots.append((initial_low, close[initial_low]))
            elif close[i] <= close[initial_high] * (1 - threshold):
                direction = -1
                if initial_high > 0:
                    pivots.append((initial_high, close[initial_high]))
            if direction:
                candidate = i
        elif direction > 0:
            if close[i] >= close[candidate]:
                candidate = i
            elif close[i] <= close[candidate] * (1 - threshold):
                pivots.append((candidate, close[candidate]))
                direction, candidate = -1, i
        else:
            if close[i] <= close[candidate]:
                candidate = i
            elif close[i] >= close[candidate] * (1 + threshold):
                pivots.append((candidate, close[candidate]))
                direction, candidate = 1, i
    if candidate > pivots[-1][0]:
        pivots.append((candidate, close[candidate]))
    if pivots[-1][0] != n - 1:
        pivots.append((n - 1, close[-1]))
    if len(pivots) == 1:
        result[0] = close[0]
    for (start, a), (end, b) in zip(pivots, pivots[1:]):
        for i in range(start, end + 1):
            result[i] = a + (b - a) * (i - start) / (end - start)
    return result


def _points(bars: tuple[tuple[str, float, float, float, float], ...]) -> list[tuple]:
    close = tuple(row[4] for row in bars)
    p10, p20 = _zig(close, 10), _zig(close, 20)
    ref = lambda p, i, offset=1: p[max(0, i - offset)]
    up = lambda p, i: p[i] > ref(p, i) and ref(p, i) <= ref(p, i, 2) <= ref(p, i, 3)
    down = lambda p, i: p[i] < ref(p, i) and ref(p, i) >= ref(p, i, 2) >= ref(p, i, 3)
    en2 = [int(up(p10, i)) for i in range(len(bars))]
    ex2 = [int(down(p10, i)) for i in range(len(bars))]
    buys, sells = [0], [0]
    for buy, sell in zip(en2, ex2):
        buys.append(buys[-1] + buy)
        sells.append(sells[-1] + sell)
    last_decline = last_rise = -1
    result = []
    for i, row in enumerate(bars):
        rising, falling = p20[i] > ref(p20, i), p20[i] < ref(p20, i)
        if falling:
            last_decline = i
        if rising:
            last_rise = i
        def count(prefix: list[int], last: int) -> float:
            if last < 0:
                return math.nan
            width = i - last
            start = 0 if width == 0 else max(0, i - width + 1)
            return prefix[i + 1] - prefix[start]
        zt_buy = count(buys, last_decline) if rising else 0
        zt_sell = count(sells, last_decline) if rising else 0
        dt_buy = count(buys, last_rise) if falling else 0
        conditions = [
            (en2[i] and dt_buy == 1, '[类 2 买]', 'buy'),
            (up(p20, i) and zt_buy == 1, '[1 买]', 'buy'),
            (ex2[i] and rising and zt_sell == 1, '[2 卖]', 'sell'),
            (en2[i] and rising and zt_buy == 2, '[2 买]', 'buy'),
            ((ex2[i] and rising and zt_sell == 2)
             or ((zt_sell == 0 or zt_sell == 2) and ex2[i]), '[1 卖]', 'sell'),
        ]
        for condition, label, direction in conditions:
            if condition:
                result.append((row[0], label, direction,
                               row[3] * 0.97 if direction == 'buy' else row[2] * 1.03))
    return result


def _singleflight_cache(function):
    """Keep cache and synchronization inside a closure; serialize CPU work only."""
    cached = lru_cache(maxsize=300)(function)
    lock = RLock()

    @wraps(function)
    def guarded(*args, **kwargs):
        with lock:
            return cached(*args, **kwargs)

    def clear():
        with lock:
            cached.cache_clear()

    def info():
        with lock:
            return cached.cache_info()

    guarded.cache_clear = clear
    guarded.cache_info = info
    return guarded


@_singleflight_cache
def _confirmed(bars: tuple[tuple[str, float, float, float, float], ...]) -> tuple[tuple, ...]:
    baseline = _points(bars[:WARMUP_BARS])
    seen = {(row[0], row[1], row[2]) for row in baseline}
    events = []
    for length in range(WARMUP_BARS + 1, len(bars) + 1):
        for pivot_date, label, direction, price in _points(bars[:length]):
            key = (pivot_date, label, direction)
            if key not in seen:
                seen.add(key)
                events.append((pivot_date, bars[length - 1][0], price, label, direction))
    return tuple(events)


def confirm_daily(rows: Sequence[Mapping], target_date: str,
                  recent_dates: Sequence[str] | None = None) -> dict:
    """Return immutable first-visible candidates; exact target date is mandatory.

    recent_dates optionally supplies the report's last three market trading dates,
    so suspended stocks cannot expand the reporting window into older sessions.
    """
    source = {'formula': 'zhihu_zig10_zig20', 'warmup_bars': WARMUP_BARS,
              'max_bars': MAX_BARS, 'repainting': True, 'loaded_bars': 0,
              'loaded_window_start': None, 'baseline_date': None,
              'notice': '原公式含未来函数；首次可见候选可能回绘历史，不是经典三买或成交信号。'}
    def blocked(reason: str) -> dict:
        return {'status': 'BLOCKED', 'reason': reason, 'recent': [], 'today': [], 'source': source}
    try:
        if not isinstance(target_date, str) or date.fromisoformat(target_date).isoformat() != target_date:
            return blocked('invalid_target_date')
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows or len(rows) > MAX_BARS:
            return blocked('empty_or_oversized_history')
        bars = []
        previous = ''
        for row in rows:
            if not isinstance(row, Mapping):
                return blocked('invalid_bar')
            day = row.get('trade_date')
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day or day <= previous:
                return blocked('invalid_or_duplicate_dates')
            previous = day
            if day > target_date:
                continue
            values = [row.get(key) for key in ('open', 'high', 'low', 'close')]
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value <= 0 for value in values):
                return blocked('invalid_ohlc')
            op, high, low, close = map(float, values)
            if high < max(op, close, low) or low > min(op, close, high):
                return blocked('invalid_ohlc_range')
            bars.append((day, op, high, low, close))
        source['loaded_bars'] = len(bars)
        source['loaded_window_start'] = bars[0][0] if bars else None
        source['baseline_date'] = bars[WARMUP_BARS - 1][0] if len(bars) >= WARMUP_BARS else None
        if not bars or bars[-1][0] != target_date:
            return blocked('target_bar_missing')
        if len(bars) < WARMUP_BARS:
            return blocked('insufficient_warmup')
        if recent_dates is None:
            recent = [row[0] for row in bars[-3:]]
        else:
            if (isinstance(recent_dates, (str, bytes)) or not 1 <= len(recent_dates) <= 3
                    or list(recent_dates) != sorted(set(recent_dates))
                    or recent_dates[-1] != target_date
                    or any(date.fromisoformat(day).isoformat() != day for day in recent_dates)):
                return blocked('invalid_recent_calendar')
            recent = list(recent_dates)
        events = [{'pivot_date': pivot, 'confirmation_date': confirmation, 'price': price,
                   'label': label, 'direction': direction, 'repainting': True, 'candidate': True}
                  for pivot, confirmation, price, label, direction in _confirmed(tuple(bars))]
        return {'status': 'READY', 'reason': '', 'today': [event for event in events
                if event['confirmation_date'] == target_date], 'recent': [event for event in events
                if event['confirmation_date'] in recent], 'source': source}
    except (ValueError, TypeError, KeyError, OverflowError):
        return blocked('invalid_input')
