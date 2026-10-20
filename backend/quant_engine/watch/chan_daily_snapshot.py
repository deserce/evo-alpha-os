"""Bounded, exact-day persistent snapshots of repainting Chan candidates.

Replay happens outside SQLite transactions; only the approved workflow writes.
Readers never initialize tables or calculate signals implicitly.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from quant_engine.watch.chan_daily_confirmation import confirm_daily, MAX_BARS

FORMULA_VERSION = 'zhihu_zig10_zig20_first_visible_v1'
MAX_SYMBOLS = 1000
MAX_PAYLOAD_BYTES = 131072


def _validate_date(value: str) -> None:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('trade_date 必须为 YYYY-MM-DD')


def _validate_symbols(symbols: Sequence[str]) -> None:
    if len(symbols) > MAX_SYMBOLS or any(not isinstance(s, str) or not re.fullmatch(r'\d{6}', s)
                                        for s in symbols):
        raise ValueError('成员必须为最多1000个六位股票代码')


def _fingerprint(rows: Sequence[Mapping], target: str, recent_dates: Sequence[str]) -> str:
    if len(rows) > MAX_BARS:
        raise ValueError('缠论历史行情超出有界窗口')
    # Future bars do not affect a historical snapshot. OHLC corrections do.
    bars = [[r.get(k) for k in ('trade_date', 'open', 'high', 'low', 'close')]
            for r in rows if isinstance(r, Mapping) and str(r.get('trade_date', '')) <= target]
    if any(not isinstance(r, Mapping) for r in rows):
        bars.append(['invalid_bar'])
    raw = json.dumps([FORMULA_VERSION, target, list(recent_dates), bars],
                     ensure_ascii=False, separators=(',', ':'), allow_nan=True)
    if len(raw.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError('行情输入过大')
    return hashlib.sha256(raw.encode()).hexdigest()


class ChanDailySnapshotStore:
    """One immutable-semantics row per symbol/day/formula, refreshed after corrections."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def _connect_read(self):
        return sqlite3.connect(self.db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)

    def read(self, symbol: str, trade_date: str, rows: Sequence[Mapping],
             recent_dates: Sequence[str]) -> dict | None:
        """Return only an exact formula and input match; absent storage is a miss."""
        _validate_date(trade_date)
        _validate_symbols([symbol])
        fingerprint = _fingerprint(rows, trade_date, recent_dates)
        result = self._load(trade_date, [symbol], fingerprint)
        return result.get(symbol)

    def load_date(self, trade_date: str, symbols: Sequence[str]) -> dict[str, dict]:
        """Read persisted exact-day rows; workflow must separately validate input freshness."""
        _validate_date(trade_date)
        _validate_symbols(symbols)
        return self._load(trade_date, list(dict.fromkeys(symbols)))

    def _load(self, target: str, symbols: Sequence[str], fingerprint: str | None = None) -> dict:
        if not symbols or not self.db_path.exists():
            return {}
        result = {}
        try:
            with closing(self._connect_read()) as conn:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                    "AND name='chan_daily_snapshot'").fetchone():
                    return {}
                for start in range(0, len(symbols), 200):
                    chunk = symbols[start:start+200]
                    query = ('SELECT symbol,input_hash,payload FROM chan_daily_snapshot '
                             'WHERE trade_date=? AND formula_version=? AND symbol IN (' +
                             ','.join('?' for _ in chunk) + ')')
                    for symbol, stored_hash, payload in conn.execute(query, [target, FORMULA_VERSION, *chunk]):
                        if fingerprint is not None and fingerprint != stored_hash:
                            continue
                        if len(payload.encode()) > MAX_PAYLOAD_BYTES:
                            raise ValueError('缠论快照超出预算')
                        value = json.loads(payload)
                        if value.get('status') not in ('READY', 'BLOCKED'):
                            raise ValueError('缠论快照状态无效')
                        result[symbol] = value
        except sqlite3.OperationalError:
            # Database busy/corruption must remain visible to the workflow caller.
            raise
        return result

    def run(self, trade_date: str, histories: Mapping[str, Sequence[Mapping]],
            recent_dates: Sequence[str]) -> dict:
        """Compute serially, then atomically save this bounded member batch."""
        _validate_date(trade_date)
        _validate_symbols(list(histories))
        if (not recent_dates or len(recent_dates)>3 or list(recent_dates)!=sorted(set(recent_dates))
                or recent_dates[-1]!=trade_date):
            raise ValueError('最近交易日必须有序且截至目标日')
        for day in recent_dates:
            _validate_date(day)
        saved = []
        reused = 0
        blocked = []
        computed_at = datetime.now(timezone.utc).isoformat()
        for symbol, rows in histories.items():
            fingerprint = _fingerprint(rows, trade_date, recent_dates)
            existing = self.read(symbol, trade_date, rows, recent_dates)
            if existing is not None:
                reused += 1
                value = existing
            else:
                value = confirm_daily(rows, trade_date, recent_dates)
                value['snapshot'] = dict(trade_date=trade_date, symbol=symbol,
                                         formula_version=FORMULA_VERSION, input_hash=fingerprint,
                                         computed_at=computed_at)
                payload = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                if len(payload.encode()) > MAX_PAYLOAD_BYTES:
                    raise ValueError('缠论结果超出预算')
                saved.append((trade_date,symbol,FORMULA_VERSION,fingerprint,value['status'],payload,computed_at))
            if value['status'] != 'READY':
                blocked.append(symbol)
        with closing(sqlite3.connect(self.db_path, timeout=5)) as conn:
            with conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS chan_daily_snapshot (
                    trade_date TEXT NOT NULL, symbol TEXT NOT NULL, formula_version TEXT NOT NULL,
                    input_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('READY','BLOCKED')),
                    payload TEXT NOT NULL, computed_at TEXT NOT NULL,
                    PRIMARY KEY(trade_date,symbol,formula_version))''')
                conn.executemany('''INSERT INTO chan_daily_snapshot VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(trade_date,symbol,formula_version) DO UPDATE SET
                    input_hash=excluded.input_hash,status=excluded.status,
                    payload=excluded.payload,computed_at=excluded.computed_at''', saved)
        return dict(trade_date=trade_date, status='READY' if histories and not blocked else 'BLOCKED',
                    members=len(histories), computed=len(saved), reused=reused, blocked=blocked,
                    formula_version=FORMULA_VERSION)
