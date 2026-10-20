"""Bounded exact-date C replay for archives; the source database is read-only."""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable

from quant_engine.research.structure_turning import VERSION, evaluate_structure_turning
from quant_engine.watch.structure_monitor import (
    HISTORY_ANCHOR, MAX_BARS, MAX_SYMBOLS, StructureMonitor, _day, _exists, _json,
)


def calculate_history(
    db_path: Path | str,
    trade_date: str,
    symbols: list[str],
    config: dict | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Return report-compatible snapshots/events without creating formal records."""
    target = _day(trade_date)
    members = list(dict.fromkeys(symbols))
    if len(members) > MAX_SYMBOLS:
        raise ValueError('historical replay is limited to 300 symbols')
    if any(not isinstance(symbol, str) or not re.fullmatch(r'\d{6}', symbol)
           for symbol in members):
        raise ValueError('symbols must be six-digit stock codes')
    monitor = StructureMonitor(db_path, config)
    end = (date.fromisoformat(target) + timedelta(days=1)).isoformat()
    started = time.monotonic()
    snapshots, events = {}, {}
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro',
                                 uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        quality = monitor._quality_statuses(conn, target)
        has_prices = _exists(conn, 'stock_daily_prices')
        has_info = _exists(conn, 'stock_info')
        for index, symbol in enumerate(members):
            reason = monitor._quality_reason(quality, symbol)
            result, prices = None, []
            if time.monotonic() - started >= 90:
                reason = 'history_time_budget_exceeded'
            if not reason:
                prices = conn.execute(
                    'SELECT substr(trade_date,1,10) AS trade_date,open,high,low,close,volume '
                    'FROM stock_daily_prices WHERE symbol=? AND trade_date>=? '
                    'AND trade_date<? ORDER BY trade_date LIMIT ?',
                    (symbol, HISTORY_ANCHOR, end, MAX_BARS + 1),
                ).fetchall() if has_prices else []
                if not prices or prices[-1]['trade_date'] != target:
                    reason = 'target_daily_bar_missing'
                elif len(prices) > MAX_BARS:
                    reason = 'history_bar_limit_exceeded'
                else:
                    try:
                        result = evaluate_structure_turning([dict(row) for row in prices],
                                                            monitor.config)
                        if result['summary']['status'] != 'ok':
                            reason = 'insufficient_history'
                    except ValueError as exc:
                        reason = 'invalid_ohlcv: ' + str(exc)[:160]
            state = ({'status': 'BLOCKED', 'trade_date': target,
                      'waiting_for': ['等待当日可信日线数据']} if reason else result['state'])
            name_row = conn.execute('SELECT name FROM stock_info WHERE symbol=? LIMIT 1',
                                    (symbol,)).fetchone() if has_info else None
            snapshots[symbol] = dict(
                symbol=symbol, name=(name_row[0] if name_row else None) or symbol,
                trade_date=target, version=VERSION, config_hash=monitor.config_hash,
                status=state['status'], data_status='BLOCKED' if reason else 'OK',
                reason=reason or '', current_price=None if reason else float(prices[-1]['close']),
                state_json=_json(state),
            )
            events[symbol] = [dict(symbol=symbol, trade_date=target,
                                  event_type=marker['marker_type'],
                                  reason=marker.get('reason', ''), price=marker.get('price'))
                              for marker in (result['markers'] if not reason else [])
                              if marker['trade_date'] == target and marker['marker_type'] in
                              {'BUY_CONFIRM', 'EXIT', 'INVALIDATED', 'RISK_WARNING'}]
            if progress is not None:
                progress(index + 1, len(members))
    return snapshots, events
