"""Bounded, read-only date/dataset planning shared by execution and health."""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import sqlite3

VERSION = 'recent_repair_v1'
TABLES = {
    'valuation': ('stock_valuation_daily', 'code'),
    'index': ('index_daily', 'index_code'),
    'etf': ('etf_daily_prices', 'symbol'),
    'sector': ('sector_composite_daily', 'sector_name'),
    'stock_rps': ('quant_feature_rps', 'symbol'),
    'sector_rps': ('quant_feature_sector_rps_v2', 'sector_name'),
    'etf_rps': ('quant_feature_etf_rps', 'symbol'),
    'structure': ('structure_health_daily', 'symbol'),
    'lifecycle': ('stock_lifecycle_state', 'symbol'),
    'watch': ('watch_daily_snapshot', 'symbol'),
    'c': ('structure_monitor_snapshots', 'symbol'),
}
CATEGORIES = {
    'index': 'support', 'etf': 'support', 'etf_rps': 'support',
    'watch': 'monitoring', 'c': 'monitoring', 'report': 'report',
}
ROLLING_DEPENDENTS = {
    'index': {'strength', 'lifecycle', 'mrgc', 'timing', 'risk', 'watch', 'c', 'report'},
    'etf': {'etf_rps'},
    'sector': {'sector_rps', 'strength', 'lifecycle', 'mrgc', 'timing', 'risk',
               'watch', 'c', 'report'},
}


def _exists(conn, table):
    return conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone() is not None


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _end(day):
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _keys(conn, table, day, entity='symbol'):
    if not _exists(conn, table):
        return set()
    columns = _columns(conn, table)
    if entity not in columns:
            entity = next((key for key in ('symbol', 'sector_name', 'sector_code', 'index_code', 'market')
                       if key in columns), None)
    if not entity or 'trade_date' not in columns:
        return set()
    return {str(row[0]) for row in conn.execute(
        f'SELECT DISTINCT {entity} FROM {table} WHERE trade_date>=? AND trade_date<?',
        (day, (date.fromisoformat(day) + timedelta(days=1)).isoformat())) if row[0] is not None}


def _stock_digest(conn, day):
    digest = hashlib.sha256()
    for table in ('stock_daily_prices', 'stock_daily_prices_raw'):
        if not _exists(conn, table):
            continue
        fields = [key for key in ('symbol', 'open', 'high', 'low', 'close', 'volume', 'amount',
                                  'pct_chg', 'turnover_rate') if key in _columns(conn, table)]
        digest.update(table.encode())
        cursor = conn.execute(
            f'SELECT {",".join(fields)} FROM {table} WHERE trade_date>=? '
            'AND trade_date<? ORDER BY symbol', (day, _end(day)))
        for row in cursor:
            digest.update(json.dumps(tuple(row), default=str).encode() + b'\n')
    return digest.hexdigest()


def _source_digest(conn, table, day):
    if not _exists(conn, table):
        return _hash([table, 'missing'])
    columns = _columns(conn, table)
    fields = [key for key in ('symbol','index_code','sector_name','open','high','low',
                              'close','volume','amount') if key in columns]
    digest = hashlib.sha256()
    for row in conn.execute(f'SELECT {",".join(fields)} FROM {table} '
                            f'WHERE trade_date>=? AND trade_date<? ORDER BY {fields[0]}',
                            (day,_end(day))):
        digest.update(json.dumps(tuple(row),default=str).encode()+b'\n')
    return digest.hexdigest()


def _sector_universe(conn):
    if _exists(conn, 'stock_sector_map'):
        from data_core.sector_membership import _sector_rows
        return {row[1] for row in _sector_rows(conn)}
    if _exists(conn, 'sector_list') and 'sector_name' in _columns(conn,'sector_list'):
        return {str(row[0]) for row in conn.execute('SELECT sector_name FROM sector_list')}
    return set()


def _quality(conn, day):
    if not _exists(conn, 'data_quality_report'):
        return False
    has_counts = 'actual_count' in _columns(conn, 'data_quality_report')
    count_column = ',actual_count' if has_counts else ''
    rows = conn.execute(
        f"SELECT market,status{count_column} FROM data_quality_report WHERE trade_date=? "
        "AND module='stock_daily' ORDER BY created_at,rowid", (day,))
    latest = {('BSE' if str(row[0]).upper() == 'BJ' else str(row[0]).upper()): row
              for row in rows}
    if has_counts:
        from data_core.quality import actual_stock_count
        for market in ('SH', 'SZ', 'BSE'):
            row = latest.get(market)
            if row is None or row[2] != actual_stock_count(conn, day, market):
                return False
    statuses = {market: row[1] for market, row in latest.items()}
    return all(statuses.get(market) in ('PASS', 'OK', 'WARNING')
               for market in ('SH', 'SZ', 'BSE')) and statuses.get('ALL', 'PASS') != 'FAIL'


def _members(conn, *, monitored=False, core=False):
    result = set()
    if core and _exists(conn, 'quant_stock_pool'):
        columns = _columns(conn, 'quant_stock_pool')
        clauses = ["symbol IS NOT NULL"]
        if 'is_active' in columns:
            clauses.append('COALESCE(is_active,1)=1')
        if 'pool_name' in columns:
            clauses.append("pool_name='core_pool'")
        result.update(str(row[0]) for row in conn.execute(
            'SELECT DISTINCT symbol FROM quant_stock_pool WHERE ' + ' AND '.join(clauses)))
    if _exists(conn, 'stock_pool_members'):
        query = 'SELECT DISTINCT m.symbol FROM stock_pool_members m'
        if monitored:
            if not _exists(conn, 'stock_pools'):
                return result
            query += ' JOIN stock_pools p ON p.pool_id=m.pool_id'
            columns = _columns(conn, 'stock_pools')
            column = 'monitor_enabled' if 'monitor_enabled' in columns else 'is_default'
            if column not in columns:
                return result
            query += f' WHERE COALESCE(p.{column},0)=1'
        result.update(str(row[0]) for row in conn.execute(query) if row[0])
    return result


def _c_members(conn, day):
    if _exists(conn, 'stock_pools') and _exists(conn, 'stock_pool_members'):
        from quant_engine.watch.structure_monitor import StructureMonitor
        path = conn.execute('PRAGMA database_list').fetchone()[2] or ':memory:'
        scope = StructureMonitor(path).resolve_scope(conn, day, historical=True)
        return set(scope['members']), scope['reason']
    members = set()
    if _exists(conn, 'stock_pool_members'):
        members = {str(row[0]) for row in conn.execute(
            "SELECT DISTINCT symbol FROM stock_pool_members WHERE pool_id<>'smart_mrgc_20d'")}
    if _exists(conn, 'strategy_run_log') and _exists(conn, 'quant_preselect_results'):
        from quant_engine.watch.smart_mrgc_pool import resolve_members
        resolved = resolve_members(conn, day)
        if resolved['status'] != 'READY':
            return members, resolved['reason']
        members.update(row['symbol'] for row in resolved['members'])
    return members, None


def _strength_universes(conn, day):
    """Match the strength calculator's eligible inputs, including index ffill."""
    from scripts.calculate_strength_features import CORE_INDEX_CODES

    market = set()
    lower = (date.fromisoformat(day) - timedelta(days=420)).isoformat()
    if _exists(conn, 'index_daily') and _exists(conn, 'stock_daily_prices'):
        placeholders = ','.join('?' for _ in CORE_INDEX_CODES)
        market = {str(row[0]) for row in conn.execute(
            f"SELECT DISTINCT i.index_code FROM index_daily i "
            f"WHERE i.index_code IN ({placeholders}) AND i.trade_date>=? AND i.trade_date<? "
            "AND EXISTS (SELECT 1 FROM stock_daily_prices s "
            "WHERE s.trade_date>=substr(i.trade_date,1,10) "
            "AND s.trade_date<date(substr(i.trade_date,1,10),'+1 day') LIMIT 1)",
            (*CORE_INDEX_CODES, lower, _end(day)))}
    universe = set()
    if _exists(conn, 'sector_list'):
        predicate = " WHERE sector_type='Industry'" if 'sector_type' in _columns(conn, 'sector_list') else ''
        universe = {str(row[0]).strip() for row in conn.execute(
            'SELECT DISTINCT sector_name FROM sector_list' + predicate) if row[0]}
    if not universe and _exists(conn, 'stock_sector_map'):
        universe = {str(row[0]).strip() for row in conn.execute(
            'SELECT DISTINCT sector_name FROM stock_sector_map') if row[0]}
    industries = set()
    if _exists(conn, 'sector_composite_daily'):
        quality = (" AND UPPER(COALESCE(quality_flag,'PASS')) IN ('PASS','OK','LEGACY_OK')"
                   if 'quality_flag' in _columns(conn, 'sector_composite_daily') else '')
        industries = {str(row[0]).strip() for row in conn.execute(
            'SELECT DISTINCT sector_name FROM sector_composite_daily '
            'WHERE trade_date>=? AND trade_date<?' + quality,
            (day, _end(day))) if row[0]}
    return market, industries & universe


def _task_audit(conn, name, day):
    if not _exists(conn, 'pipeline_task_log'):
        return False
    return bool(conn.execute(
        "SELECT 1 FROM pipeline_task_log WHERE status='success' "
        "AND task_name IN (?,?) AND instr(','||replace(target_dates,' ','')||',',?)>0 "
        "LIMIT 1", (name, name+'@'+day, ','+day+',')).fetchone())


def _mrgc_audit(conn, day):
    return _exists(conn, 'strategy_run_log') and bool(conn.execute(
        "SELECT 1 FROM strategy_run_log WHERE strategy_name='mrgc_v1' "
        "AND trade_date>=? AND trade_date<? AND status='success' LIMIT 1",
        (day, _end(day))).fetchone())


def read_frozen_members(conn, start, end):
    """Read serialized frozen universes without creating or changing any schema."""
    if (not _exists(conn, 'data_repair_state')
            or 'members_json' not in _columns(conn, 'data_repair_state')):
        return {}
    return {(row[0], row[1]): row[2] for row in conn.execute(
        'SELECT trade_date,dataset,members_json FROM data_repair_state '
        'WHERE trade_date>=? AND trade_date<? AND members_json IS NOT NULL',
        (start, _end(end)))}


def _decode_frozen_members(saved):
    if not isinstance(saved, str):
        raise ValueError('invalid_frozen_members')
    if len(saved) > 100000:
        raise ValueError('frozen_member_capacity_exceeded')
    try:
        members = json.loads(saved)
    except (ValueError, RecursionError) as exc:
        raise ValueError('invalid_frozen_members') from exc
    if (not isinstance(members, list) or len(members) > 10000
            or any(not isinstance(value, str) or len(value) != 6
                   or not value.isascii() or not value.isdigit() for value in members)):
        raise ValueError('invalid_frozen_members')
    return set(members)


def _date_members(conn, day, name, current, frozen, *, audited):
    """Use an audited date's universe; never infer deleted rows after freezing."""
    saved = frozen.get((day, name))
    if saved is not None:
        members = _decode_frozen_members(saved)
        if day < date.today().isoformat():
            return members
    if day < date.today().isoformat() and audited:
        table = 'stock_lifecycle_state' if name == 'lifecycle' else 'watch_daily_snapshot'
        actual = _keys(conn, table, day)
        if actual:
            return actual
    return current


def build_repair_plan(conn: sqlite3.Connection, target: str, *, limit: int = 7,
                      completed: set[tuple[str, str]] | None = None) -> dict:
    """Inspect at most seven calendar dates; no schema creation or strategy replay."""
    if date.fromisoformat(target).isoformat() != target or not 1 <= limit <= 7:
        raise ValueError('repair target must be ISO date and window must be 1..7')
    conn.row_factory = sqlite3.Row
    if not _exists(conn, 'trading_calendar'):
        raise ValueError('trading_calendar_missing')
    from data_core.market_calendar import is_a_share_session

    target_day = date.fromisoformat(target)
    calendar = conn.execute(
        'SELECT DISTINCT is_open FROM trading_calendar WHERE trade_date>=? AND trade_date<?',
        (target, _end(target))).fetchall()
    # History-backed calendars lag a newly completed session. Only the published
    # 2026 offline calendar may fill absence; explicit records remain authoritative.
    inferred_open = target_day.year == 2026 and is_a_share_session(target_day)
    if (calendar and (len(calendar) != 1 or calendar[0][0] != 1)) or (
            not calendar and not inferred_open):
        raise ValueError('target_calendar_not_open')
    candidates = {str(row[0])[:10] for row in conn.execute(
        'SELECT DISTINCT trade_date FROM trading_calendar WHERE is_open=1 '
        'AND trade_date<? ORDER BY trade_date DESC LIMIT ?', (_end(target), limit))}
    if target_day.year == 2026:
        lower = target_day - timedelta(days=60)
        explicit = {}
        for row in conn.execute(
                'SELECT trade_date,is_open FROM trading_calendar '
                'WHERE trade_date>=? AND trade_date<?', (lower.isoformat(), _end(target))):
            explicit.setdefault(str(row[0])[:10], set()).add(row[1])
        for offset in range(60):
            day = target_day - timedelta(days=offset)
            if day.year != 2026:
                break
            text = day.isoformat()
            states = explicit.get(text)
            if states and (len(states) != 1 or not states <= {0, 1}):
                raise ValueError('calendar_conflicting_records')
            if states == {0}:
                candidates.discard(text)
            elif states == {1} or (states is None and is_a_share_session(day)):
                candidates.add(text)
            if len([value for value in candidates if value >= text]) >= limit:
                break
    dates = sorted(candidates)[-limit:]
    states = {}
    frozen = read_frozen_members(conn, dates[0], target)
    debt = set()
    completed = completed or set()
    if _exists(conn, 'data_repair_state'):
        states = {(row[0], row[1]): row[2] for row in conn.execute(
            'SELECT trade_date,dataset,input_hash FROM data_repair_state '
            'WHERE trade_date>=? AND trade_date<?', (dates[0], _end(target))) }
    if _exists(conn,'data_repair_pending'):
        debt = {(row[0],row[1]) for row in conn.execute(
            'SELECT trade_date,dataset FROM data_repair_pending WHERE trade_date>=? '
            'AND trade_date<?',(dates[0],_end(target)))}
    monitored, lifecycle = _members(conn, monitored=True), _members(conn, core=True)
    sectors = _sector_universe(conn)
    output, source_dirty = [], False
    support_dirty = dict.fromkeys(ROLLING_DEPENDENTS, False)
    for day in dates:
        source = _keys(conn, 'stock_daily_prices', day)
        raw = _keys(conn, 'stock_daily_prices_raw', day)
        source_hash = _stock_digest(conn, day)
        own_source_changed = states.get((day, 'stock'), source_hash) != source_hash
        source_dirty = source_dirty or own_source_changed
        index_hash = _source_digest(conn,'index_daily',day)
        etf_hash = _source_digest(conn,'etf_daily_prices',day)
        sector_hash = _source_digest(conn,'sector_composite_daily',day)
        stock_expected = len(source)
        if _exists(conn, 'stock_info'):
            from data_core.quality import expected_stock_count
            stock_expected = sum(expected_stock_count(conn, market, day)
                                 for market in ('SH', 'SZ', 'BSE'))
        # Completeness tolerance belongs to the market quality gate. Listed
        # names without a bar remain diagnostic gaps, rather than a second 100% gate.
        stock_ready = bool(source) and raw >= source and _quality(conn, day)
        source_dirty = source_dirty or not stock_ready
        datasets = {}
        lifecycle_audited = _task_audit(conn, 'lifecycle_state', day) and _mrgc_audit(conn, day)
        watch_audited = _task_audit(conn, 'watch_engine', day)
        day_lifecycle = _date_members(conn, day, 'lifecycle', lifecycle, frozen,
                                      audited=lifecycle_audited)
        day_monitored = _date_members(conn, day, 'watch', monitored, frozen,
                                      audited=watch_audited)

        def add(name, actual, expected, ready, reason='', *, signature=None, degraded=0):
            inputs = [source_hash]
            if name in ('strength','lifecycle','mrgc','timing','risk','watch','c','report'):
                inputs.extend((index_hash,sector_hash))
            if name == 'sector_rps':
                inputs = [sector_hash,sorted(sectors)]
            elif name == 'sector':
                inputs.extend((sector_hash,sorted(sectors)))
            elif name == 'etf_rps':
                inputs = [etf_hash]
            if name == 'watch':
                inputs.append(sorted(day_monitored))
                if _exists(conn,'watch_rule_configs'):
                    inputs.append([tuple(r) for r in conn.execute(
                        "SELECT version,config_json FROM watch_rule_configs WHERE status='ACTIVE'")])
            elif name in ('lifecycle','mrgc'):
                inputs.append(sorted(day_lifecycle))
            fingerprint = signature or _hash([VERSION, name, day, inputs])
            own_changed = states.get((day, name), fingerprint) != fingerprint
            if (day,name) in debt and (day,name) not in completed:
                ready, reason = False, 'pending_repair'
            if (states.get((day, name), fingerprint) != fingerprint and name != 'stock'
                    and (day, name) not in completed):
                ready, reason = False, 'upstream_changed'
            if (source_dirty and name not in ('stock', 'index', 'etf', 'etf_rps')
                    and (day, name) not in completed):
                ready, reason = False, 'upstream_changed'
            if (any(dirty and name in ROLLING_DEPENDENTS[parent]
                    for parent, dirty in support_dirty.items())
                    and (day, name) not in completed):
                ready, reason = False, 'upstream_changed'
            if name == 'stock' and own_source_changed and (day, name) not in completed:
                ready, reason = False, 'source_changed_quality_recheck'
            if (name == 'watch' and actual and not states.get((day, name))
                    and (day, name) not in completed and _exists(conn,'watch_daily_snapshot')
                    and 'factors' in _columns(conn,'watch_daily_snapshot')):
                legacy = conn.execute("SELECT 1 FROM watch_daily_snapshot WHERE trade_date=? "
                    "AND json_extract(factors,'$.watch_rebuild.input_signature') IS NULL LIMIT 1",
                    (day,)).fetchone()
                if legacy:
                    ready, reason = False, 'legacy_snapshot_unverified'
            datasets[name] = dict(ready=bool(ready), actual=actual, expected=expected,
                                  missing=max(0, expected-actual), reason=reason,
                                  input_hash=fingerprint, degraded=degraded,
                                  category=CATEGORIES.get(name, 'core'))
            if name in support_dirty:
                # Propagate readiness only, never a window-prefix into date fingerprints.
                support_dirty[name] = support_dirty[name] or own_changed or (day, name) in debt or (
                    _exists(conn, TABLES[name][0]) and not ready
                )

        add('stock', len(source), stock_expected,
            stock_ready, 'coverage_or_quality_not_ready', signature=source_hash)
        for name, (table, entity) in TABLES.items():
            actual = _keys(conn, table, day, entity)
            expected = source
            reason = 'coverage_incomplete'
            degraded = 0
            legacy_valuation = False
            if name == 'index':
                expected = {'000001','399001','399006','399317','000300','000688','000905','000852'}
            elif name in ('etf', 'etf_rps'):
                expected = ({str(row[0]) for row in conn.execute('SELECT DISTINCT symbol FROM etf_info')}
                            if _exists(conn, 'etf_info') else set())
            elif name in ('sector', 'sector_rps'):
                expected = sectors
            elif name == 'lifecycle':
                expected = day_lifecycle or source
            elif name == 'watch':
                expected = day_monitored
            elif name == 'c':
                expected, reason = _c_members(conn, day)
                reason = reason or 'coverage_incomplete'
                if _exists(conn,table):
                    from quant_engine.watch.structure_monitor import StructureMonitor, VERSION as C_VERSION
                    path = conn.execute('PRAGMA database_list').fetchone()[2] or ':memory:'
                    config_hash = StructureMonitor(path).config_hash
                    actual = {str(r[0]) for r in conn.execute(
                        "SELECT symbol FROM structure_monitor_snapshots WHERE trade_date=? "
                        "AND version=? AND config_hash=? AND status<>'REMOVED'",
                        (day,C_VERSION,config_hash))}
            elif name == 'valuation':
                # Historic valuation cannot be recreated from today's shares/PE/PB.
                degraded = len(source-actual)
                if actual:
                    expected = actual
                columns = _columns(conn, table) if _exists(conn, table) else set()
                if 'source' in columns:
                    local = {str(row[0]) for row in conn.execute(
                        f"SELECT DISTINCT code FROM {table} WHERE trade_date>=? "
                        "AND trade_date<? AND source='local_kline'", (day,_end(day)))}
                    degraded += len(source & local)
                    pe = 'pe_ttm' if 'pe_ttm' in columns else 'pe' if 'pe' in columns else None
                    if pe and 'pb' in columns:
                        legacy_fields = [f'{pe}=0', 'pb=0']
                        if 'total_mv' in columns:
                            legacy_fields.append('total_mv IS NOT NULL')
                        legacy_valuation = any(str(row[0]) in source for row in conn.execute(
                            f"SELECT code FROM {table} WHERE trade_date>=? AND trade_date<? "
                            "AND source='local_kline' AND (" + ' OR '.join(legacy_fields) + ')',
                            (day,_end(day))))
            ready = bool(expected) and actual >= expected
            if name == 'c':
                ready = bool(expected) and actual == expected and reason == 'coverage_incomplete'
            if name == 'watch' and not expected:
                ready, reason = True, 'no_monitored_members'
            if ready and name in ('watch', 'c'):
                columns = _columns(conn, table)
                if 'data_status' in columns:
                    bad = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE trade_date=? "
                        "AND data_status NOT IN ('OK','REMOVED')", (day,)).fetchone()[0]
                    ready = not bad
                if name == 'c':
                    ready = ready and _exists(conn, 'structure_monitor_runs') and bool(conn.execute(
                        "SELECT 1 FROM structure_monitor_runs WHERE trade_date=? "
                        "AND status='COMPLETED' LIMIT 1", (day,)).fetchone())
                    if ready:
                        ready = not conn.execute(
                            "SELECT 1 FROM structure_monitor_snapshots WHERE trade_date=? "
                            "AND version=? AND config_hash=? AND status<>'REMOVED' "
                            "AND json_extract(state_json,'$._input_digest') IS NULL LIMIT 1",
                            (day,C_VERSION,config_hash)).fetchone()
            signature = (_hash([name, day, index_hash if name == 'index' else etf_hash,
                                sorted(expected)]) if name in ('index', 'etf') else None)
            add(name, len(actual & expected), len(expected), ready, reason,
                signature=signature, degraded=degraded)
            if legacy_valuation:
                datasets[name].update(ready=False,reason='legacy_valuation_placeholder')
        for name, tables in {
            'strength': ('market_strength_daily', 'industry_strength_daily'),
            'timing': ('timing_signals_daily',), 'risk': ('trade_plans',),
        }.items():
            sizes = [len(_keys(conn, table, day)) for table in tables]
            if name in ('timing', 'risk'):
                task = 'timing_signals' if name == 'timing' else 'risk_plans'
                audited = _task_audit(conn, task, day)
                add(name, sum(sizes), sum(sizes) if audited else 1,
                    audited, 'successful_audit_missing')
            else:
                expected = _strength_universes(conn, day)
                actual = [_keys(conn, table, day) for table in tables]
                audited = _task_audit(conn, 'strength_features', day)
                covered = all(wanted and found >= wanted
                              for found, wanted in zip(actual, expected))
                add(name, sum(len(found & wanted) for found, wanted in zip(actual, expected)),
                    sum(len(wanted) for wanted in expected), audited and covered,
                    'coverage_incomplete' if audited else 'successful_audit_missing')
        mrgc_ready = _mrgc_audit(conn, day)
        add('mrgc', int(mrgc_ready), 1, mrgc_ready, 'successful_audit_missing')
        report_ready = _exists(conn, 'three_group_daily_runs') and bool(conn.execute(
            "SELECT 1 FROM three_group_daily_runs WHERE trade_date=? AND status='READY' "
            "AND report_json IS NOT NULL LIMIT 1", (day,)).fetchone())
        add('report', int(report_ready), 1, report_ready, 'local_archive_missing_or_blocked')
        if lifecycle_audited or (day, 'lifecycle') in frozen:
            for name in ('lifecycle', 'mrgc'):
                datasets[name]['members_json'] = json.dumps(sorted(day_lifecycle))
        if watch_audited or (day, 'watch') in frozen:
            datasets['watch']['members_json'] = json.dumps(sorted(day_monitored))
        pending = [name for name, state in datasets.items() if not state['ready']]
        output.append(dict(trade_date=day, source_changed=source_dirty,
                           datasets=datasets, pending=pending))
    return dict(version=VERSION, target=target, dates=output,
                complete=all(not row['pending'] and not any(
                    state['degraded'] for state in row['datasets'].values()) for row in output))


def persist_ready_states(conn: sqlite3.Connection, plan: dict) -> None:
    """Only the locked mutating workflow may persist observed readiness versions."""
    conn.execute('''CREATE TABLE IF NOT EXISTS data_repair_state (
        trade_date TEXT NOT NULL,dataset TEXT NOT NULL,input_hash TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(trade_date,dataset))''')
    if 'members_json' not in _columns(conn, 'data_repair_state'):
        conn.execute('ALTER TABLE data_repair_state ADD COLUMN members_json TEXT')
    for row in plan['dates']:
        for name, state in row['datasets'].items():
            if state['ready'] or name == 'stock' and state['actual']:
                conn.execute('''INSERT INTO data_repair_state(trade_date,dataset,input_hash,members_json)
                    VALUES (?,?,?,?) ON CONFLICT(trade_date,dataset) DO UPDATE SET
                    input_hash=excluded.input_hash,members_json=excluded.members_json,
                    updated_at=CURRENT_TIMESTAMP
                    WHERE data_repair_state.input_hash<>excluded.input_hash
                    OR COALESCE(data_repair_state.members_json,'')<>COALESCE(excluded.members_json,'')''',
                    (row['trade_date'], name, state['input_hash'], state.get('members_json')))
                if state['ready'] and _exists(conn,'data_repair_pending'):
                    conn.execute('DELETE FROM data_repair_pending WHERE trade_date=? AND dataset=?',
                                 (row['trade_date'],name))


def persist_pending_states(conn: sqlite3.Connection, plan: dict,
                           additional: set[tuple[str,str]] | None = None) -> None:
    """Durably retain invalidated dependents before any source task starts."""
    conn.execute('''CREATE TABLE IF NOT EXISTS data_repair_pending (
        trade_date TEXT NOT NULL,dataset TEXT NOT NULL,reason TEXT NOT NULL,
        PRIMARY KEY(trade_date,dataset))''')
    needed = {(row['trade_date'],name) for row in plan['dates'] for name in row['pending']}
    for day,name in sorted(needed | (additional or set())):
        conn.execute('INSERT OR IGNORE INTO data_repair_pending VALUES (?,?,?)',
                     (day,name,'planned_repair'))
