"""Read-only, bounded MRGC industry audit for the fixed twenty-session research window."""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from contextlib import closing
import csv
import json
from pathlib import Path
import resource
import sqlite3
import time


START = '2026-09-28'
END = '2026-10-30'
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def industry_labels() -> tuple[str, ...]:
    """Read the project's existing broad-category allowlist without importing its API."""
    tree = ast.parse((ROOT / 'backend/app/api/v1/quant.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == 'COMMON_INDUSTRY_SECTORS'
            for target in node.targets
        ):
            labels = ast.literal_eval(node.value)
            if not isinstance(labels, tuple) or not all(isinstance(s, str) for s in labels):
                raise ValueError('Unexpected industry allowlist')
            # The existing API omits the genuine vendor broad category 综合.
            # It is validated against sector_list below, never inferred per stock.
            return (*labels, '综合')
    raise ValueError('COMMON_INDUSTRY_SECTORS missing')


def collect() -> dict:
    """Collect only selected stocks, memberships, and calendar/run-log records."""
    labels = industry_labels()
    with closing(sqlite3.connect(
        (ROOT / 'backend/data/local_quant.db').resolve().as_uri() + '?mode=ro',
        uri=True, timeout=2,
    )) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA cache_size=-8192')
        conn.execute('BEGIN')
        extra = conn.execute(
            "SELECT sector_code,source FROM sector_list WHERE sector_name='综合'"
        ).fetchone()
        if extra != ('BK1217', 'eastmoney_stock_plate'):
            raise ValueError(f'Unexpected 综合 source: {extra}')
        dates = [r[0] for r in conn.execute(
            'SELECT DISTINCT substr(trade_date,1,10) FROM trading_calendar '
            'WHERE trade_date>=? AND trade_date<? AND is_open=1 ORDER BY 1',
            (START, END + 'T'),
        )]
        if len(dates) != 20 or dates[0] != START or dates[-1] != END:
            raise ValueError(f'Unexpected calendar: {dates}')
        previous = conn.execute(
            'SELECT max(trade_date) FROM trading_calendar WHERE trade_date<? AND is_open=1',
            (START,),
        ).fetchone()[0][:10]
        rows = conn.execute(
            "SELECT substr(trade_date,1,10),symbol FROM quant_preselect_results "
            "WHERE strategy_name='mrgc_v1' AND trade_date>=? AND trade_date<? "
            'GROUP BY substr(trade_date,1,10),symbol ORDER BY 1,2',
            (previous, END + 'T'),
        ).fetchall()
        selected = defaultdict(set)
        for day, symbol in rows:
            selected[day].add(symbol)
        targets = set().union(*(selected[d] for d in dates))
        historical_symbols = {r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM quant_preselect_results WHERE strategy_name='mrgc_v1' "
            'AND trade_date<?', (END + 'T',),
        )}
        all_symbols = sorted(historical_symbols | targets | selected[previous])
        marks = ','.join('?' for _ in all_symbols)
        infos = {s: {'name': n, 'stock_info_industry': i} for s, n, i in conn.execute(
            f'SELECT symbol,name,industry FROM stock_info WHERE symbol IN ({marks})',
            all_symbols,
        )}
        memberships = defaultdict(list)
        for s, label, code in conn.execute(
            f'SELECT symbol,sector_name,sector_code FROM stock_sector_map '
            f"WHERE sector_type='Industry' AND symbol IN ({marks})", all_symbols,
        ):
            if label in labels:
                memberships[s].append({'label': label, 'sector_code': code})
        mismatches = {s: memberships[s] for s in all_symbols if len(memberships[s]) != 1}
        if mismatches:
            raise ValueError(f'Missing or ambiguous broad industry: {mismatches}')
        mapping = {s: memberships[s][0]['label'] for s in all_symbols}
        historical = defaultdict(set)
        versions = []
        for label in labels:
            version = conn.execute(
                'SELECT snapshot_id,effective_from,captured_at,member_count '
                'FROM sector_membership_version WHERE sector_name=? '
                "AND effective_from<=? AND membership_status='daily_snapshot' "
                'ORDER BY effective_from DESC,captured_at DESC LIMIT 1', (label, START),
            ).fetchone()
            if not version:
                raise ValueError(f'No pre-window daily snapshot for {label}')
            snapshot, effective, captured, count = version
            versions.append(dict(label=label, snapshot_id=snapshot, effective_from=effective,
                                 captured_at=captured, member_count=count))
            for (symbol,) in conn.execute(
                f'SELECT symbol FROM sector_membership_item WHERE snapshot_id=? '
                f'AND symbol IN ({marks})', (snapshot, *all_symbols),
            ):
                historical[symbol].add(label)
        historical_mismatches = {s: sorted(historical[s]) for s in all_symbols
                                 if historical[s] != {mapping[s]}}
        success_days = {r[0] for r in conn.execute(
            "SELECT DISTINCT substr(trade_date,1,10) FROM strategy_run_log "
            "WHERE strategy_name='mrgc_v1' AND status='success' "
            'AND trade_date>=? AND trade_date<?', (previous, END + 'T'),
        )}
        missing_runs = sorted(set([previous, *dates]) - success_days)
        if missing_runs:
            raise ValueError(f'Missing successful MRGC audit: {missing_runs}')
        source_info = conn.execute(
            'SELECT sector_type,source,count(*) FROM sector_list GROUP BY sector_type,source'
        ).fetchall()
    return dict(labels=labels, dates=dates, previous=previous, selected=selected,
                targets=targets, historical_symbols=historical_symbols,
                mapping=mapping, infos=infos, memberships=memberships,
                versions=versions, historical_mismatches=historical_mismatches,
                source_info=source_info)


def write_mapping(data: dict) -> None:
    """Write explicit provenance and one exact existing label per target stock."""
    payload = {
        'window_start': START, 'window_end': END,
        'trade_dates': data['dates'], 'stock_count': len(data['historical_symbols']),
        'window_stock_count': len(data['targets']),
        'window_symbols': sorted(data['targets']),
        'mapping': {s: data['mapping'][s] for s in sorted(data['historical_symbols'])},
        'source': {
            'classification': '本地固定行业大类（东财来源）',
            'allowlist_file': 'backend/app/api/v1/quant.py',
            'allowlist_name': 'COMMON_INDUSTRY_SECTORS',
            'allowlist_extension': '综合：sector_list中的东财BK1217；精确成员映射，不按名称猜测',
            'allowlist': list(data['labels']),
            'membership_table': 'stock_sector_map',
            'vendor_source': data['source_info'],
            'historical_snapshot_status': 'daily_snapshot',
            'historical_snapshot_effective_from': sorted({v['effective_from']
                                                         for v in data['versions']}),
            'historical_mismatches': data['historical_mismatches'],
            'snapshots': data['versions'],
        },
        'limitations': [
            '采用项目已有固定30类加东财现存综合BK1217，共31类；未经过外部申万分类主表认证，不称正式申万一级。',
            'stock_info.industry混合不同层级，保留作原标签对照，不用于行业份额加总。',
            '当前成员与窗口前2026-08-26真实daily_snapshot逐股核验；该快照之后没有新的成员版本，无法证明所有期间变更均被及时收集。',
            '2023-01-03版本为current_snapshot_backfill，不能作为2023年已知行业成员的证据。',
            '映射覆盖全部历史MRGC426只，避免只用近期135只做历史回测的幸存者偏差；但08-26以前的行业归属仍是后来成员快照，历史行业策略对照存在分类回看偏差。',
            '行业份额按当日MRGC唯一股票计数，不代表资金流入、行业涨幅或全市场行业强度。',
            '新增/退出相对前一交易日当日选股集合；不表示20日观察池新增/移除。',
        ],
        'stocks': {s: dict(data['infos'].get(s, {}),
                           mapped_industry=data['mapping'][s],
                           sector_code=data['memberships'][s][0]['sector_code'])
                   for s in sorted(data['historical_symbols'])},
    }
    (OUT / 'industry_mapping.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def summarize(data: dict) -> dict:
    """Count entries/exits and repeated stock-days without confusing unique stocks."""
    daily = []
    seen = set()
    prior = data['selected'][data['previous']]
    for day in data['dates']:
        current = data['selected'][day]
        entrants, exits = current - prior, prior - current
        first_window = current - seen
        for label in data['labels']:
            members = {s for s in current if data['mapping'][s] == label}
            entered = {s for s in entrants if data['mapping'][s] == label}
            exited = {s for s in exits if data['mapping'][s] == label}
            first = members & first_window
            daily.append(dict(trade_date=day, industry=label, selected_count=len(members),
                              total_selected=len(current), share_pct=round(100 * len(members)
                              / len(current), 6) if current else 0,
                              entered_vs_previous_day=len(entered),
                              exited_vs_previous_day=len(exited),
                              first_seen_in_window=len(first),
                              retained_from_previous_day=len(members & prior),
                              symbols='|'.join(sorted(members)),
                              entered_symbols='|'.join(sorted(entered)),
                              exited_symbols='|'.join(sorted(exited))))
        seen |= current
        prior = current
    with (OUT / 'industry_daily.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(daily[0]))
        writer.writeheader()
        writer.writerows(daily)
    segments = []
    for offset in range(0, 20, 5):
        days = data['dates'][offset:offset + 5]
        counts = Counter(data['mapping'][s] for d in days for s in data['selected'][d])
        unique = set().union(*(data['selected'][d] for d in days))
        unique_counts = Counter(data['mapping'][s] for s in unique)
        total = sum(counts.values())
        segments.append(dict(start=days[0], end=days[-1], stock_days=total,
                             unique_stocks=len(unique), counts=dict(counts),
                             unique_counts=dict(unique_counts)))
    counts = Counter(data['mapping'][s] for d in data['dates'] for s in data['selected'][d])
    unique_counts = Counter(data['mapping'][s] for s in data['targets'])
    return dict(daily=daily, segments=segments, counts=counts, unique_counts=unique_counts)


def write_review(data: dict, summary: dict, elapsed: float) -> None:
    total = sum(summary['counts'].values())
    raw_labels = {data['infos'].get(s, {}).get('stock_info_industry') for s in data['targets']}
    rows = [
        '# MRGC 近20交易日行业复盘', '',
        f'窗口：{START}—{END}，20个真实交易日；{len(data["targets"])}只不同股票，'
        f'{total}个“股票×入选日”。全部20日及首日比较基准{data["previous"]}均有MRGC成功运行审计。', '',
        '## 行业口径', '',
        f'`stock_info.industry`在候选中包含{len(raw_labels)}个标签，混有交通运输、医疗服务、'
        '城商行Ⅲ等不同层级。直接按此字段排名会拆散银行、医药等大类。', '',
        '本报告采用项目已有`COMMON_INDUSTRY_SECTORS`固定30类清单，补充东财真实存在的'
        '`综合`（BK1217），共31类，与东财来源的'
        '`stock_sector_map`精确联接。所有候选均恰好匹配一个大类，缺失0、多归属0；'
        '称“本地固定行业大类（东财来源）”，不冒称已认证的申万一级。', '',
        f'与窗口前2026-08-26的`daily_snapshot`成员逐股核验，差异股票'
        f'{len(data["historical_mismatches"])}只。更早的2023-01-03版本是2026年回填，'
        '不用于证明历史时点成员。快照之后没有新的成员版本，仍可能漏记分类变更。', '',
        f'映射文件额外覆盖全部历史MRGC的{len(data["historical_symbols"])}只股票，'
        '历史回测不能只使用近期135只；但2026-08-26以前的行业归属是后见分类，'
        '行业过滤历史表现须单列此局限。近期行业统计仍只包含目标135只。', '',
        '## 全窗口份额', '',
        '| 行业大类 | 不同股票数 | 入选股票日 | 占全部股票日 |',
        '|---|---:|---:|---:|',
    ]
    for label, count in summary['counts'].most_common():
        rows.append(f'| {label} | {summary["unique_counts"][label]} | {count} | {100*count/total:.2f}% |')
    rows += ['', '## 每5交易日分段', '',
             '股票日份额反映重复入选的持续性；不同股票数反映覆盖广度，两者分别列示。', '',
             '| 区间 | 全部股票日 / 不同股票 | 前五行业：股票日（份额；不同股票） |',
             '|---|---:|---|']
    for segment in summary['segments']:
        top = sorted(segment['counts'].items(), key=lambda pair: (-pair[1], pair[0]))[:5]
        description = '；'.join(f'{label} {count}（{count/segment["stock_days"]*100:.1f}%；'
                                f'{segment["unique_counts"][label]}只）' for label, count in top)
        rows.append(f'| {segment["start"]}—{segment["end"]} | '
                    f'{segment["stock_days"]} / {segment["unique_stocks"]} | {description} |')
    rows += ['', '## 每日进出与行业集中', '',
             '“新增/退出”相对前一交易日的当日选股集合；“窗口初见”仅指本窗口首次出现，'
             f'不等同历史首次入选。首日以{data["previous"]}为比较基准。', '',
             '| 日期 | 入选 | 新增 | 退出 | 窗口初见 | 第一行业及份额 |',
             '|---|---:|---:|---:|---:|---|']
    for day in data['dates']:
        items = [r for r in summary['daily'] if r['trade_date'] == day]
        top = max(items, key=lambda r: (r['selected_count'], r['industry']))
        rows.append(f'| {day} | {top["total_selected"]} | '
                    f'{sum(r["entered_vs_previous_day"] for r in items)} | '
                    f'{sum(r["exited_vs_previous_day"] for r in items)} | '
                    f'{sum(r["first_seen_in_window"] for r in items)} | '
                    f'{top["industry"]} {top["selected_count"]}只（{top["share_pct"]:.1f}%） |')
    rows += ['', '## 解读边界', '',
             '行业入选份额只能显示MRGC候选构成；不能直接证明资金轮动、绝对行业上涨或可交易盈利。'
             '某行业末段份额上升也可能来自其他行业入选减少，必须同时观察绝对数量和不同股票数。'
             '由本窗口排名反选行业并计算同窗口收益属于样本内筛选，不能据此宣称高胜率。', '',
             '行业收益、个股入场时机与时间外验证由主回测统一计算，本子报告不挑选收益赢家。', '',
             '## 文件和资源', '',
             '`industry_mapping.json`保留全部历史426只股票映射、原标签与快照来源，标出近期135只；'
             '`industry_daily.csv`包含20日×31类的620行每日计数、份额、进出和代码；'
             '`industry_audit.py`可在仓库根目录使用标准Python复现。', '',
             f'本次仅只读SQLite有界查询，未读取全量行情，无网络请求、后台任务或数据库写入。'
             f'运行耗时{elapsed:.3f}秒；进程峰值RSS原始计数'
             f'{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}（macOS单位字节）。', '']
    (OUT / 'industry_review.md').write_text('\n'.join(rows))


def main() -> None:
    started = time.perf_counter()
    data = collect()
    write_mapping(data)
    summary = summarize(data)
    write_review(data, summary, time.perf_counter() - started)
    assert len(summary['daily']) == 20 * len(data['labels'])
    assert sum(r['selected_count'] for r in summary['daily']) == sum(summary['counts'].values())
    assert sum(r['first_seen_in_window'] for r in summary['daily']) == len(data['targets'])
    print(json.dumps(dict(stocks=len(data['targets']), stock_days=sum(summary['counts'].values()),
                          industries=len(summary['counts']),
                          historical_mismatches=data['historical_mismatches'],
                          elapsed_seconds=round(time.perf_counter() - started, 3)),
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
