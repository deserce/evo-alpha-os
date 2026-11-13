"""Evaluate personal watchlist symbols without publishing shared events."""
import argparse
import json
import sqlite3
from time import monotonic
from contextlib import closing
from pathlib import Path

from quant_engine.watch.engine import WatchEngine, WatchMember
from quant_engine.watch.rules import load_active_rules


def calculate(market: Path, accounts: Path, trade_date: str, limit: int = 100) -> int:
    engine = WatchEngine(market)
    trade_date = engine._exact_historical_date(trade_date)
    with closing(sqlite3.connect(market.resolve().as_uri() + "?mode=ro", uri=True)) as source, closing(
        sqlite3.connect(accounts)
    ) as target, target:
        source.row_factory = sqlite3.Row
        if engine._output_date(source, trade_date) != trade_date:
            raise ValueError("Trading date unavailable")
        has_groups = target.execute("SELECT 1 FROM sqlite_master WHERE name='personal_group_members'").fetchone()
        rules = load_active_rules(source)
        target.execute("""CREATE TABLE IF NOT EXISTS personal_watch_results (
            trade_date TEXT, version INTEGER, symbol TEXT, result TEXT NOT NULL,
            PRIMARY KEY (trade_date, version, symbol))""")
        candidates = set()
        for table in ("quant_preselect_results", "quant_formula_results"):
            if source.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                candidates.update(row[0] for row in source.execute(
                    f"SELECT DISTINCT symbol FROM {table} WHERE trade_date=?", (trade_date,)
                ))
        if has_groups:
            candidates.update(row[0] for row in target.execute("""SELECT DISTINCT m.symbol
                FROM personal_group_members m JOIN personal_groups g ON g.id=m.group_id
                JOIN users u ON u.id=g.user_id AND u.active=1"""))
        completed = {row[0] for row in target.execute(
            "SELECT symbol FROM personal_watch_results WHERE trade_date=? AND version=?",
            (trade_date, rules.version),
        )}
        symbols = sorted(candidates - completed)[:max(1, min(limit, 100))]
        target.commit()
        count = 0
        for symbol in symbols:
            shared = source.execute("""SELECT * FROM watch_daily_snapshot
                WHERE trade_date=? AND rule_version=? AND symbol=? AND data_status='OK'""",
                (trade_date, rules.version, symbol)).fetchone()
            if shared:
                snapshot = dict(shared)
            else:
                member = WatchMember(symbol, "", "", None)
                lifecycle = engine._lifecycle(source, symbol, trade_date)
                prices = engine._prices(source, symbol, trade_date)
                prior = target.execute("""SELECT result FROM personal_watch_results
                    WHERE symbol=? AND version=? AND trade_date<? ORDER BY trade_date DESC LIMIT 1""",
                    (symbol, rules.version, trade_date)).fetchone()
                previous = json.loads(prior[0]) if prior else None
                errors = engine._data_errors(lifecycle, prices, trade_date)
                snapshot = (
                    engine._unknown_snapshot(member, trade_date, lifecycle, prices, previous,
                                             errors, rules.version)
                    if errors else engine._evaluate(source, member, trade_date, lifecycle or {},
                                                   prices, previous, rules)
                )
            target.execute("INSERT OR IGNORE INTO personal_watch_results VALUES (?,?,?,?)",
                           (trade_date, rules.version, symbol, json.dumps(snapshot, default=str)))
            target.commit()
            count += 1
        return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    deadline = monotonic() + 45
    total = 0
    while monotonic() < deadline:
        count = calculate(args.market, args.accounts, args.date)
        total += count
        if count < 100:
            break
    print(f"Personal Watchlist: {total} processed")
