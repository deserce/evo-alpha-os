"""Read-only Strategy C exit adapter for real, revision-scoped positions."""
from datetime import date

from quant_engine.research.structure_turning import (
    VERSION, StructureConfig, _indicator_evidence, evaluate_structure_turning,
)

HISTORY_ANCHOR = "2023-01-01"
MAX_BARS = 1500


def evaluate_position_structure(prices: list[dict], *, entry_date: str) -> dict:
    """Evaluate only the latest completed day, counting weakness after real entry.

    All price comparisons use one qfq series; raw account cost is never compared
    with adjusted structure levels. A structure binds only if C was HOLDING at
    actual entry, preventing a later simulated BUY from rewriting real risk.
    """
    if date.fromisoformat(entry_date).isoformat() != entry_date:
        raise ValueError("entry_date must be an ISO day")
    if len(prices) > MAX_BARS:
        raise ValueError("Strategy C history exceeds bounded bar limit")
    result = evaluate_structure_turning(prices)
    base = dict(strategy="C", version=VERSION, exit=False, warning=False,
                reason="", stop_price=None, stop_binding="UNBOUND", weak_days=0)
    if result['summary']['status'] != 'ok' or not prices:
        return dict(base, data_status="INSUFFICIENT")
    if not any(row['trade_date'] == entry_date for row in prices):
        return dict(base, data_status="ENTRY_HISTORY_MISSING")
    entry_rows = [row for row in prices if row['trade_date'] <= entry_date]
    entry_state = evaluate_structure_turning(entry_rows)['state']
    stop = entry_state['stop_price'] if entry_state['status'] == 'HOLDING' else None
    cfg = StructureConfig()
    indicators = _indicator_evidence(prices, cfg.divergence_lookback, cfg.divergence_min_gap)
    weak_days, top_warning_index = 0, None
    closes = [float(row['close']) for row in prices]
    new_top_warning = False
    for i, row in enumerate(prices):
        if row['trade_date'] < entry_date or i < 24:
            continue
        improving = indicators[i]['macd_hist'] > indicators[i-1]['macd_hist']
        ma10 = sum(closes[i-9:i+1])/10
        weak_days = weak_days+1 if closes[i] < ma10 and not improving else 0
        top_divergence = indicators[i]['top_hist_divergence']
        new_top_warning = bool(top_divergence and (top_warning_index is None or
                               i-top_warning_index > cfg.divergence_setup_window))
        if top_divergence:
            top_warning_index = i
    i = len(prices)-1
    close = closes[-1]
    ma5, ma10 = sum(closes[-5:])/5, sum(closes[-10:])/10
    improving = indicators[i]['macd_hist'] > indicators[i-1]['macd_hist']
    top_exit = (top_warning_index is not None and
                i-top_warning_index <= cfg.divergence_setup_window and
                indicators[i]['price_breakdown'] and close < ma5 and not improving)
    stopped = stop is not None and close < stop
    reason = ('收盘跌破结构失效线' if stopped else
              '顶背离预警后收盘跌破前日低点及MA5，MACD动能继续走弱' if top_exit else
              '连续两日失守MA10且MACD动能走弱' if weak_days >= 2 else
              '价格创新高但MACD正柱低于参考高点，顶背离预警' if new_top_warning else
              '收盘失守MA10且MACD动能走弱，等待下一日确认' if weak_days == 1 else '')
    return dict(base, data_status="OK", exit=bool(stopped or top_exit or weak_days >= 2),
                warning=bool(weak_days == 1 or new_top_warning), reason=reason,
                stop_price=stop, stop_binding="BOUND_AT_ENTRY" if stop is not None else "UNBOUND",
                weak_days=weak_days, qfq_close=close, ma5=ma5, ma10=ma10,
                macd_hist=indicators[i]['macd_hist'],
                macd_hist_previous=indicators[i-1]['macd_hist'],
                top_warning_date=prices[top_warning_index]['trade_date']
                if top_warning_index is not None else None,
                structure_note="结构止损线已绑定" if stop is not None else "结构止损线未绑定")
