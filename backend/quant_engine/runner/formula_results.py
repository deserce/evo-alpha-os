"""Compute versioned daily formula screens and atomically publish each date."""

import json
import logging
from datetime import date
from uuid import uuid4

import pandas as pd
from sqlalchemy import bindparam, text

from quant_engine.strategies.rps_screeners import LOOKBACK_BARS, STRATEGIES, evaluate

logger = logging.getLogger(__name__)
MARKETS = "('SH', 'SZ', 'BSE')"
CREATE_RESULTS = """
CREATE TABLE IF NOT EXISTS quant_formula_results (
    strategy_name TEXT NOT NULL, strategy_display_name TEXT NOT NULL,
    strategy_description TEXT, strategy_logic TEXT, filter_criteria TEXT,
    result_type TEXT NOT NULL, trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL, meta_info TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_name, trade_date, symbol, result_type)
)
"""
HISTORY = text(f"""
    WITH bounds AS MATERIALIZED (
        SELECT value AS symbol, COALESCE((
            SELECT h.trade_date FROM stock_daily_prices h
            WHERE h.symbol=value AND h.trade_date<:start AND h.market IN {MARKETS}
            ORDER BY h.trade_date DESC LIMIT 1 OFFSET :offset
        ), '0001-01-01') AS first_day
        FROM json_each(:symbols)
    )
    SELECT p.symbol, p.trade_date, p.open, p.high, p.low, p.close, p.volume,
           r.rps_10, r.rps_20, r.rps_50, r.rps_120, r.rps_250
    FROM bounds b
    CROSS JOIN stock_daily_prices p ON p.symbol=b.symbol AND p.trade_date>=b.first_day
    LEFT JOIN quant_feature_rps r ON r.symbol=p.symbol AND r.trade_date=(
        SELECT f.trade_date FROM quant_feature_rps f
        WHERE f.symbol=p.symbol AND f.trade_date>=CAST(p.trade_date AS TEXT)
          AND f.trade_date<date(p.trade_date,'+1 day')
        ORDER BY f.trade_date LIMIT 1
    )
    WHERE p.trade_date<=:end AND p.market IN {MARKETS}
    ORDER BY p.symbol, p.trade_date
""")
AUDIT = text("""
    INSERT INTO strategy_run_log
        (run_id,strategy_name,trade_date,status,signal_count,error_message,started_at,finished_at)
    VALUES (:run_id,:strategy_name,:trade_date,:status,:signal_count,:error,
            :started,CURRENT_TIMESTAMP)
""")


def run_formula_screens(engine, trade_dates, *, missing_only=False):
    """Screen at most 20 explicit trading dates with bounded memory and no network."""
    dates = sorted({date.fromisoformat(day).isoformat() for day in trade_dates})
    if not dates or len(dates) > 20:
        raise ValueError("formula screening requires 1–20 distinct dates")
    with engine.begin() as conn:
        conn.execute(text(CREATE_RESULTS))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_formula_results_date "
            "ON quant_formula_results(trade_date, symbol)"
        ))
        if missing_only:
            completed = conn.execute(text("""
                SELECT a.trade_date, a.strategy_name, a.status FROM strategy_run_log a
                WHERE a.trade_date IN :dates AND a.strategy_name IN :names
                AND a.rowid=(
                    SELECT b.rowid FROM strategy_run_log b
                    WHERE b.strategy_name=a.strategy_name AND b.trade_date=a.trade_date
                    ORDER BY b.finished_at DESC, b.rowid DESC LIMIT 1
                )
            """).bindparams(bindparam("dates", expanding=True),
                            bindparam("names", expanding=True)),
                {"dates": dates, "names": list(STRATEGIES)})
            counts = dict.fromkeys(dates, 0)
            for day, _, status in completed:
                counts[day] += status == "success"
            dates = [day for day in dates if counts[day] != len(STRATEGIES)]
    if not dates:
        return {"success": True, "dates": {}}

    started = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S")
    results = {day: {name: [] for name in STRATEGIES} for day in dates}
    valid_dates = set()
    batch_error = None
    try:
        with engine.connect() as conn:
            target_days = text(f"""
                SELECT value FROM json_each(:dates)
                WHERE EXISTS (
                    SELECT 1 FROM stock_daily_prices p
                    WHERE p.trade_date=value AND p.market IN {MARKETS}
                ) AND EXISTS (
                    SELECT 1 FROM quant_feature_rps r
                    WHERE r.trade_date>=CAST(value AS TEXT)
                      AND r.trade_date<date(value,'+1 day') AND r.rps_50 IS NOT NULL
                )
            """)
            valid_dates = set(conn.execute(target_days, {"dates": json.dumps(dates)}).scalars())
            symbols = conn.execute(text(f"""
                SELECT DISTINCT symbol FROM stock_daily_prices
                WHERE trade_date IN :dates AND market IN {MARKETS}
                ORDER BY symbol
            """).bindparams(bindparam("dates", expanding=True)), {"dates": dates}).scalars().all()
            for offset in range(0, len(symbols), 128):
                frame = pd.read_sql(HISTORY, conn, params={
                    "symbols": json.dumps(symbols[offset:offset + 128]),
                    "start": dates[0], "end": dates[-1], "offset": LOOKBACK_BARS - 2,
                })
                if frame.empty:
                    continue
                target = frame.trade_date.isin(valid_dates)
                all_metrics = {}
                signals = evaluate(frame, metrics=all_metrics)
                for name, matches in signals.items():
                    metrics = all_metrics.get(name, {})
                    for idx in frame.index[target & matches]:
                        bar = frame.loc[idx]
                        details = {
                            key: round(float(values.loc[idx]), 6)
                            for key, values in metrics.items() if pd.notna(values.loc[idx])
                        }
                        results[bar.trade_date][name].append({
                            "strategy_name": name, "display_name": STRATEGIES[name],
                            "trade_date": bar.trade_date, "symbol": bar.symbol,
                            "meta_info": json.dumps(details, allow_nan=False),
                        })
    except Exception as exc:
        logger.exception("Formula screening failed")
        batch_error = str(exc)[:300]

    response = {}
    for day, by_name in results.items():
        error = batch_error or (None if day in valid_dates else "当日日 K 或 RPS 未就绪")
        with engine.begin() as conn:
            for name, rows in by_name.items():
                if error is None:
                    conn.execute(text(
                        "DELETE FROM quant_formula_results "
                        "WHERE strategy_name=:name AND trade_date=:day"
                    ), {"name": name, "day": day})
                    if rows:
                        conn.execute(text("""
                            INSERT INTO quant_formula_results (
                                strategy_name,strategy_display_name,trade_date,symbol,
                                result_type,signal_type,meta_info
                            ) VALUES (
                                :strategy_name,:display_name,:trade_date,:symbol,
                                'formula','FORMULA',:meta_info
                            )
                        """), rows)
                conn.execute(AUDIT, {
                    "run_id": uuid4().hex, "strategy_name": name, "trade_date": day,
                    "status": "failed" if error else "success",
                    "signal_count": 0 if error else len(rows),
                    "error": error, "started": started,
                })
        response[day] = {"success": error is None, "counts": {
            name: 0 if error else len(rows) for name, rows in by_name.items()
        }}
        logger.info("Formula screening %s %s", day, error or response[day]["counts"])
    return {"success": all(result["success"] for result in response.values()), "dates": response}
