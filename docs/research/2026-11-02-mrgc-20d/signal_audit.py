"""Read-only signal evidence audit. Writes only adjacent research artifacts."""
from __future__ import annotations
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import resource
import sqlite3
import time as clock

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DB = ROOT / 'backend/data/local_quant.db'
END = '2026-10-30'
TZ = ZoneInfo('Asia/Shanghai')
KINDS = {'BUY_CONFIRM': 'C_BUY', 'EXIT': 'C_EXIT',
         'INVALIDATED': 'C_RISK', 'RISK_WARNING': 'C_RISK'}


def timestamp(value):
    """Never infer a timezone from a naive timestamp."""
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def key(row):
    return (row['date'], row['symbol'], row['kind'], row.get('reason', ''),
            row.get('pivot_date', ''))


def extract_archive(envelope, filename):
    report = envelope['report']
    day = report['date']
    rows = []
    for stock in report.get('stocks', []):
        if stock.get('data_status') != 'OK' or stock.get('status') == 'BLOCKED':
            continue
        base = dict(date=day, symbol=stock['symbol'], source='dated_report_archive',
                    computed_at=envelope.get('generated_at'), archive=filename,
                    archive_id=envelope.get('archive_id'), evidence='archived_before_next_open')
        for event in stock.get('events', []):
            if event.get('event_type') in KINDS:
                rows.append(dict(base, kind=KINDS[event['event_type']],
                                 event_type=event['event_type'], reason=event.get('reason', '')))
        chan = stock.get('chan', {})
        if chan.get('status') == 'READY':
            for signal in chan.get('today', []):
                if signal.get('confirmation_date') == day and signal.get('direction') in ('buy', 'sell'):
                    rows.append(dict(base, kind='CHAN_' + signal['direction'].upper(),
                                     reason=signal.get('label', ''), pivot_date=signal.get('pivot_date')))
    return rows


def main():
    started = clock.monotonic()
    with closing(sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA cache_size=-4096')
        calendar = [r[0][:10] for r in conn.execute(
            'SELECT DISTINCT trade_date FROM trading_calendar WHERE is_open=1 ORDER BY trade_date')]
        days = [d for d in calendar if d <= END][-20:]
        start = days[0]
        first = calendar.index(start)
        historical_start = calendar[max(0, first-19)]
        hits = [dict(r) for r in conn.execute(
            "SELECT trade_date,symbol FROM quant_preselect_results WHERE strategy_name='mrgc_v1' "
            "AND result_type='PRESELECT' AND trade_date>=? AND trade_date<=? ORDER BY trade_date,symbol",
            (historical_start, END))]
        symbols = sorted({r['symbol'] for r in hits if r['trade_date'] >= start})
        names = dict(conn.execute('SELECT symbol,name FROM stock_info'))
        pools = {}
        for day in days:
            index = calendar.index(day)
            left = calendar[max(0,index-19)]
            pools[day] = {r['symbol'] for r in hits if left <= r['trade_date'] <= day}
        next_open = {}
        for day in days:
            later = [d for d in calendar if d > day]
            next_open[day] = datetime.combine(datetime.fromisoformat(later[0]).date(),
                                              time(9,30),TZ) if later else None
        latest = conn.execute('SELECT version,config_hash FROM structure_monitor_runs '
                              'WHERE trade_date<=? ORDER BY trade_date DESC,updated_at DESC LIMIT 1',
                              (END,)).fetchone()
        version, config = latest
        formula = conn.execute('SELECT formula_version FROM chan_daily_snapshot WHERE trade_date<=? '
                               'ORDER BY trade_date DESC,computed_at DESC LIMIT 1',(END,)).fetchone()[0]
        placeholders = ','.join('?' for _ in symbols)
        snapshots = {(r['trade_date'],r['symbol']):dict(r) for r in conn.execute(
            f'SELECT trade_date,symbol,data_status,status,updated_at FROM structure_monitor_snapshots '
            f'WHERE version=? AND config_hash=? AND trade_date>=? AND trade_date<=? '
            f'AND symbol IN ({placeholders})', [version,config,start,END,*symbols])}
        all_signals = []
        for raw in conn.execute(
            f'SELECT trade_date,symbol,event_type,payload,created_at FROM structure_monitor_events '
            f'WHERE version=? AND config_hash=? AND trade_date>=? AND trade_date<=? '
            f'AND symbol IN ({placeholders}) ORDER BY trade_date,symbol,event_type',
            [version,config,start,END,*symbols]):
            row = dict(raw)
            snapshot = snapshots.get((row['trade_date'],row['symbol']),{})
            if row['event_type'] not in KINDS or snapshot.get('data_status') != 'OK':
                continue
            payload = json.loads(row['payload'])
            all_signals.append(dict(date=row['trade_date'],symbol=row['symbol'],
                kind=KINDS[row['event_type']],event_type=row['event_type'],reason=payload.get('reason',''),
                source='structure_monitor_events_current', computed_at=snapshot.get('updated_at'),
                created_at=row['created_at'],evidence='current_recomputed',
                timestamp_timezone='unknown_naive',version=version,config_hash=config))
        chan_coverage = {}
        for raw in conn.execute(
            f'SELECT trade_date,symbol,status,payload,computed_at FROM chan_daily_snapshot '
            f'WHERE formula_version=? AND trade_date>=? AND trade_date<=? '
            f'AND symbol IN ({placeholders}) ORDER BY trade_date,symbol', [formula,start,END,*symbols]):
            row = dict(raw)
            chan_coverage[(row['trade_date'],row['symbol'])] = row['status']
            if row['status'] != 'READY':
                continue
            payload = json.loads(row['payload'])
            for signal in payload.get('today',[]):
                if signal.get('confirmation_date') != row['trade_date'] or signal.get('direction') not in ('buy','sell'):
                    continue
                all_signals.append(dict(date=row['trade_date'],symbol=row['symbol'],
                    kind='CHAN_'+signal['direction'].upper(),reason=signal.get('label',''),
                    pivot_date=signal.get('pivot_date'),source='chan_daily_snapshot_current',
                    computed_at=row['computed_at'],evidence='current_recomputed',formula_version=formula))
    # Enumerate old and current archives; only explicit timezone-aware generation stamps qualify.
    archive_candidates = defaultdict(list)
    archives_audit = []
    for path in sorted((DB.parent/'mrgc_report_archives').glob('*.json')):
        if path.stat().st_size > 2*1024*1024:
            continue
        envelope = json.loads(path.read_text(encoding='utf-8'))
        report = envelope.get('report',{})
        day = report.get('date')
        if day not in days:
            continue
        generated = timestamp(envelope.get('generated_at'))
        deadline = next_open[day]
        complete = (envelope.get('status') == 'READY' and report.get('status') == 'READY'
                    and not report.get('missing'))
        available = bool(complete and generated and deadline and generated < deadline
                         and generated >= datetime.combine(datetime.fromisoformat(day).date(),time(15),TZ))
        archives_audit.append(dict(file=path.name,date=day,generated_at=envelope.get('generated_at'),
            complete=complete,available_before_next_open=available,
            next_open=deadline.isoformat() if deadline else None))
        if available:
            archive_candidates[day].append((generated,path.name,envelope))
    archived_signals = []
    selected_archives = {}
    archive_members = {}
    for day, candidates in archive_candidates.items():
        _, filename, envelope = max(candidates,key=lambda item:item[0])
        selected_archives[day] = filename
        archive_members[day] = {s['symbol'] for s in envelope['report'].get('stocks',[])
                               if s.get('data_status') == 'OK' and s.get('status') != 'BLOCKED'}
        for row in extract_archive(envelope,filename):
            if row['symbol'] in symbols and row['symbol'] in pools[day]:
                archived_signals.append(row)
    archive_keys = {key(r):r for r in archived_signals}
    for row in all_signals:
        row['in_historical_mrgc_20d_pool'] = row['symbol'] in pools[row['date']]
        row['archive_match'] = key(row) in archive_keys
        if row['archive_match']:
            row['evidence'] = 'archived_before_next_open'
            row['archive'] = archive_keys[key(row)]['archive']
            row['archived_at'] = archive_keys[key(row)]['computed_at']
    all_signals.sort(key=lambda r:(r['date'],r['symbol'],r['kind'],r.get('pivot_date','')))
    signals = [r for r in all_signals if r['in_historical_mrgc_20d_pool']]
    coverage = []
    for day in days:
        expected = sorted(set(symbols)&pools[day])
        coverage.append(dict(date=day,pool_size=len(pools[day]),research_members=len(expected),
            c_ok=sum(snapshots.get((day,s),{}).get('data_status')=='OK' for s in expected),
            chan_ready=sum(chan_coverage.get((day,s))=='READY' for s in expected),
            c_missing_or_blocked=[s for s in expected if snapshots.get((day,s),{}).get('data_status')!='OK'],
            chan_missing_or_blocked=[s for s in expected if chan_coverage.get((day,s))!='READY'],
            archive=selected_archives.get(day),
            archive_covered=sum(s in archive_members.get(day,set()) for s in expected),
            current_counts=dict(Counter(r['kind'] for r in signals if r['date']==day)),
            archived_counts=dict(Counter(r['kind'] for r in archived_signals if r['date']==day))))
    by_stock = []
    for symbol in symbols:
        rows = [r for r in all_signals if r['symbol']==symbol]
        by_stock.append(dict(symbol=symbol,name=names.get(symbol,symbol),
            mrgc_hit_dates=sorted({r['trade_date'] for r in hits if r['symbol']==symbol and r['trade_date']>=start}),
            events=[{k:r.get(k) for k in ['date','kind','reason','pivot_date','evidence','in_historical_mrgc_20d_pool']} for r in rows]))
    current_keys = {key(r) for r in signals}
    result = dict(window=dict(start=start,end=END,dates=days,stocks=len(symbols)),
        version=version,config_hash=config,formula_version=formula,
        semantics='signals=current persisted events, filtered by date-specific trailing-20-session MRGC pool; archive_signals=latest complete archive generated before next recorded trading-day open; no model recomputation',
        signals=signals,all_stock_signals=all_signals,archive_signals=archived_signals,
        coverage=coverage,archives=archives_audit,by_stock=by_stock,
        summary=dict(current_signals=len(signals),current_counts=dict(Counter(r['kind'] for r in signals)),
            archived_signals=len(archived_signals),archived_counts=dict(Counter(r['kind'] for r in archived_signals)),
            current_with_archive_match=sum(r['archive_match'] for r in signals),
            archive_only_signals=sum(key(r) not in current_keys for r in archived_signals),
            dates_with_timely_archive=len(selected_archives),
            next_open_unknown_dates=[d for d in days if next_open[d] is None]),
        resources=dict(elapsed_seconds=round(clock.monotonic()-started,3),
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            db_mode='read-only',sqlite_cache_mib=4,processes=1,network_requests=0))
    (OUT/'signal_evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# MRGC 近20日信号证据审计','',f'研究区间：{start} 至 {END}，20 个交易日，{len(symbols)} 只当期入选股票。',
        '', '每天用该日及之前 19 个交易日的 MRGC 结果构建历史观察池，未来入选不反推此前可交易资格。',
        '', '## 时间与证据口径','',
        '- MRGC 是收盘后预选；C/缠论日线均需完整当日行情。可执行回测从下一交易日开盘起算，另处理 T+1、停牌、涨跌停和成本。',
        '- C 原有模拟收益按信号收盘价成交，不能直接作为本研究的执行收益；created_at 可能保留旧值而 payload 已更新，因此只表示数据库字段。',
        '- 缠论按 confirmation_date 统计；pivot_date 为回绘拐点。当天候选可能同时出现买卖方向。',
        '- 当前信号来自现存单一版本与参数。未重新计算任何正式信号，未写正式库。',
        '- 时点证据采用明确带时区的 generated_at，选择下一交易日 09:30 前最后一份完整存档；当前信号需日期、股票、类别、理由与拐点均匹配才提升证据等级。',
        '- 存档只能证明文件记录的生成时点已有该内容，不能证明用户已阅读或邮件送达。未重新验证源签名；存档用于历史内容证据，当前行情修订仍是限制。',
        '- 最后一天 10月30日的下一交易日不在本地日历，保守不赋予“次日开盘前”等级；其10月31日存档仍保留在JSON审核清单。',
        '- 证据匹配核对信号日期、方向与理由，不证明价格序列未经后续复权/修订；收益应标为使用当前行情复算。',
        '- 9月28、29日 C 仅覆盖当期历史池各7只（应各47只），缠论无快照；10月9日缺1只。不能将这些缺失视为没有买卖点。',
        '- 9月28、29日及10月21、22日的现存日报均晚于下一交易日开盘；10月9日无完整及时日报。10月23日日报于周日晚生成，仍早于下周一开盘。',
        '', '## 汇总','', '```json',json.dumps(result['summary'],ensure_ascii=False,indent=2),'```','',
        '## 每日覆盖','', '| 日期 | 当期135只中的历史池成员 | C可信 | 缠论可信 | 及时存档覆盖 | 当前买/卖候选 |',
        '|---|---:|---:|---:|---:|---|']
    for row in coverage:
        counts=row['current_counts']
        lines.append(f"| {row['date']} | {row['research_members']} | {row['c_ok']} | {row['chan_ready']} | {row['archive_covered']} | C买{counts.get('C_BUY',0)}/卖{counts.get('C_EXIT',0)}；缠买{counts.get('CHAN_BUY',0)}/卖{counts.get('CHAN_SELL',0)} |")
    lines += ['', '## 全部股票时序','', '“池外”表示当时尚无过去20日MRGC资格，仅供个股回顾，回测候选已排除。无事件不代表无监控；缺口见每日覆盖及JSON。', '', '| 股票 | MRGC入选日 | 当前保存买卖/风险点 |', '|---|---|---|']
    labels={'C_BUY':'C买','C_EXIT':'C卖','C_RISK':'C风险/失效','CHAN_BUY':'缠买','CHAN_SELL':'缠卖'}
    for stock in by_stock:
        desc=[]
        for row in stock['events']:
            suffix='' if row['in_historical_mrgc_20d_pool'] else '（池外）'
            evidence='✓' if row['evidence']=='archived_before_next_open' else ''
            desc.append(f"{row['date'][5:]} {labels[row['kind']]}{suffix}{evidence}")
        lines.append(f"| {stock['name']} {stock['symbol']} | {', '.join(d[5:] for d in stock['mrgc_hit_dates'])} | {'；'.join(desc) or '无保存事件'} |")
    lines += ['', '✓ 表示找到次日开盘前存档匹配；未标记者不可当作已证实的当时提醒。', '', '## 资源', '',json.dumps(result['resources'],ensure_ascii=False), '']
    (OUT/'signal_audit.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(result['summary'],ensure_ascii=False))
    print(json.dumps(result['resources'],ensure_ascii=False))


if __name__ == '__main__':
    main()
