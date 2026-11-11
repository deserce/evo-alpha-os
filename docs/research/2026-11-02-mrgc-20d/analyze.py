"""Bounded, read-only MRGC event study. See protocol.md for predeclared rules."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import resource
import sqlite3
import statistics as st
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DB = ROOT / 'backend/data/local_quant.db'
END = '2026-10-30'
START = '2026-09-28'
COST = 0.004
RULES = ('daily', 'new5', 'first20', 'reentry5', 'sector_new5',
         'controlled_new5', 'c_buy', 'chan_buy', 'c_buy_archived', 'chan_buy_archived')


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2,
                                     allow_nan=False), encoding='utf-8')


def write_csv(name, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / name).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def wilson(wins, n):
    if not n:
        return [None, None]
    z = 1.96
    p = wins / n
    mid = (p + z*z/(2*n))/(1+z*z/n)
    width = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [mid-width, mid+width]


def load():
    mapping_doc = json.loads((OUT / 'industry_mapping.json').read_text())
    mapping = mapping_doc['mapping']
    evidence = json.loads((OUT / 'signal_evidence.json').read_text())
    with closing(sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True,
                                 timeout=2)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA cache_size=-8192')
        c.execute('BEGIN')
        days = [r[0] for r in c.execute('SELECT DISTINCT trade_date FROM trading_calendar '
                "WHERE is_open=1 AND trade_date>='2026-04-01' AND trade_date<=? ORDER BY 1", (END,))]
        runs = {r[0][:10] for r in c.execute("SELECT DISTINCT trade_date FROM strategy_run_log "
                "WHERE strategy_name='mrgc_v1' AND status='success' AND trade_date<=?", (END,))}
        results = [dict(r) for r in c.execute("SELECT trade_date,symbol,meta_info,created_at "
                "FROM quant_preselect_results WHERE strategy_name='mrgc_v1' "
                "AND result_type='PRESELECT' AND trade_date<=? ORDER BY trade_date,symbol", (END,))]
        symbols = sorted({r['symbol'] for r in results})
        info = {r['symbol']: dict(r) for r in c.execute('SELECT symbol,name,industry,list_date FROM stock_info')
                if r['symbol'] in symbols}
        prices, rps, digest = {}, {}, hashlib.sha256()
        raw_missing = 0
        for symbol in symbols:
            rows = [dict(r) for r in c.execute('SELECT p.trade_date,p.open,p.high,p.low,p.close,'
                    'p.volume,p.quality_flag,p.adjustment_mode,r.close AS raw_close '
                    'FROM stock_daily_prices p LEFT JOIN stock_daily_prices_raw r '
                    'ON r.symbol=p.symbol AND r.trade_date=p.trade_date '
                    'WHERE p.symbol=? AND p.trade_date>=? AND p.trade_date<=? ORDER BY p.trade_date',
                    (symbol, days[0], END))]
            digest.update(json.dumps(rows, sort_keys=True).encode())
            prices[symbol] = {r['trade_date']: r for r in rows}
            raw_missing += sum(r['raw_close'] is None for r in rows)
            rps[symbol] = {r['trade_date']: dict(r) for r in c.execute(
                'SELECT trade_date,rps_20,rps_50,rps_250 FROM quant_feature_rps '
                'WHERE symbol=? AND trade_date>=? AND trade_date<=?', (symbol, days[0], END))}
        indices = {r['trade_date']: dict(r) for r in c.execute('SELECT trade_date,open,close '
                "FROM index_daily WHERE index_code='000300' AND trade_date>=? AND trade_date<=?",
                (days[0], END))}
    return dict(days=days, runs=runs, results=results, symbols=symbols, info=info,
                prices=prices, rps=rps, indices=indices, mapping=mapping,
                evidence=evidence, input_hash=digest.hexdigest(), raw_missing=raw_missing)


def valid_bar(bar):
    if not bar:
        return False
    values = [bar.get(k) for k in ('open', 'high', 'low', 'close', 'volume')]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values):
        return False
    return bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']


def limit_rate(symbol):
    if symbol.startswith(('300', '301', '688', '689')):
        return 0.20
    if symbol.startswith(('4', '8', '92')):
        return 0.30
    return 0.10


def open_blocked(symbol, bar, previous, side):
    """Conservative limit proxy, adjusted price ratios; not order-book evidence."""
    if not valid_bar(bar) or not valid_bar(previous):
        return True
    ratio = bar['open'] / previous['close'] - 1
    threshold = limit_rate(symbol) - 0.003
    return ratio >= threshold if side == 'buy' else ratio <= -threshold


def features(data):
    days, prices = data['days'], data['prices']
    feats = {}
    anomalies = []
    for symbol, bars in prices.items():
        feats[symbol] = {}
        closes, ranges = [], []
        previous = None
        for day in days:
            bar = bars.get(day)
            if not valid_bar(bar):
                previous = None
                closes, ranges = [], []
                continue
            if previous:
                ret = bar['close']/previous['close'] - 1
                if abs(ret) > limit_rate(symbol) + 0.025:
                    anomalies.append(dict(symbol=symbol, date=day, return_close=ret))
                tr = max(bar['high']-bar['low'], abs(bar['high']-previous['close']),
                         abs(bar['low']-previous['close']))
            else:
                ret, tr = None, bar['high']-bar['low']
            closes.append(bar['close'])
            ranges.append(tr)
            feats[symbol][day] = dict(ma20=st.mean(closes[-20:]) if len(closes)>=20 else None,
                atr=st.mean(ranges[-14:]) if len(ranges)>=14 else None, day_return=ret)
            previous = bar
    return feats, anomalies


def candidates(data, feats):
    days, mapping = data['days'], data['mapping']
    hits = defaultdict(set)
    for row in data['results']:
        hits[row['trade_date'][:10]].add(row['symbol'])
    sectors = {d: Counter(mapping.get(s, '未分类') for s in hits[d]) for d in days}
    current = defaultdict(set)
    archive = defaultdict(set)
    for key, target in [('signals', current), ('archive_signals', archive)]:
        for row in data['evidence'][key]:
            target[(row['date'], row['symbol'])].add(row['kind'])
    all_c = defaultdict(set)
    for row in data['evidence']['all_stock_signals']:
        all_c[(row['date'], row['symbol'])].add(row['kind'])
    output = {rule: [] for rule in RULES}
    daily = []
    for i, day in enumerate(days):
        if i < 20 or day not in data['runs']:
            continue
        prior = days[i-20:i]
        ready = all(d in data['runs'] for d in prior)
        prev5 = set().union(*(hits[d] for d in prior[-5:]))
        prev20 = set().union(*(hits[d] for d in prior))
        pool20 = set().union(*(hits[d] for d in days[i-19:i+1]))
        for symbol in sorted(hits[day] | pool20):
            sector = mapping.get(symbol, '未分类')
            bar = data['prices'][symbol].get(day)
            tradable = valid_bar(bar) and 'ST' not in data['info'].get(symbol, {}).get('name', '').upper()
            feat = feats[symbol].get(day, {})
            base = dict(signal_date=day, symbol=symbol, name=data['info'].get(symbol, {}).get('name', symbol),
                        industry=sector, rps250=data['rps'][symbol].get(day, {}).get('rps_250') or 0,
                        price=bar.get('close') if bar else None, atr=feat.get('atr'), ma20=feat.get('ma20'),
                        history_ready=ready,signal_bar_tradable=tradable)
            if symbol in hits[day]:
                new5 = ready and symbol not in prev5
                first20 = ready and symbol not in prev20
                secn = sectors[day][sector]
                secavg = st.mean(sectors[d][sector] for d in prior[-5:])
                share = secn / len(hits[day]) if hits[day] else 0
                avgshare = st.mean(sectors[d][sector]/len(hits[d]) if hits[d] else 0 for d in prior[-5:])
                controlled = bool(tradable and feat.get('atr') and feat.get('ma20') and
                    feat['ma20'] < bar['close'] <= feat['ma20']+1.5*feat['atr'] and
                    feat.get('day_return') is not None and feat['day_return'] < .05)
                flags = dict(daily=True, new5=new5, first20=first20,
                    reentry5=new5 and not first20,
                    sector_new5=new5 and sector!='未分类' and secn>=3 and secn>secavg and share>avgshare,
                    controlled_new5=new5 and controlled)
                for rule, accepted in flags.items():
                    if accepted and tradable:
                        output[rule].append(base.copy())
                if day >= START:
                    daily.append(dict(base, new5=new5, first20=first20, sector_count=secn,
                                      sector_prior5_mean=secavg, controlled=controlled))
            if symbol in pool20 and ready and tradable:
                for rule, kind, source in [('c_buy', 'C_BUY', current), ('chan_buy', 'CHAN_BUY', current),
                     ('c_buy_archived', 'C_BUY', archive), ('chan_buy_archived', 'CHAN_BUY', archive)]:
                    if kind in source[(day,symbol)]:
                        output[rule].append(base.copy())
    return output, hits, daily, all_c


def trade(candidate, horizon, data, exits=None, managed=False):
    """Enter next session open; exit after horizon full sessions, obey T+1."""
    days = data['days']
    symbol = candidate['symbol']
    i = data['day_index'][candidate['signal_date']]
    entry_i = i+1
    result = dict(candidate, horizon=horizon, status='immature', managed=managed)
    if not valid_bar(data['prices'][symbol].get(days[i])):
        return dict(result,status='nontradable_signal')
    if entry_i >= len(days):
        return result
    bars = data['prices'][symbol]
    entry = bars.get(days[entry_i])
    if not valid_bar(entry):
        return dict(result, status='missing_entry')
    if open_blocked(symbol, entry, bars.get(days[i]), 'buy'):
        return dict(result, status='limit_entry')
    # Newly listed/ST and detected extreme corrupt transitions are excluded upstream or here.
    target_i = entry_i+horizon
    stop = entry['open']-1.5*candidate['atr'] if candidate.get('atr') else None
    reason = 'time'
    if managed:
        for j in range(entry_i, min(target_i, len(days))):
            bar = bars.get(days[j])
            if valid_bar(bar) and ((stop is not None and bar['close'] < stop) or
                    (exits and exits.get((days[j],symbol),set()) & {'C_EXIT','C_RISK'})):
                target_i = j+1
                reason = 'close_stop' if stop is not None and bar['close'] < stop else 'c_exit_or_risk'
                break
    result.update(entry_date=days[entry_i], entry_price=entry['open'], stop_price=stop,
                  planned_exit_date=days[target_i] if target_i<len(days) else '', exit_reason=reason)
    for j in range(target_i, len(days)):
        bar = bars.get(days[j])
        if not valid_bar(bar) or open_blocked(symbol,bar,bars.get(days[j-1]),'sell'):
            continue
        path = [bars.get(d) for d in days[entry_i:j]]
        if any(not valid_bar(b) for b in path):
            # Could be suspension, but no trusted classification is stored; do not fill prices.
            return dict(result, status='missing_path')
        if any((symbol,d) in data['anomaly_keys'] for d in days[i+1:j+1]):
            return dict(result, status='abnormal_path')
        gross = bar['open']/entry['open']-1
        maximum = max([b['high'] for b in path]+[bar['open']])/entry['open']-1
        minimum = min([b['low'] for b in path]+[bar['open']])/entry['open']-1
        bench_in = data['indices'].get(days[entry_i],{}).get('open')
        bench_out = data['indices'].get(days[j],{}).get('open')
        benchmark = bench_out/bench_in-1 if bench_in and bench_out else None
        net = (1+gross-COST/2)/(1+COST/2)-1
        return dict(result, status='closed', exit_date=days[j], exit_price=bar['open'],
                    gross_return=gross, net_return=net, mfe=maximum, mae=minimum,
                    benchmark_return=benchmark, excess=net-benchmark if benchmark is not None else None,
                    exit_delay=j-target_i)
    return result


def simulate(cands, horizon, data, exits=None, managed=False):
    last_exit = {}
    records = []
    for candidate in sorted(cands, key=lambda r:(r['signal_date'],r['symbol'])):
        symbol = candidate['symbol']
        i = data['day_index'][candidate['signal_date']]
        if i+1>=len(data['days']):
            records.append(trade(candidate,horizon,data,exits,managed))
            continue
        entry_day = data['days'][i+1]
        if entry_day < last_exit.get(symbol, ''):
            records.append(dict(candidate, status='overlap',horizon=horizon,managed=managed))
            continue
        record = trade(candidate,horizon,data,exits,managed)
        records.append(record)
        if record['status']=='closed':
            last_exit[symbol] = record['exit_date']
        elif record['status'] in {'immature','missing_path','abnormal_path'} and record.get('entry_date'):
            last_exit[symbol] = END
    return records


def stats(records):
    rows = [r for r in records if r['status']=='closed']
    values = [r['net_return'] for r in rows]
    n = len(rows)
    result = dict(n=n, unique_stocks=len({r['symbol'] for r in rows}),
                  signal_days=len({r['signal_date'] for r in rows}),
                  statuses=dict(Counter(r['status'] for r in records)))
    if not n:
        return result
    win = [v for v in values if v>0]
    loss = [v for v in values if v<=0]
    groups=defaultdict(list)
    for r in rows:
        groups[r['signal_date']].append(r['net_return'])
    group_values=list(groups.values())
    rng=random.Random(20261102)
    boot=[]
    for _ in range(1000):
        sample=[v for group in rng.choices(group_values,k=len(group_values)) for v in group]
        boot.append(st.mean(sample))
    boot.sort()
    excess=[r['excess'] for r in rows if r.get('excess') is not None]
    result.update(win_rate=len(win)/n, win_ci=wilson(len(win),n), mean=st.mean(values),
        median=st.median(values), mean_ci_cluster=[boot[24],boot[974]], worst=min(values), best=max(values),
        avg_win=st.mean(win) if win else None, avg_loss=st.mean(loss) if loss else None,
        payoff=st.mean(win)/abs(st.mean(loss)) if win and loss and st.mean(loss) else None,
        profit_factor=sum(win)/abs(sum(loss)) if loss and sum(loss) else None,
        mean_mae=st.mean(r['mae'] for r in rows), worst_mae=min(r['mae'] for r in rows),
        mean_excess=st.mean(excess) if excess else None,
        fee02_mean=st.mean((1+r['gross_return']-.001)/1.001-1 for r in rows),
        fee06_mean=st.mean((1+r['gross_return']-.003)/1.003-1 for r in rows),
        fee06_win_rate=sum((1+r['gross_return']-.003)/1.003>1 for r in rows)/n)
    return result


def portfolio(records, data, start, end=END):
    """Five equal-capacity slots, mark-to-market incl. incomplete end positions."""
    offers=defaultdict(list)
    for r in records:
        if r.get('entry_date') and r['status'] in {'closed','immature','missing_path','abnormal_path'}:
            offers[r['entry_date']].append(r)
    cash=1.0
    held=[]
    curve=[]
    chosen=[]
    peak=1.0
    worst_dd=0.0
    for day in data['days']:
        if day < start or day>end:
            continue
        keep=[]
        for pos in held:
            r=pos['record']
            if r.get('exit_date')==day:
                cash+=pos['quantity']*r['exit_price']-pos['capital']*COST/2
            else:
                keep.append(pos)
        held=keep
        if any(not valid_bar(data['prices'][p['record']['symbol']].get(day)) for p in held):
            return dict(status='DATA_INCOMPLETE', reason='missing held mark', date=day,
                        return_nav=None,max_drawdown=None,curve=curve)
        if any((p['record']['symbol'],day) in data['anomaly_keys'] for p in held):
            return dict(status='DATA_INCOMPLETE', reason='abnormal held mark', date=day,
                        return_nav=None,max_drawdown=None,curve=curve)
        current_symbols={p['record']['symbol'] for p in held}
        candidates_today=sorted(offers.get(day,[]), key=lambda r:(-r['rps250'],r['symbol']))
        # Use open mark for sizing, never today's close.
        nav_open=cash+sum(p['quantity']*data['prices'][p['record']['symbol']][day]['open'] for p in held)
        for r in candidates_today:
            if len(held)>=5:
                break
            if r['symbol'] in current_symbols:
                continue
            if r['status'] in {'missing_path','abnormal_path'}:
                return dict(status='DATA_INCOMPLETE',reason=r['status'],symbol=r['symbol'],
                            date=day,return_nav=None,max_drawdown=None,curve=curve)
            if (r['symbol'],day) in data['anomaly_keys']:
                return dict(status='DATA_INCOMPLETE',reason='abnormal entry mark',symbol=r['symbol'],
                            date=day,return_nav=None,max_drawdown=None,curve=curve)
            amount=min(nav_open/5,cash/(1+COST/2))
            if amount<1e-8:
                break
            cash-=amount*(1+COST/2)
            held.append(dict(record=r,quantity=amount/r['entry_price'],capital=amount))
            current_symbols.add(r['symbol'])
            chosen.append(r)
        nav=cash+sum(p['quantity']*data['prices'][p['record']['symbol']][day]['close'] for p in held)
        peak=max(peak,nav)
        worst_dd=min(worst_dd,nav/peak-1)
        curve.append(dict(date=day,nav=nav,cash=cash,positions=len(held),drawdown=nav/peak-1))
    return dict(status='OK',return_nav=curve[-1]['nav']-1 if curve else 0,max_drawdown=worst_dd,
                entries=len(chosen),open_positions=len(held),curve=curve,
                trades=[{k:r.get(k) for k in ('symbol','name','signal_date','entry_date','exit_date','net_return')} for r in chosen])


def main():
    started=time.monotonic()
    data=load()
    data['day_index']={d:i for i,d in enumerate(data['days'])}
    feats, anomalies=features(data)
    data['anomaly_keys']={(r['symbol'],r['date']) for r in anomalies}
    cands,hits,daily,exits=candidates(data,feats)
    recent=data['days'][-20:]
    assert recent[0]==START and recent[-1]==END
    assert all(d in data['runs'] for d in recent)
    # Historical period requires 20 previous completed strategy runs.
    hist_days=[d for i,d in enumerate(data['days']) if i>=20 and d<='2026-09-18'
               and d in data['runs'] and all(p in data['runs'] for p in data['days'][i-20:i])]
    history_start=min(hist_days)
    all_records=[]
    metrics=[]
    portfolios={}
    for rule in RULES:
        for period in ('history','recent'):
            subset=[r for r in cands[rule] if (r['signal_date']>=START if period=='recent'
                       else r['signal_date'] in hist_days)]
            for horizon,managed in [(3,False),(5,False),(10,False),(5,True)]:
                records=simulate(subset,horizon,data,exits,managed)
                # No historical outcome may enter the recent evaluation window.
                if period=='history':
                    records=[dict(r,status='purged_boundary') if r.get('exit_date','')>=START
                             or (r['status']=='immature') else r for r in records]
                key=f'{rule}:{period}:{horizon}:{"managed" if managed else "fixed"}'
                metrics.append(dict(rule=rule,period=period,horizon=horizon,managed=managed,**stats(records)))
                if period=='recent' and horizon==5:
                    # Finite portfolio must perform its own holding checks; independent
                    # per-symbol trades must not suppress an opportunity it never bought.
                    opportunities=[trade(r,horizon,data,exits,managed) for r in subset]
                    portfolios[key]=portfolio(opportunities,data,START)
                all_records.extend(dict(r,rule=rule,period=period) for r in records)
    review=[]
    for row in daily:
        outcome=dict(row)
        for h in (1,3,5,10):
            t=trade(row,h,data)
            outcome[f'status_{h}']=t['status']
            for key in ('net_return','mfe','mae','entry_date','exit_date'):
                outcome[f'{key}_{h}']=t.get(key)
        review.append(outcome)
    bystock=[]
    signalmap=defaultdict(lambda:defaultdict(set))
    for r in data['evidence']['all_stock_signals']:
        if r['date']>=START:
            signalmap[r['symbol']][r['kind']].add(r['date'])
    for symbol in sorted(set().union(*(hits[d] for d in recent))):
        rows=[r for r in review if r['symbol']==symbol]
        dates=[d for d in recent if symbol in hits[d]]
        before=[r['trade_date'][:10] for r in data['results'] if r['symbol']==symbol and r['trade_date'][:10]<START]
        bars=data['prices'][symbol]
        first=rows[0]
        first_signal=first['signal_date']
        idx=data['day_index'][first_signal]+1
        nextbar=bars.get(data['days'][idx]) if idx<len(data['days']) else None
        latestbar=bars.get(END)
        ongoing=(latestbar['close']/nextbar['open']-COST/2)/(1+COST/2)-1 if valid_bar(latestbar) and valid_bar(nextbar) else None
        row=dict(symbol=symbol,name=data['info'][symbol]['name'],industry=data['mapping'].get(symbol,'未分类'),
            first_in_window=dates[0],last_in_window=dates[-1],hit_count=len(dates),hit_dates=';'.join(dates),
            first_saved_hit=min([*before,*dates]),prior_last_hit=max(before) if before else '',
            first_is_new5=first['new5'],first_is_first20=first['first20'],
            first_entry_to_latest_marked_return=ongoing)
        row.update({f'first_{k}':v for k,v in first.items() if k.startswith(('net_return_','status_','mfe_','mae_'))})
        row.update({kind+'_dates':';'.join(sorted(signalmap[symbol][kind])) for kind in ('C_BUY','C_EXIT','C_RISK','CHAN_BUY','CHAN_SELL')})
        bystock.append(row)
    perday=[]
    for day in recent:
        ds=[r for r in daily if r['signal_date']==day]
        prev=data['days'][data['day_index'][day]-1]
        perday.append(dict(date=day,count=len(hits[day]),added_vs_previous=len(hits[day]-hits[prev]),
            removed_vs_previous=len(hits[prev]-hits[day]),new5=sum(r['new5'] for r in ds),
            first20=sum(r['first20'] for r in ds),industries=len({r['industry'] for r in ds})))
    audit=dict(window=recent,history_start=history_start,history_end='2026-09-18',
        history_dates=hist_days,symbols_recent=len(bystock),hit_rows_recent=len(review),
        saved_hit_rows_recent=sum(len(hits[d]) for d in recent),
        tradable_hit_rows_recent=sum(r['signal_bar_tradable'] for r in daily),
        nontradable_signal_bars=[dict(date=d,symbol=s) for d in recent for s in hits[d]
                                if not valid_bar(data['prices'][s].get(d))],
        symbols_history_universe=len(data['symbols']),price_rows=sum(len(v) for v in data['prices'].values()),
        raw_missing=data['raw_missing'],anomalies=anomalies,input_sha256=data['input_hash'],
        industry_unmapped=[s for s in data['symbols'] if s not in data['mapping']],
        signal_audit_summary=data['evidence'].get('summary'),
        resources=dict(elapsed_seconds=round(time.monotonic()-started,3),
            peak_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,2)))
    dump('audit.json',audit)
    dump('strategy_metrics.json',metrics)
    dump('portfolios.json',portfolios)
    write_csv('strategy_metrics.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v
                                       for k,v in r.items()} for r in metrics])
    write_csv('trades.csv',all_records)
    write_csv('stock_review.csv',bystock)
    write_csv('hit_outcomes.csv',review)
    signal_outcomes=[]
    recent_symbols={r['symbol'] for r in bystock}
    for signal in data['evidence']['all_stock_signals']:
        symbol,day=signal['symbol'],signal['date']
        if symbol not in recent_symbols or day<START:
            continue
        outcome=dict(signal)
        candidate=dict(signal_date=day,symbol=symbol,atr=feats[symbol].get(day,{}).get('atr'))
        for h in (1,3,5,10):
            t=trade(candidate,h,data)
            outcome[f'status_{h}']=t['status']
            outcome[f'net_return_{h}']=t.get('net_return')
            outcome[f'entry_date_{h}']=t.get('entry_date')
            outcome[f'exit_date_{h}']=t.get('exit_date')
        signal_outcomes.append(outcome)
    write_csv('signal_outcomes.csv',signal_outcomes)
    write_csv('daily_review.csv',perday)
    print(json.dumps(audit,ensure_ascii=False,indent=2))
    print('PRIMARY_RESULTS')
    for m in metrics:
        if m['horizon']==5 and not m['managed']:
            print(json.dumps({k:m.get(k) for k in ('rule','period','n','win_rate','mean','mean_ci_cluster','mean_excess','statuses')},ensure_ascii=False))


if __name__=='__main__':
    main()
