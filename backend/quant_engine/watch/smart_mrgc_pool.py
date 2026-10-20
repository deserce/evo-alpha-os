"""Maintain a bounded, transactional twenty-trading-day MRGC watchlist."""
from __future__ import annotations

from datetime import date, datetime
import json
import re
import sqlite3
from zoneinfo import ZoneInfo

POOL_ID = 'smart_mrgc_20d'
POOL_NAME = 'MRGC · 近20日'
MAX_CANDIDATES = 1000
WINDOW_DAYS = 20


def resolve_members(conn: sqlite3.Connection, target: str) -> dict:
    """Resolve a historical window without writing or borrowing future results.

    Calendar output is capped at twenty dates and candidates at 1,001 rows.
    A blocked window never exposes a partial membership as a complete pool.
    """
    if date.fromisoformat(target).isoformat() != target:
        raise ValueError('target must be an ISO date')
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    days = []
    calendar_source = 'stock_daily_prices'
    reason = None
    missing = []
    candidates = []
    if not {'stock_daily_prices', 'strategy_run_log', 'quant_preselect_results'} <= tables:
        reason = 'source_tables_missing'
    else:
        calendar_source = 'trading_calendar' if 'trading_calendar' in tables else 'stock_daily_prices'
        predicate = 'AND is_open=1' if calendar_source == 'trading_calendar' else ''
        days = [row[0] for row in conn.execute(
            f'SELECT DISTINCT substr(trade_date,1,10) FROM {calendar_source} '
            f'WHERE trade_date<? {predicate} ORDER BY 1 DESC LIMIT ?',
            (target + 'T', WINDOW_DAYS))]
        if len(days) != WINDOW_DAYS or days[0] != target:
            reason = 'source_window_incomplete'
        else:
            placeholders = ','.join('?' for _ in days)
            completed = {row[0] for row in conn.execute(
                'SELECT DISTINCT substr(trade_date,1,10) FROM strategy_run_log '
                "WHERE strategy_name='mrgc_v1' AND status='success' "
                f'AND substr(trade_date,1,10) IN ({placeholders})', days)}
            source_days = {row[0] for row in conn.execute(
                'SELECT DISTINCT substr(trade_date,1,10) FROM stock_daily_prices '
                'WHERE trade_date>=? AND trade_date<?', (min(days), target + 'T'))}
            missing = sorted(set(days) - completed)
            source_missing = sorted(set(days) - source_days)
            if source_missing:
                reason = 'source_dates_missing'
                missing = sorted(set(missing) | set(source_missing))
            elif missing:
                reason = 'mrgc_dates_missing'
            else:
                candidates = conn.execute(
                    'SELECT symbol,MAX(substr(trade_date,1,10)),COUNT(DISTINCT substr(trade_date,1,10)) '
                    "FROM quant_preselect_results WHERE strategy_name='mrgc_v1' "
                    f'AND substr(trade_date,1,10) IN ({placeholders}) '
                    'GROUP BY symbol ORDER BY symbol LIMIT ?', [*days, MAX_CANDIDATES + 1]).fetchall()
                if len(candidates) > MAX_CANDIDATES:
                    reason = 'candidate_capacity_exceeded'
                elif any(not re.fullmatch(r'[0-9]{6}', str(row[0])) for row in candidates):
                    reason = 'invalid_candidate_symbol'
    members = [] if reason else [
        dict(symbol=symbol, last_hit_date=latest, hit_count=count)
        for symbol, latest, count in candidates]
    return dict(pool_id=POOL_ID, status='BLOCKED' if reason else 'READY',
                target_trade_date=target, dates=days,
                window_start=min(days) if days else None,
                window_end=max(days) if days else None, window_days=len(days),
                calendar_source=calendar_source, missing_dates=missing,
                reason=reason, members=members, member_count=len(members))


def refresh(conn: sqlite3.Connection, target: str, *, activation_date: str | None = None) -> dict:
    """Update members without committing; caller owns writer lock and transaction."""
    if date.fromisoformat(target).isoformat() != target:
        raise ValueError('target must be an ISO date')
    enabled_date = activation_date or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    if date.fromisoformat(enabled_date).isoformat() != enabled_date:
        raise ValueError('activation_date must be an ISO date')
    conn.execute('''CREATE TABLE IF NOT EXISTS smart_watchlist_state (
        pool_id TEXT PRIMARY KEY, status TEXT NOT NULL, target_trade_date TEXT NOT NULL,
        window_start TEXT, window_end TEXT, initialized_date TEXT,
        member_count INTEGER NOT NULL, reason TEXT, updated_at TEXT NOT NULL, dates_json TEXT NOT NULL)''')
    prior = conn.execute('SELECT * FROM smart_watchlist_state WHERE pool_id=?',
                         (POOL_ID,)).fetchone()
    if prior and target < prior[2]:
        columns = [column[1] for column in conn.execute('PRAGMA table_info(smart_watchlist_state)')]
        return dict(zip(columns, prior))
    now = datetime.now().isoformat(timespec='seconds')
    conn.execute('''INSERT INTO stock_pools (
        pool_id,name,description,sort_order,is_default,monitor_enabled,legacy_migrated,
        created_at,updated_at) VALUES (?,?,?,100,0,0,1,?,?)
        ON CONFLICT(pool_id) DO UPDATE SET name=excluded.name,monitor_enabled=0,
        description=excluded.description,updated_at=excluded.updated_at''',
        (POOL_ID, POOL_NAME, '自动收集最近20个交易日MRGC命中股票，仅监控C结构信号', now, now))
    initialized = prior[5] if prior else None
    resolved = resolve_members(conn, target)
    days = resolved['dates']
    calendar_source = resolved['calendar_source']
    reason = resolved['reason']
    missing = resolved['missing_dates']
    candidates = [(item['symbol'], item['last_hit_date'], item['hit_count'])
                  for item in resolved['members']]
    if not reason:
        other = {row[0] for row in conn.execute(
            'SELECT DISTINCT symbol FROM stock_pool_members WHERE pool_id<>? LIMIT 301', (POOL_ID,))}
        if len(other | {row[0] for row in candidates}) > 300:
            reason = 'c_monitor_capacity_exceeded'
    if not reason:
        # Keep caller rollback available while making the membership replacement atomic.
        conn.execute('SAVEPOINT smart_mrgc_refresh')
        try:
            conn.execute('DELETE FROM stock_pool_members WHERE pool_id=?', (POOL_ID,))
            conn.executemany('''INSERT INTO stock_pool_members
                (pool_id,symbol,note,sort_order,added_at,updated_at) VALUES (?,?,?,0,?,?)''',
                [(POOL_ID, symbol, f'最近命中 {latest} · 20日命中 {count} 次', now, now)
                 for symbol, latest, count in candidates])
            conn.execute('RELEASE smart_mrgc_refresh')
        except Exception:
            conn.execute('ROLLBACK TO smart_mrgc_refresh')
            conn.execute('RELEASE smart_mrgc_refresh')
            raise
        initialized = initialized or max(target, enabled_date)
    count = conn.execute('SELECT COUNT(*) FROM stock_pool_members WHERE pool_id=?',
                         (POOL_ID,)).fetchone()[0]
    result = dict(pool_id=POOL_ID, status='BLOCKED' if reason else 'READY', target_trade_date=target,
                  window_start=min(days) if days else None, window_end=max(days) if days else None,
                  initialized_date=initialized, member_count=count, reason=reason,
                  missing_dates=missing, window_days=len(days), capacity=300,
                  calendar_source=calendar_source, updated_at=now)
    conn.execute('''INSERT INTO smart_watchlist_state VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(pool_id) DO UPDATE SET status=excluded.status,target_trade_date=excluded.target_trade_date,
        window_start=excluded.window_start,window_end=excluded.window_end,
        initialized_date=excluded.initialized_date,member_count=excluded.member_count,
        reason=excluded.reason,updated_at=excluded.updated_at,dates_json=excluded.dates_json''',
        (POOL_ID, result['status'], target, result['window_start'], result['window_end'],
         initialized, count, reason, now, json.dumps(days, ensure_ascii=False)))
    return result
