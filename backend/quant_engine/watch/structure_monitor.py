"""Bounded post-close C monitoring, isolated from A/B, mail and real positions."""
from __future__ import annotations

from collections import Counter
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from quant_engine.research.structure_turning import VERSION, evaluate_structure_turning

HISTORY_ANCHOR = '2023-01-01'
MAX_BARS = 1500
MAX_SYMBOLS = 300
SCOPE = 'all_watchlist_pools'


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _day(value: str) -> str:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value or value < HISTORY_ANCHOR or parsed == date.max:
        raise ValueError('trade_date must be an ISO day from 2023-01-01 through 9999-12-30')
    return value


def _exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)
    ).fetchone() is not None


class StructureMonitor:
    """Replay one symbol at a time; persist only the requested day's state/events."""

    def __init__(self, db_path: Path | str, config: dict | None = None):
        self.db_path = Path(db_path)
        self.lock_path = self.db_path.parent / '.daily_data_workflow.lock'
        self.config = evaluate_structure_turning([], config)['config']
        self.config_hash = hashlib.sha256(_json(self.config).encode()).hexdigest()[:16]

    @staticmethod
    def ensure_schema(conn: sqlite3.Connection) -> None:
        """Create only C-owned tables; caller owns the writer lock and commit."""
        statements = (
            '''CREATE TABLE IF NOT EXISTS structure_monitor_runs (
                trade_date TEXT NOT NULL, version TEXT NOT NULL, config_hash TEXT NOT NULL,
                status TEXT NOT NULL, scope TEXT NOT NULL, config_json TEXT NOT NULL,
                stats_json TEXT NOT NULL, elapsed_seconds REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (trade_date, version, config_hash))''',
            '''CREATE TABLE IF NOT EXISTS structure_monitor_snapshots (
                trade_date TEXT NOT NULL, symbol TEXT NOT NULL, version TEXT NOT NULL,
                config_hash TEXT NOT NULL, name TEXT NOT NULL, pool_ids TEXT NOT NULL,
                pool_names TEXT NOT NULL, status TEXT NOT NULL, data_status TEXT NOT NULL,
                reason TEXT NOT NULL, current_price REAL, state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (trade_date,symbol,version,config_hash))''',
            '''CREATE INDEX IF NOT EXISTS idx_structure_monitor_symbol
                ON structure_monitor_snapshots(symbol,version,config_hash,trade_date DESC)''',
            '''CREATE TABLE IF NOT EXISTS structure_monitor_events (
                event_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
                version TEXT NOT NULL, config_hash TEXT NOT NULL, name TEXT NOT NULL,
                pool_ids TEXT NOT NULL, pool_names TEXT NOT NULL, event_type TEXT NOT NULL,
                setup_id TEXT, trade_id TEXT, payload TEXT NOT NULL, created_at TEXT NOT NULL)''',
            '''CREATE INDEX IF NOT EXISTS idx_structure_monitor_events_date
                ON structure_monitor_events(trade_date,version,config_hash)''',
        )
        for statement in statements:
            conn.execute(statement)

    @contextmanager
    def _writer_lock(self, target: str, workflow_child: bool):
        with self.lock_path.open('a+') as handle:
            acquired = False
            try:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError as exc:
                    handle.seek(0)
                    try:
                        metadata = json.load(handle)
                    except (ValueError, TypeError):
                        metadata = {}
                    inherited = (
                        isinstance(metadata, dict) and workflow_child and metadata.get('pid') == os.getppid()
                        and metadata.get('stage') == 'structure_monitor'
                        and metadata.get('target_trade_date') == target
                    )
                    if not inherited:
                        raise RuntimeError('daily workflow writer lock is busy') from exc
                yield
            finally:
                if acquired:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _members(self, conn: sqlite3.Connection) -> dict[str, dict]:
        if not _exists(conn, 'stock_pool_members') or not _exists(conn, 'stock_pools'):
            return {}
        symbols = [str(row[0]) for row in conn.execute(
            'SELECT DISTINCT m.symbol FROM stock_pool_members m '
            'JOIN stock_pools p ON p.pool_id=m.pool_id ORDER BY m.symbol LIMIT ?',
            (MAX_SYMBOLS + 1,),
        )]
        if len(symbols) > MAX_SYMBOLS:
            raise ValueError('C monitor scope exceeds 300 symbols; no partial run was saved')
        if any(not re.fullmatch(r'[0-9]{6}', symbol) for symbol in symbols):
            raise ValueError('C monitor members contain an invalid stock symbol')
        members = {s: {'symbol': s, 'name': s, 'pool_ids': [], 'pool_names': []} for s in symbols}
        if not symbols:
            return members
        placeholders = ','.join('?' for _ in symbols)
        rows = conn.execute(
            'SELECT m.symbol,p.pool_id,p.name FROM stock_pool_members m '
            f'JOIN stock_pools p ON p.pool_id=m.pool_id WHERE m.symbol IN ({placeholders}) '
            'ORDER BY m.symbol,p.pool_id LIMIT 6001', symbols,
        ).fetchall()
        if len(rows) > 6000:
            raise ValueError('C monitor group membership exceeds bounded limit')
        for symbol, pool_id, name in rows:
            member = members[symbol]
            if pool_id not in member['pool_ids']:
                member['pool_ids'].append(pool_id)
                member['pool_names'].append(name or pool_id)
        if _exists(conn, 'stock_info'):
            for symbol, name in conn.execute(
                f'SELECT symbol,name FROM stock_info WHERE symbol IN ({placeholders}) LIMIT 300',
                symbols,
            ):
                members[symbol]['name'] = name or symbol
        return members

    @staticmethod
    def _quality_statuses(conn: sqlite3.Connection, target: str) -> dict[str, str]:
        if not _exists(conn, 'data_quality_report'):
            return {}
        rows = conn.execute(
            """WITH normalized AS (
                SELECT status,created_at,rowid AS report_rowid,
                    CASE WHEN UPPER(market) IN ('BJ','BSE') THEN 'BSE'
                         ELSE UPPER(market) END AS canonical_market
                FROM data_quality_report WHERE trade_date=? AND module='stock_daily'
            ), ranked AS (
                SELECT canonical_market,status,ROW_NUMBER() OVER (
                    PARTITION BY canonical_market ORDER BY created_at DESC,report_rowid DESC) AS n
                FROM normalized
            ) SELECT canonical_market,status FROM ranked WHERE n=1""", (target,),
        ).fetchall()
        return dict(rows)

    @staticmethod
    def _market(symbol: str) -> str:
        if symbol.startswith('6'):
            return 'SH'
        if symbol.startswith(('0', '3')):
            return 'SZ'
        if symbol.startswith(('4', '8', '92')):
            return 'BSE'
        return 'UNKNOWN'

    @classmethod
    def _quality_reason(cls, statuses: dict[str, str], symbol: str) -> str | None:
        accepted = {'PASS', 'OK', 'WARNING'}
        if 'ALL' in statuses and statuses['ALL'] not in accepted:
            return 'stock_daily_quality_failed:ALL'
        market = cls._market(symbol)
        if market not in statuses:
            return 'stock_daily_quality_missing:' + market
        if statuses[market] not in accepted:
            return 'stock_daily_quality_failed:' + market
        return None

    def _last_known(self, conn: sqlite3.Connection, symbol: str, target: str) -> dict | None:
        row = conn.execute(
            'SELECT state_json FROM structure_monitor_snapshots WHERE symbol=? '
            "AND version=? AND config_hash=? AND trade_date<=? "
            "AND status<>'REMOVED' ORDER BY trade_date DESC LIMIT 1",
            (symbol, VERSION, self.config_hash, target),
        ).fetchone()
        if not row:
            return None
        state = json.loads(row[0])
        return state.get('last_known_state') if state.get('status') == 'BLOCKED' else state

    def run(self, trade_date: str, *, workflow_child: bool = False) -> dict:
        """Write one exact day under the existing workflow lock. Never send mail."""
        target = _day(trade_date)
        end = (date.fromisoformat(target) + timedelta(days=1)).isoformat()
        started = time.monotonic()
        with self._writer_lock(target, workflow_child), closing(sqlite3.connect(
            self.db_path.resolve().as_uri() + '?mode=rw', uri=True, timeout=2
        )) as conn:
            conn.row_factory = sqlite3.Row
            self.ensure_schema(conn)
            if _exists(conn, 'strategy_run_log') and _exists(conn, 'quant_preselect_results'):
                from quant_engine.watch.smart_mrgc_pool import refresh
                refresh(conn, target)
            members = self._members(conn)
            quality_statuses = self._quality_statuses(conn, target)
            prices_exist = _exists(conn, 'stock_daily_prices')
            snapshots, events = [], []
            now = datetime.now().isoformat(timespec='seconds')
            for symbol, member in members.items():
                reason = self._quality_reason(quality_statuses, symbol)
                result = None
                prices = []
                if time.monotonic() - started > 90:
                    reason = 'monitor_time_budget_exceeded'
                if not reason:
                    prices = conn.execute(
                        'SELECT substr(trade_date,1,10) AS trade_date,open,high,low,close,volume '
                        'FROM stock_daily_prices WHERE symbol=? AND trade_date>=? '
                        'AND trade_date<? ORDER BY trade_date LIMIT ?',
                        (symbol, HISTORY_ANCHOR, end, MAX_BARS + 1),
                    ).fetchall() if prices_exist else []
                    if not prices or prices[-1]['trade_date'] != target:
                        reason = 'target_daily_bar_missing'
                    elif len(prices) > MAX_BARS:
                        reason = 'history_bar_limit_exceeded'
                    else:
                        try:
                            result = evaluate_structure_turning([dict(p) for p in prices], self.config)
                            if result['summary']['status'] != 'ok':
                                reason = 'insufficient_history'
                        except ValueError as exc:
                            reason = 'invalid_ohlcv: ' + str(exc)[:160]
                if reason:
                    state = {'status': 'BLOCKED', 'trade_date': target,
                             'waiting_for': ['等待当日可信日线数据'],
                             'last_known_state': self._last_known(conn, symbol, target)}
                    current_price = None
                else:
                    state = result['state']
                    current_price = float(prices[-1]['close'])
                    for marker in result['markers']:
                        if marker['trade_date'] != target:
                            continue
                        key = [symbol, target, marker['marker_type'], marker.get('setup_id'),
                               marker.get('trade_id'), VERSION, self.config_hash]
                        events.append(dict(
                            member, event_id=hashlib.sha256(_json(key).encode()).hexdigest(),
                            trade_date=target, version=VERSION, config_hash=self.config_hash,
                            event_type=marker['marker_type'], setup_id=marker.get('setup_id'),
                            trade_id=marker.get('trade_id'),
                            payload={**marker, 'simulation': True, 'execution': 'NONE',
                                     'source': 'structure_monitor_daily'}, created_at=now,
                        ))
                snapshots.append(dict(
                    member, trade_date=target, version=VERSION, config_hash=self.config_hash,
                    status=state['status'], data_status='BLOCKED' if reason else 'OK',
                    reason=reason or '', current_price=current_price, state=state, updated_at=now,
                ))
            # Persist an explicit removed state, without inventing a strategy exit.
            prior_date = conn.execute(
                'SELECT MAX(trade_date) FROM structure_monitor_runs '
                'WHERE trade_date<=? AND version=? AND config_hash=?',
                (target, VERSION, self.config_hash),
            ).fetchone()[0]
            prior = conn.execute(
                'SELECT * FROM structure_monitor_snapshots WHERE trade_date=? '
                "AND version=? AND config_hash=? AND (status<>'REMOVED' OR trade_date=?) LIMIT 600",
                (prior_date, VERSION, self.config_hash, target),
            ).fetchall() if prior_date else []
            for old in prior:
                if old['symbol'] not in members:
                    snapshots.append(dict(
                        symbol=old['symbol'], name=old['name'], pool_ids=json.loads(old['pool_ids']),
                        pool_names=json.loads(old['pool_names']), trade_date=target, version=VERSION,
                        config_hash=self.config_hash, status='REMOVED', data_status='REMOVED',
                        reason='symbol_removed_from_all_pools', current_price=None,
                        state={'status': 'REMOVED', 'trade_date': target,
                               'waiting_for': ['已移出所有自选分组，停止C监控']}, updated_at=now,
                    ))
            counts = dict(Counter(row['status'] for row in snapshots))
            counts.update(total=len(members), events=len(events))
            with conn:
                self._save(conn, target, snapshots, events, now)
                conn.execute(
                    '''INSERT OR REPLACE INTO structure_monitor_runs VALUES (?,?,?,?,?,?,?,?,?)''',
                    (target, VERSION, self.config_hash,
                     'BLOCKED' if counts.get('BLOCKED') else 'COMPLETED', SCOPE,
                     _json(self.config), _json(counts), round(time.monotonic()-started, 4), now),
                )
                # C-owned rolling one-year history, bounded cleanup batches.
                cutoff = (date.fromisoformat(target)-timedelta(days=365)).isoformat()
                for table in ('structure_monitor_snapshots', 'structure_monitor_events',
                              'structure_monitor_runs'):
                    conn.execute(
                        f'DELETE FROM {table} WHERE rowid IN '
                        f'(SELECT rowid FROM {table} WHERE trade_date<? LIMIT 5000)', (cutoff,),
                    )
        return self.read_status(target)

    def _save(self, conn, target, snapshots, events, now):
        conn.execute(
            'DELETE FROM structure_monitor_snapshots WHERE trade_date=? AND version=? AND config_hash=?',
            (target, VERSION, self.config_hash),
        )
        for row in snapshots:
            conn.execute(
                'INSERT INTO structure_monitor_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (target,row['symbol'],VERSION,self.config_hash,row['name'],_json(row['pool_ids']),
                 _json(row['pool_names']),row['status'],row['data_status'],row['reason'],
                 row['current_price'],_json(row['state']),now),
            )
        ids = [event['event_id'] for event in events]
        predicate = ' AND event_id NOT IN (' + ','.join('?' for _ in ids) + ')' if ids else ''
        conn.execute(
            'DELETE FROM structure_monitor_events WHERE trade_date=? AND version=? AND config_hash=?'
            + predicate, (target, VERSION, self.config_hash, *ids),
        )
        for event in events:
            conn.execute(
                '''INSERT INTO structure_monitor_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(event_id) DO UPDATE SET name=excluded.name,
                   pool_ids=excluded.pool_ids,pool_names=excluded.pool_names,payload=excluded.payload''',
                (event['event_id'],target,event['symbol'],VERSION,self.config_hash,event['name'],
                 _json(event['pool_ids']),_json(event['pool_names']),event['event_type'],
                 event['setup_id'],event['trade_id'],_json(event['payload']),now),
            )

    def read_status(self, trade_date: str | None = None, *, symbol: str | None = None,
                    pool_id: str | None = None) -> dict:
        """Read saved C state without schema creation or a write-capable connection."""
        if trade_date is not None:
            _day(trade_date)
        if symbol is not None and not re.fullmatch(r'[0-9]{6}', symbol):
            raise ValueError('symbol must be six digits')
        base = dict(run=None, trade_date=trade_date, version=VERSION, config_hash=self.config_hash,
                    scope=SCOPE, counts={'total': 0, 'events': 0}, rows=[], events=[])
        if not self.db_path.exists():
            return base
        with closing(sqlite3.connect(self.db_path.resolve().as_uri()+'?mode=ro',
                                     uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            if not _exists(conn, 'structure_monitor_runs'):
                return base
            target = trade_date or conn.execute(
                'SELECT MAX(trade_date) FROM structure_monitor_runs WHERE version=? AND config_hash=?',
                (VERSION, self.config_hash),
            ).fetchone()[0]
            run = conn.execute(
                'SELECT * FROM structure_monitor_runs WHERE trade_date=? AND version=? AND config_hash=?',
                (target, VERSION, self.config_hash),
            ).fetchone()
            if run is None:
                return base
            base['trade_date'] = target
            base['run'] = dict(run)
            base['run']['stats'] = json.loads(base['run'].pop('stats_json'))
            base['run']['config'] = json.loads(base['run'].pop('config_json'))
            predicate = 'trade_date=? AND version=? AND config_hash=?'
            params = [target, VERSION, self.config_hash]
            if symbol:
                predicate += ' AND symbol=?'
                params.append(symbol)
            if pool_id:
                predicate += ' AND EXISTS (SELECT 1 FROM json_each(pool_ids) WHERE value=?)'
                params.append(pool_id)
            for table, key, limit in [('structure_monitor_snapshots','rows',600),
                                      ('structure_monitor_events','events',1500)]:
                rows = conn.execute(f'SELECT * FROM {table} WHERE {predicate} ORDER BY symbol LIMIT ?',
                                    [*params, limit]).fetchall()
                for row in rows:
                    item = dict(row)
                    for field in ('pool_ids', 'pool_names', 'payload'):
                        if field in item:
                            item[field] = json.loads(item[field])
                    if 'state_json' in item:
                        item['state'] = json.loads(item.pop('state_json'))
                    base[key].append(item)
            counts = dict(Counter(row['status'] for row in base['rows']))
            counts.update(total=sum(row['status'] != 'REMOVED' for row in base['rows']),
                          events=len(base['events']))
            base['counts'] = counts
        return base
