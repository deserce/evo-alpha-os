"""Bounded read-only MRGC report; facts are shared by UI and email preview."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date
import html
import json
import math
from pathlib import Path
import re
import sqlite3

from quant_engine.research.structure_turning import VERSION
from quant_engine.watch.smart_mrgc_pool import POOL_ID, resolve_members
from quant_engine.watch.structure_monitor import StructureMonitor

LIMIT = 300
REPORT_VERSION = 'three_groups_c_chan_v2'
GROUP_DEFS = [('mrgc', 'MRGC · 近20日', POOL_ID), ('company', '公司', 'pool_0d1aea597dc4'), ('watchlist', '自选股', 'watchlist')]
EVENT_TYPES = ('BUY_CONFIRM', 'EXIT', 'INVALIDATED', 'RISK_WARNING')
LABELS = dict(BUY_CONFIRM='今日新增买入确认', EXIT='今日模拟卖出',
              INVALIDATED='今日结构失效', RISK_WARNING='今日风险预警')


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _json(value):
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _opportunities(events: list, chan: dict, target: str) -> list:
    result = []
    signals = chan.get('recent', []) if chan.get('status') == 'READY' else []
    directions = {s.get('direction') for s in signals}
    directions.update('buy' if e['event_type'] == 'BUY_CONFIRM' else 'sell'
                      for e in events if e['event_type'] in ('BUY_CONFIRM', 'EXIT'))
    conflict = {'buy', 'sell'} <= directions
    for direction, event_type in [('buy', 'BUY_CONFIRM'), ('sell', 'EXIT')]:
        c_events = [e for e in events if e['event_type'] == event_type]
        same = [signal for signal in signals if signal.get('direction') == direction]
        today = [signal for signal in same if signal.get('confirmation_date') == target]
        base = dict(direction=direction, signal=direction.upper(), confirmed_date=target,
                    conflict=conflict)
        if c_events and same and not conflict:
            result.append(dict(base, label='C确认＋缠论候选共振', level='resonance', source='C_CHAN',
                evidence=[e['reason'] for e in c_events] +
                    [str(signal.get('label', '')) + '（首次可见计算日 ' + str(signal.get('confirmation_date')) +
                     '，拐点日 ' + str(signal.get('pivot_date')) + '；ZIG会回绘）' for signal in same]))
        elif c_events:
            result.append(dict(base, label='C 类单策略' + ('买点' if direction == 'buy' else '卖点') + ('（方向分歧）' if conflict else ''),
                level='c_only', source='C', evidence=[e['reason'] for e in c_events]))
        if today and (not c_events or conflict):
            result.append(dict(base, label='缠论' + ('买' if direction == 'buy' else '卖') + '点候选（ZIG回绘）',
                level='chan_only', source='CHAN', evidence=[str(signal.get('label', '')) +
                    '，拐点日 ' + str(signal.get('pivot_date')) + '，首次可见计算日 ' +
                    str(signal.get('confirmation_date')) for signal in today]))
    return result


def _opportunity_counts(stocks: list) -> dict:
    counts = dict(buy=0, sell=0, resonance_buy=0, resonance_sell=0, chan_buy=0,
                  chan_sell=0, c_only_buy=0, c_only_sell=0)
    for stock in stocks:
        if stock['data_status'] != 'OK':
            continue
        for direction in ('BUY', 'SELL'):
            subset = [o for o in stock['opportunities'] if o['signal'] == direction]
            key = direction.lower()
            counts[key] += bool(subset)
            counts['resonance_' + key] += any(o['source'] == 'C_CHAN' for o in subset)
            counts['chan_' + key] += any(o['source'] == 'CHAN' for o in subset)
            counts['c_only_' + key] += any(o['source'] == 'C' for o in subset)
    return counts


def _aggregate(stocks: list) -> tuple[dict, list]:
    result = dict(stocks=stocks, sectors=[], counts=dict(total=0, buy_confirm=0, exit=0,
        invalidated=0, risk_warning=0, risk_total=0, holding=0, existing_holding=0, blocked=0))
    counts = result['counts']
    counts['total'] = len(result['stocks'])
    for stock in result['stocks']:
        counts['holding'] += stock['status'] == 'HOLDING'
        counts['existing_holding'] += stock['status'] == 'HOLDING' and not any(e['event_type'] == 'BUY_CONFIRM' for e in stock['events'])
        counts['blocked'] += stock['data_status'] != 'OK'
        counts['risk_total'] += any(e['event_type'] in ('INVALIDATED', 'RISK_WARNING') for e in stock['events'])
        for event_type in EVENT_TYPES:
            counts[event_type.lower()] += stock['data_status'] == 'OK' and any(e['event_type'] == event_type for e in stock['events'])
    groups = defaultdict(list)
    for stock in result['stocks']:
        groups[stock['sector_name']].append(stock)
    for name, stocks in groups.items():
        sector = dict(name=name, count=len(stocks))
        for key, output in [('pct_chg', 'avg_pct_chg'), ('rps20', 'avg_rps20'), ('structure_score', 'avg_structure_score')]:
            values = [s[key] for s in stocks if s[key] is not None and s['data_status'] == 'OK']
            sector[output] = sum(values) / len(values) if values else None
        sector.update(buy_confirm=sum(s['data_status']=='OK' and any(e['event_type'] == 'BUY_CONFIRM' for e in s['events']) for s in stocks),
                      exit=sum(s['data_status']=='OK' and any(e['event_type'] == 'EXIT' for e in s['events']) for s in stocks))
        result['sectors'].append(sector)
    result['sectors'].sort(key=lambda s: (-s['count'], s['name']))
    return result['counts'], result['sectors']


def build_report(db_path: Path | str, trade_date: str, *,
                 frozen_groups: list[dict] | None = None,
                 history_results: tuple[dict, dict] | None = None,
                 compute_chan: bool = True, freeze_mrgc: bool = False) -> dict:
    """Read three explicit memberships and exact-day C/Chan facts, never write."""
    if date.fromisoformat(trade_date).isoformat() != trade_date:
        raise ValueError('trade_date 必须是 YYYY-MM-DD')
    frozen_by_id = {g['id']: g for g in (frozen_groups or []) if g.get('id') in ('mrgc', 'company', 'watchlist')}
    monitor = StructureMonitor(db_path)
    result = dict(date=trade_date, scope='three_groups', status='BLOCKED', stocks=[], sectors=[],
                  groups=[], counts={}, opportunity_counts={}, summary=[], missing=[], warnings=[], partial_usable=False,
                  source=dict(version=VERSION, config_hash=monitor.config_hash, mode='rules',
                    report_version=REPORT_VERSION, chan_formula='zhihu_zig10_zig20', chan_repainting=True, window_days=0, window_start=None, window_end=None,
                    rules=['C 当日买入确认/退出与近3交易日缠论同向确认优先标为共振',
                           '单日仅一个策略触发时展示单策略候选；历史持有不是今日买点',
                           '公司、自选股采用当前成员，历史日期不代表历史成员；C 风险/失效独立保留']))
    if frozen_groups is not None:
        result['source']['rules'][-1] = '公司、自选股采用生成时成员快照；C 风险/失效独立保留'
    for gid, name, pid in GROUP_DEFS:
        result['groups'].append(dict(id=gid, name=name, pool_id=pid, membership_as_of='historical_20d' if gid == 'mrgc' else 'current',
            symbols=[], count=0, counts=_aggregate([])[0], opportunity_counts=_opportunity_counts([]), sectors=[], status='BLOCKED', missing=[], warnings=[]))
    path = Path(db_path)
    if not path.exists():
        result['missing'] = ['本地行情数据库尚未准备好']
        result['counts'], result['sectors'] = _aggregate([])
        result['opportunity_counts'] = _opportunity_counts([])
        return result
    pending_histories = []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'stock_daily_prices' not in tables:
            result['missing'] = ['基础数据表缺失：stock_daily_prices']
            result['counts'], result['sectors'] = _aggregate([])
            result['opportunity_counts'] = _opportunity_counts([])
            return result
        resolved = resolve_members(conn, trade_date)
        days = resolved['dates']
        result['source'].update(window_days=len(days), window_start=resolved['window_start'],
                               window_end=resolved['window_end'], calendar=resolved['calendar_source'])
        group = result['groups'][0]
        reason = resolved['reason']
        if reason == 'source_window_incomplete':
            group['missing'].append(trade_date + '：不足完整20个交易日，或所选日期不是已记录交易日')
        elif reason == 'source_tables_missing':
            group['missing'].append('MRGC 筛选数据表缺失')
        elif reason in ('mrgc_dates_missing', 'source_dates_missing'):
            suffix = '：MRGC 筛选尚未成功完成' if reason == 'mrgc_dates_missing' else '：源行情缺失'
            group['missing'].extend(d + suffix for d in resolved['missing_dates'])
        elif reason:
            group['missing'].append('MRGC 历史票池校验失败：' + reason)
        members_by_symbol = {}
        mrgc_members = resolved['members']
        if freeze_mrgc and 'mrgc' in frozen_by_id:
            symbols = frozen_by_id['mrgc'].get('symbols', [])
            if (not isinstance(symbols, list) or len(symbols) > LIMIT
                    or any(not isinstance(symbol, str) or not re.fullmatch(r'[0-9]{6}', symbol)
                           for symbol in symbols)):
                group['missing'].append('存档 MRGC 成员格式或容量无效')
                mrgc_members = []
            else:
                original = {member['symbol']: member for member in mrgc_members}
                mrgc_members = [original.get(symbol, dict(symbol=symbol, last_hit_date=None,
                                hit_count=0)) for symbol in sorted(set(symbols))]
                group['membership_as_of'] = 'generation_snapshot'
        for member in mrgc_members:
            symbol = member['symbol']
            members_by_symbol[symbol] = dict(member, group_ids=['mrgc'], group_names=[group['name']])
            group['symbols'].append(symbol)
        for group in result['groups'][1:]:
            frozen = frozen_by_id.get(group['id'])
            if frozen is not None:
                group['pool_id'] = frozen.get('pool_id', group['pool_id'])
                group['membership_as_of'] = 'generation_snapshot'
                symbols = frozen.get('symbols', [])
                if (not isinstance(symbols, list) or len(symbols) > LIMIT
                        or any(not isinstance(symbol, str) or not re.fullmatch(r'[0-9]{6}', symbol)
                               for symbol in symbols)):
                    group['missing'].append('存档分组成员格式或容量无效')
                    continue
                for symbol in sorted(set(symbols)):
                    group['symbols'].append(symbol)
                    member = members_by_symbol.setdefault(symbol, dict(symbol=symbol,
                        last_hit_date=None, hit_count=0, group_ids=[], group_names=[]))
                    member['group_ids'].append(group['id'])
                    member['group_names'].append(group['name'])
                continue
            pid = group['pool_id']
            if not {'stock_pools', 'stock_pool_members'} <= tables:
                group['missing'].append(group['name'] + '分组记录不存在')
                continue
            pool = conn.execute('SELECT pool_id FROM stock_pools WHERE pool_id=?', (pid,)).fetchone()
            if not pool and group['id'] == 'company':
                matches = conn.execute('SELECT pool_id FROM stock_pools WHERE name=? LIMIT 2', ('公司',)).fetchall()
                pool = matches[0] if len(matches) == 1 else None
            if not pool:
                group['missing'].append(group['name'] + '分组不存在或名称不唯一')
                continue
            group['pool_id'] = pool[0]
            for row in conn.execute('SELECT DISTINCT symbol FROM stock_pool_members WHERE pool_id=? ORDER BY symbol LIMIT ?', (pool[0], LIMIT+1)):
                symbol = row[0]
                group['symbols'].append(symbol)
                member = members_by_symbol.setdefault(symbol, dict(symbol=symbol,last_hit_date=None,hit_count=0,group_ids=[],group_names=[]))
                member['group_ids'].append(group['id'])
                member['group_names'].append(group['name'])
        members = list(members_by_symbol.values())
        if len(members) > LIMIT:
            result['missing'].append('成员超过300只报告容量，暂停生成以防遗漏')
            result['counts'], result['sectors'] = _aggregate([])
            result['opportunity_counts'] = _opportunity_counts([])
            return result
        if any(not re.fullmatch(r'[0-9]{6}', str(m['symbol'])) for m in members):
            result['missing'].append('分组成员包含无效股票代码，请核对成员记录')
            result['counts'], result['sectors'] = _aggregate([])
            result['opportunity_counts'] = _opportunity_counts([])
            return result
        params = (trade_date, VERSION, monitor.config_hash)
        snapshots = {}
        event_map = defaultdict(list)
        if history_results is not None:
            snapshots, provided_events = history_results
            event_map.update({symbol: [event for event in events if event.get('event_type') in EVENT_TYPES]
                              for symbol, events in provided_events.items()})
        elif {'structure_monitor_runs', 'structure_monitor_snapshots', 'structure_monitor_events'} <= tables:
            run = conn.execute('SELECT status FROM structure_monitor_runs WHERE '
                               'trade_date=? AND version=? AND config_hash=?', params).fetchone()
            if not run or run[0] != 'COMPLETED':
                result['warnings'].append(trade_date + '：C 类总运行状态异常，逐股核对快照质量')
            snapshots = {r['symbol']: dict(r) for r in conn.execute(
                'SELECT * FROM structure_monitor_snapshots WHERE trade_date=? AND version=? '
                'AND config_hash=? LIMIT 601', params)}
            event_rows = conn.execute('SELECT * FROM structure_monitor_events WHERE trade_date=? '
                                      'AND version=? AND config_hash=? LIMIT 1501', params).fetchall()
            if len(event_rows) > 1500:
                result['missing'].append('C 类事件超过1500条报告容量，暂停完整机会判断')
            for row in event_rows[:1500]:
                if row['event_type'] in EVENT_TYPES:
                    payload = _json(row['payload'])
                    event_map[row['symbol']].append(dict(event_type=row['event_type'],
                        reason=str(payload.get('reason') or '')[:600], price=_number(payload.get('price'))))
        else:
            result['missing'].append('C 类监控记录尚未准备好')
        quality = monitor._quality_statuses(conn, trade_date)
        for member in members:
            symbol = member['symbol']
            lifecycle = None
            if 'stock_lifecycle_state' in tables:
                lifecycle = conn.execute('SELECT * FROM stock_lifecycle_state WHERE symbol=? '
                    'AND trade_date>=? AND trade_date<? LIMIT 1',
                    (symbol, trade_date, trade_date + 'T')).fetchone()
            facts = dict(lifecycle) if lifecycle else {}
            info = {}
            if 'stock_info' in tables:
                info_row = conn.execute('SELECT * FROM stock_info WHERE symbol=? LIMIT 1', (symbol,)).fetchone()
                info = dict(info_row) if info_row else {}
            snapshot = snapshots.get(symbol, {})
            state = _json(snapshot.get('state_json'))
            prices = conn.execute('SELECT * FROM stock_daily_prices WHERE symbol=? '
                'AND trade_date<? ORDER BY trade_date DESC LIMIT 400', (symbol, trade_date + 'T')).fetchall()
            price = _number(prices[0]['close']) if prices and 'close' in prices[0].keys() and str(prices[0]['trade_date'])[:10] == trade_date else None
            previous = _number(prices[1]['close']) if len(prices) > 1 and 'close' in prices[1].keys() else None
            pct = (price / previous - 1) * 100 if price is not None and previous and previous > 0 else None
            problems = []
            if price is None or pct is None:
                problems.append('当日收盘或上一交易日价格缺失')
            if not facts or facts.get('data_status') not in ('OK', 'PASS', 'WARNING'):
                result['warnings'].append(symbol + '：生命周期背景指标缺失，RPS / 结构分数暂不评价')
                facts = {}
            if not snapshot:
                problems.append('当日当前版本 C 类历史结果尚未计算')
            elif snapshot.get('data_status') != 'OK':
                problems.append('C 类质量校验未通过：' + str(snapshot.get('reason') or 'unknown')[:600])
            quality_reason = monitor._quality_reason(quality, symbol)
            if quality_reason:
                problems.append('日线质量校验缺失或未通过：' + monitor._market(symbol))
            result['missing'].extend(symbol + ' ' + trade_date + '：' + p for p in problems)
            events = event_map[symbol]
            opportunities = []
            chan = {}
            status = 'BLOCKED' if problems else state.get('status', 'WATCHING')
            messages = [LABELS[e['event_type']] + ('：' + e['reason'] if e['reason'] else '') for e in events]
            if not messages:
                messages = ['此前确认后的模拟持有，今日无新增买入确认' if status == 'HOLDING'
                            else '；'.join(str(v) for v in state.get('waiting_for', [])[:3]) or '等待 C 类结构确认']
            if not facts:
                messages.append('生命周期背景指标缺失，不能评价 RPS 与结构分数')
            if problems:
                messages = ['数据不完整，暂停机会判断']
            result['stocks'].append(dict(symbol=symbol, name=facts.get('name') or info.get('name') or snapshot.get('name') or symbol,
                sector_name=facts.get('sector_name') or info.get('industry') or '未分类', price=price, pct_chg=pct,
                rps20=_number(facts.get('rps_20')), structure_score=_number(facts.get('structure_score')),
                last_hit_date=member.get('last_hit_date'), hit_count=member.get('hit_count', 0), status=status,
                group_ids=member['group_ids'], group_names=member['group_names'], overlap_count=len(member['group_ids']),
                opportunities=opportunities, chan=chan, chan_signals=chan.get('recent', []),
                conflict=any(o['conflict'] for o in opportunities),
                data_status='BLOCKED' if problems else 'OK', usable=not problems, events=events,
                entry_price=_number(state.get('entry_price')), stop_price=_number(state.get('stop_price')),
                analysis='；'.join(messages)))
            history = []
            if compute_chan and price is not None and pct is not None and not quality_reason and prices and {'open', 'high', 'low', 'close'} <= set(prices[0].keys()):
                history = [(str(p['trade_date'])[:10], p['open'], p['high'], p['low'], p['close'])
                           for p in reversed(prices)]
            pending_histories.append((result['stocks'][-1], history))
    # Release the SQLite read transaction before bounded CPU replay, avoiding WAL retention.
    from quant_engine.watch.chan_daily_confirmation import confirm_daily
    for stock, history in pending_histories:
        if not compute_chan:
            history.clear()
            stock['chan'] = dict(status='NOT_COMPUTED', recent=[], today=[])
            stock['opportunities'] = _opportunities(stock['events'], {}, trade_date) if stock['data_status'] == 'OK' else []
            continue
        chan = dict(status='BLOCKED', recent=[], today=[], reason='insufficient_history')
        if history:
            from quant_engine.watch.chan_daily_snapshot import ChanDailySnapshotStore
            rows = [dict(zip(('trade_date', 'open', 'high', 'low', 'close'), bar))
                    for bar in history]
            recent_dates = sorted(days[:3])
            chan = ChanDailySnapshotStore(db_path).read(stock['symbol'], trade_date, rows,
                                                       recent_dates=recent_dates)
            if chan is None:
                chan = confirm_daily(rows, trade_date, recent_dates=recent_dates)
        stock['chan'] = chan
        stock['chan_signals'] = chan.get('recent', [])
        if chan.get('status') != 'READY':
            reason = chan.get('reason', '')
            message = '至少需要60根有效日线' if reason == 'insufficient_warmup' else '历史行情不足或未通过OHLC与日期校验'
            result['warnings'].append(stock['symbol'] + '：缠论候选暂不可评，' + message)
        if stock['data_status'] == 'OK':
            stock['opportunities'] = _opportunities(stock['events'], chan, trade_date)
            stock['conflict'] = any(o['conflict'] for o in stock['opportunities'])
            if stock['opportunities']:
                stock['analysis'] += '；' + '；'.join(o['label'] for o in stock['opportunities'])
        history.clear()
    pending_histories.clear()
    result['counts'], result['sectors'] = _aggregate(result['stocks'])
    result['opportunity_counts'] = _opportunity_counts(result['stocks'])
    for group in result['groups']:
        subset = [s for s in result['stocks'] if group['id'] in s['group_ids']]
        group['count'] = len(subset)
        group['counts'], group['sectors'] = _aggregate(subset)
        group['opportunity_counts'] = _opportunity_counts(subset)
        group['missing'].extend(message for message in result['missing']
            if not re.match(r'^\d{6} ', message) or message[:6] in group['symbols'])
        group['warnings'] = [message for message in result['warnings'] if message[:6] in group['symbols']]
        group['status'] = 'BLOCKED' if group['missing'] else 'READY'
    result['missing'] = list(dict.fromkeys(result['missing'] + [g['name'] + '：' + m for g in result['groups'] for m in g['missing']
        if not re.match(r'^\d{6} ', m) and m not in result['missing']]))
    result['status'] = 'BLOCKED' if result['missing'] else 'READY'
    result['partial_usable'] = any(stock['data_status'] == 'OK' for stock in result['stocks'])
    counts = result['counts']
    result['stocks'].sort(key=lambda s: (0 if any(o['source']=='C_CHAN' for o in s['opportunities']) else 1 if s['opportunities'] else 2, s['symbol']))
    result['summary'] = [f"三分组去重 {counts['total']} 只，覆盖 {len(result['sectors'])} 个行业。",
        f"今日 C 类新增买入确认 {counts['buy_confirm']} 只、模拟卖出 {counts['exit']} 只、结构失效 {counts['invalidated']} 只、风险预警 {counts['risk_warning']} 只。",
        f"既有模拟持有 {counts['existing_holding']} 只（不含当日新买点）。",
        '部分分组或股票数据缺失，仅可分析可信部分。' if result['missing'] else '共振与单策略分别展示；模拟卖出不代表实际账户下单。']
    return result


def render_email(report: dict) -> str:
    """Render the same facts and AI commentary in an unsent branded email."""
    from urllib.parse import urlencode
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    esc = lambda value: html.escape(str(value))
    ai = report.get('ai') or {}
    if ai.get('status') not in ('READY', None):
        ai = {}
    def number(value, suffix=''):
        return f'{value:.2f}{suffix}' if isinstance(value, (float, int)) else '缺失'
    def card(title, body):
        return ('<div style="margin:12px 0;padding:18px;border:1px solid #dce5ee;'
                'border-radius:12px;background:#f8fafc"><h3 style="margin:0 0 12px">'
                + esc(title) + '</h3>' + body + '</div>')
    content = '<div style="padding:8px 12px;background:#e0f2fe;border-radius:8px">'
    content += esc('数据可信' if report['status'] == 'READY' else '部分数据待校验，仅分析可信个股' if report.get('partial_usable') else '数据阻断：暂停机会判断') + '</div>'
    content += ''.join('<p>' + esc(line) + '</p>' for line in report['summary'])
    if ai.get('summary'):
        content += card('AI 今日分析', ''.join('<p>' + esc(line) + '</p>' for line in ai['summary']))
    sector_notes = ai.get('sector_notes') or {}
    stock_notes = ai.get('stock_notes') or {}
    for sector in report['sectors']:
        body = '<p>成员 ' + str(sector['count']) + ' 只 · 平均涨跌幅 ' + number(sector['avg_pct_chg'], '%') + '</p>'
        body += '<p>平均 RPS20 ' + number(sector['avg_rps20']) + ' · 平均结构分 ' + number(sector['avg_structure_score']) + '</p>'
        if sector['name'] in sector_notes:
            body += '<p>' + esc(sector_notes[sector['name']]) + '</p>'
        content += card('行业 · ' + sector['name'], body)
    def stock_card(stock):
        labels = [LABELS[e['event_type']] for e in stock['events']]
        badge = ' / '.join(labels) or ('此前模拟持有 · 今日无新买点' if stock['status'] == 'HOLDING' else stock['status'])
        body = '<div style="color:#0369a1;font-weight:600">' + esc(badge) + '</div>'
        body += '<table width="100%" style="margin:12px 0"><tr>'
        for label, value in [('收盘', number(stock['price'])), ('涨跌幅', number(stock['pct_chg'], '%')),
                             ('RPS20', number(stock['rps20'])), ('失效线', number(stock['stop_price']))]:
            body += '<td width="25%" style="padding:8px;background:#eaf0f6">' + esc(label) + '<br><strong>' + esc(value) + '</strong></td>'
        body += '</tr></table><p><strong>规则依据：</strong>' + esc(stock['analysis']) + '</p>'
        if stock['symbol'] in stock_notes:
            body += '<p><strong>AI 分析：</strong>' + esc(stock_notes[stock['symbol']]) + '</p>'
        query = urlencode(dict(trade_date=report['date']))
        body += '<a style="color:#0284c7" href="http://127.0.0.1:3000/a/stock/' + esc(stock['symbol']) + '?' + esc(query) + '">查看个股与信号 →</a>'
        body += '<p>所属分组：' + esc(' / '.join(stock.get('group_names', []))) + '</p>'
        for opportunity in stock.get('opportunities', []):
            body += '<p><strong>' + esc(opportunity['label']) + '</strong> · ' + esc(opportunity['confirmed_date']) + '<br>' + esc('；'.join(opportunity['evidence'])) + '</p>'
        return card(stock['name'] + ' ' + stock['symbol'], body)
    stock_map = {stock['symbol']: stock for stock in report['stocks']}
    for group in report.get('groups', []):
        scope_note = '当前成员（历史日期不代表历史成员）' if group['membership_as_of'] == 'current' else '生成时成员快照' if group['membership_as_of'] == 'generation_snapshot' else '指定日期20交易日历史重建'
        counts = group['opportunity_counts']
        content += card(group['name'] + ' · ' + group['status'], '<p>' + esc(scope_note) +
            f" · {group['count']} 只</p><p>买点候选 {counts.get('buy', 0)} · 卖点候选 {counts.get('sell', 0)} · 共振买点 {counts.get('resonance_buy', 0)} · 共振卖点 {counts.get('resonance_sell', 0)}</p>")
        for symbol in group['symbols'][:LIMIT]:
            if symbol in stock_map:
                content += stock_card(stock_map[symbol])
    if not report.get('groups'):
        content += ''.join(stock_card(stock) for stock in report['stocks'][:LIMIT])
    if report['missing']:
        content += card('缺失数据', '<p>' + esc('；'.join(report['missing'])) + '</p>')
    if report.get('warnings'):
        content += card('背景指标提示', '<p>' + esc('；'.join(report['warnings'])) + '</p>')
    return WatchNotificationDispatcher._document(eyebrow='MRGC · 公司 · 自选股', title='三分组买卖点分析日报',
        subtitle=report['date'] + ' · 邮件预览，未发送', content=content)
