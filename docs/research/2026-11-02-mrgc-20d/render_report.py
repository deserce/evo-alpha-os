"""Render a standalone, offline research page from the generated research artifacts."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import html
import json
import math
from pathlib import Path


HERE = Path(__file__).resolve().parent
RULES = {
    'daily': ('每日入选基线', '信号日入选 MRGC；同股持仓期间不叠加。'),
    'new5': ('5日未入选后出现', '此前5个交易日未入选 MRGC。'),
    'first20': ('20日未入选后出现', '此前20个交易日未入选 MRGC。'),
    'reentry5': ('短暂退出后再入选', '此前5日未入选，6—20日前曾入选。'),
    'sector_new5': ('新出现＋行业扩张', 'new5，行业当日至少3只，数量和份额均高于前5日均值。'),
    'controlled_new5': ('新出现＋控制追涨', 'new5，MA20 < 收盘 ≤ MA20 + 1.5ATR14，日涨幅 < 5%。'),
    'c_buy': ('C 买入确认 · 当前重放', '历史当日近20日 MRGC 池内，C 当日买入确认。'),
    'chan_buy': ('缠论买点 · 当前重放', '历史当日近20日 MRGC 池内，缠论首次可见买点候选。'),
    'c_buy_archived': ('C 买入确认 · 存档', '仅采用下一交易日开盘前已有存档证据的 C 买点。'),
    'chan_buy_archived': ('缠论买点 · 存档', '仅采用下一交易日开盘前已有存档证据的缠论候选。'),
}
KINDS = {'C_BUY': 'C 买入确认', 'C_EXIT': 'C 模拟退出', 'C_RISK': 'C 风险／失效',
         'CHAN_BUY': '缠论买点候选', 'CHAN_SELL': '缠论卖点候选'}
STATUSES = {'closed': '已满期', 'immature': '未满期', 'overlap': '持仓重叠，跳过',
            'purged_boundary': '跨样本边界剔除', 'entry_missing': '入场行情缺失',
            'missing_entry': '入场行情缺失', 'entry_limit_up': '近涨停，跳过',
            'limit_up': '近涨停，跳过', 'exit_missing': '退出行情缺失',
            'invalid_price': '价格无效', 'DATA_INCOMPLETE': '数据不完整，不可用',
            'nontradable_signal': '信号日无有效成交，跳过',
            'nontradable_entry': '入场日无有效成交，跳过',
            'missing_price': '行情缺失', 'bad_price': '异常行情，跳过'}


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ''), quote=True)


def csv_rows(name: str) -> list[dict]:
    with (HERE / name).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def read_json(name: str) -> object:
    return json.loads((HERE / name).read_text())


def number(value: object) -> float | None:
    if value is None or value == '' or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def percent(value: object, signed: bool = True) -> str:
    value = number(value)
    if value is None:
        return '<span class="muted">—</span>'
    label = f'{value * 100:+.2f}%' if signed else f'{value * 100:.1f}%'
    color = 'gain' if value > 0 else 'loss' if value < 0 else ''
    return f'<span class="number {color}">{label}</span>'


def scalar(value: object, digits: int = 2) -> str:
    value = number(value)
    return '—' if value is None else f'{value:.{digits}f}'


def status_label(status: str) -> str:
    return STATUSES.get(status, status or '无记录')


def interval_text(values: object) -> str:
    if not isinstance(values, list) or len(values) != 2:
        return '—'
    if any(number(v) is None for v in values):
        return '—'
    return f'{float(values[0])*100:.1f}% ～ {float(values[1])*100:.1f}%'


def returns_cell(row: dict, horizon: int, prefix: str = '') -> str:
    state = row.get(f'{prefix}status_{horizon}', '')
    value = row.get(f'{prefix}net_return_{horizon}')
    if state != 'closed' or number(value) is None:
        return f'<span class="pending" title="{esc(state)}">{esc(status_label(state))}</span>'
    title = ''
    if row.get(f'entry_date_{horizon}'):
        title = f'{row[f"entry_date_{horizon}"]} 开盘 → {row.get(f"exit_date_{horizon}", "")} 开盘'
    return f'<span title="{esc(title)}">{percent(value)}</span>'


def signal_table(signals: list[dict], outcomes: dict) -> str:
    if not signals:
        return '<p class="muted">本窗口没有保存的 C／缠论事件；覆盖缺失时不能等同“没有信号”。</p>'
    rows = []
    seen = set()
    for signal in sorted(signals, key=lambda r: (r['date'], r['kind'])):
        key = (signal.get('date'), signal.get('kind'), signal.get('pivot_date'), signal.get('reason'))
        if key in seen:
            continue
        seen.add(key)
        archived = signal.get('archive_match') or signal.get('evidence') == 'archived_before_next_open'
        evidence = '开盘前存档可见' if archived else '当前保存／重算，未获及时存档验证'
        if signal.get('in_historical_mrgc_20d_pool') is False:
            evidence += '；当日未在MRGC近20日池内'
        kind = signal['kind']
        label = KINDS.get(kind, kind)
        extra = ' · ZIG可回绘' if kind.startswith('CHAN_') else ''
        outcome = outcomes.get((signal['date'], signal['symbol'], kind))
        returns = ''.join(f'<td>{returns_cell(outcome, h) if outcome else "未评估"}</td>'
                          for h in (1, 3, 5, 10))
        rows.append('<tr>'
                    f'<td class="date">{esc(signal["date"])}</td>'
                    f'<td>{esc(label)}{esc(extra)}</td>'
                    f'<td class="date">{esc(signal.get("pivot_date") or "—")}</td>'
                    f'<td><span class="badge {"verified" if archived else "pending"}">{esc(evidence)}</span>'
                    f'<small>{esc(signal.get("computed_at") or "")}</small></td>'
                    f'<td class="wrap">{esc(signal.get("reason", ""))}</td>{returns}</tr>')
    return ('<div class="table-scroll"><table class="inner"><thead><tr><th>信号确认／首次可见日</th>'
            '<th>信号类型</th><th>历史拐点日</th><th>当时可得性证据 / 计算时间</th>'
            '<th>依据</th><th>信号后1日</th><th>信号后3日</th><th>信号后5日</th>'
            '<th>信号后10日</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')


def stock_table(stocks: list[dict], hits: list[dict], evidence: dict,
                signal_outcomes: list[dict]) -> str:
    hit_map = defaultdict(list)
    for hit in hits:
        hit_map[hit['symbol']].append(hit)
    signal_map = defaultdict(list)
    outcome_map = {(r['date'], r['symbol'], r['kind']): r for r in signal_outcomes}
    for signal in evidence.get('all_stock_signals', evidence.get('signals', [])):
        signal_map[signal['symbol']].append(signal)
    bodies = []
    for stock in sorted(stocks, key=lambda r: (r['first_in_window'], r['symbol'])):
        symbol = stock['symbol']
        row_hits = sorted(hit_map[symbol], key=lambda r: r['signal_date'])
        header = (
            f'<tr class="stock-row"><td><button class="expand" aria-expanded="false" '
            f'aria-controls="detail-{esc(symbol)}" title="展开{esc(stock["name"])}">＋</button></td>'
            f'<td class="stock-name"><strong>{esc(stock["name"])}</strong>'
            f'<small class="number">{esc(symbol)}</small></td>'
            f'<td>{esc(stock["industry"])}</td><td class="date">{esc(stock["first_in_window"])}</td>'
            f'<td class="number">{esc(stock["hit_count"])}</td>'
            + ''.join(f'<td>{returns_cell(stock, h, "first_")}</td>' for h in (1, 3, 5, 10))
            + '</tr>'
        )
        hit_rows = []
        for hit in row_hits:
            tags = []
            if hit.get('new5') == 'True':
                tags.append('5日未入选后出现')
            if hit.get('first20') == 'True':
                tags.append('20日未入选后出现')
            hit_rows.append(
                '<tr><td class="date">' + esc(hit['signal_date']) + '</td>'
                + ''.join(f'<td>{returns_cell(hit, h)}</td>' for h in (1, 3, 5, 10))
                + f'<td class="date">{esc(hit.get("entry_date_5") or "—")} → '
                f'{esc(hit.get("exit_date_5") or "未完成")}</td>'
                f'<td>{"；".join(esc(t) for t in tags) or "重复／连续入选"}</td></tr>'
            )
        available = {r['signal_date'] for r in row_hits}
        recorded = set(filter(None, stock.get('hit_dates', '').split(';')))
        missing = sorted(recorded - available)
        if missing:
            hit_rows.extend(f'<tr><td>{esc(d)}</td><td colspan="6" class="pending">'
                            '该日已入选，但缺少可评估行情；未补零。</td></tr>' for d in missing)
        detail = (
            f'<tr id="detail-{esc(symbol)}" class="detail-row" hidden><td colspan="9">'
            '<div class="stock-detail"><div class="detail-heading"><h3>'
            f'{esc(stock["name"])} · {esc(symbol)}</h3><span>{esc(stock["industry"])}</span></div>'
            f'<p class="muted">窗口初见：{esc(stock["first_in_window"])}；'
            f'本地保存历史首次命中：{esc(stock.get("first_saved_hit") or "—")}；'
            f'窗口前最近命中：{esc(stock.get("prior_last_hit") or "无记录")}。</p>'
            '<h4>全部 MRGC 入选日与后续表现</h4><p class="micro">'
            '各行从对应信号日的下一交易日开盘买入，持有 1／3／5／10 个完整交易日后开盘卖出，'
            '扣除往返0.4个百分点成本；表格行之间可能重叠，不能直接当作独立交易加总。'
            '鼠标停留在已满期收益上可查看实际入场和退出日。</p>'
            '<div class="table-scroll"><table class="inner"><thead><tr><th>MRGC 信号日</th>'
            '<th>1日净收益</th><th>3日净收益</th><th>5日净收益</th><th>10日净收益</th>'
            '<th>5日入场 → 退出</th><th>入选时机</th></tr></thead><tbody>'
            + ''.join(hit_rows) + '</tbody></table></div><h4>C／缠论买卖点时间线</h4>'
            '<p class="micro">以下日期来自保存的信号事实。缠论按确认／首次可见日计，'
            '历史拐点日不能作为当时买点。此时间线不以最新价格代替信号后收益；'
            '信号后收益统一按信号次日开盘买入、持有h日后开盘卖出的假设做多净收益；'
            '卖点／风险事件的这一列仅用于观察后续方向，不是卖出交易收益，也不是做空收益。</p>'
            + signal_table(signal_map[symbol], outcome_map) + '</div></td></tr>'
        )
        ret = number(stock.get('first_net_return_5'))
        bodies.append(
            f'<tbody class="stock-group" data-symbol="{esc(symbol)}" '
            f'data-search="{esc(symbol+" "+stock["name"]+" "+stock["industry"])}" '
            f'data-industry="{esc(stock["industry"])}" '
            f'data-first="{esc(stock["first_in_window"])}" '
            f'data-count="{esc(stock["hit_count"])}" '
            f'data-return="{ret if ret is not None else ""}">' + header + detail + '</tbody>'
        )
    return ('<div class="table-scroll"><table id="stocks"><thead><tr><th></th><th>股票</th>'
            '<th>行业大类</th><th>窗口首次入选</th><th>次数</th><th>首次 1日</th>'
            '<th>首次 3日</th><th>首次 5日</th><th>首次 10日</th></tr></thead>'
            + ''.join(bodies) + '</table></div>')


def industry_section(rows: list[dict], daily: list[dict]) -> str:
    dates = sorted({r['trade_date'] for r in rows})
    by_label = defaultdict(dict)
    totals = Counter()
    for row in rows:
        by_label[row['industry']][row['trade_date']] = row
        totals[row['industry']] += int(row['selected_count'])
    labels = [label for label, count in totals.most_common() if count]
    heading = ''.join(f'<th title="{esc(d)}">{esc(d[5:])}</th>' for d in dates)
    body = []
    for label in labels:
        cells = []
        for day in dates:
            row = by_label[label].get(day, {})
            count = int(row.get('selected_count', 0))
            share = float(row.get('share_pct', 0))
            alpha = min(0.88, share / 40 * 0.88)
            title = f'{day} {label}：{count}只，占{share:.1f}%；新增{row.get("entered_vs_previous_day",0)}，退出{row.get("exited_vs_previous_day",0)}'
            cells.append(f'<td class="heat" style="background:rgba(37,102,108,{alpha:.3f});'
                         f'color:{"#fff" if alpha > .47 else "#183b40"}" title="{esc(title)}">'
                         f'{count if count else "·"}</td>')
        body.append(f'<tr><th>{esc(label)}</th>' + ''.join(cells)
                    + f'<td class="number">{totals[label]}</td></tr>')
    daily_rows = ''.join('<tr>' + ''.join(f'<td>{esc(row[key])}</td>' for key in
                         ('date', 'count', 'added_vs_previous', 'removed_vs_previous',
                          'new5', 'first20', 'industries')) + '</tr>' for row in daily)
    return ('<p class="muted">单元格是当日入选股票数，颜色深浅是占当日全部入选的比例；'
            '悬停查看份额及进出。行业使用本地固定大类（东财来源），不是资金流向指标。</p>'
            '<div class="table-scroll"><table class="heatmap"><thead><tr><th>行业</th>'
            + heading + '<th>股票日</th></tr></thead><tbody>' + ''.join(body)
            + '</tbody></table></div><details class="panel-details"><summary>展开20日候选变化明细</summary>'
            '<div class="table-scroll"><table><thead><tr><th>日期</th><th>入选</th><th>较前日新增</th>'
            '<th>较前日退出</th><th>5日未入选后出现</th><th>20日未入选后出现</th><th>行业数</th>'
            '</tr></thead><tbody>' + daily_rows + '</tbody></table></div></details>')


def metric_rows(metrics: list[dict]) -> str:
    output = []
    for item in metrics:
        title, description = RULES.get(item['rule'], (item['rule'], ''))
        recent = item['period'] == 'recent'
        sample = item.get('n', 0)
        states = '；'.join(f'{status_label(k)} {v}' for k, v in item.get('statuses', {}).items())
        fields = [f'<strong title="{esc(description)}">{esc(title)}</strong>',
                  '近20日' if recent else '历史对照',
                  '风险退出 / 最长5日' if item['managed'] else f'{item["horizon"]}日固定',
                  str(sample), str(item.get('unique_stocks', 0)),
                  percent(item.get('win_rate'), False) if sample else '无覆盖／无成熟样本',
                  interval_text(item.get('win_ci')), percent(item.get('mean')),
                  percent(item.get('median')), scalar(item.get('profit_factor')),
                  percent(item.get('worst')), percent(item.get('mean_excess')),
                  interval_text(item.get('mean_ci_cluster')),
                  percent(item.get('fee02_mean')), percent(item.get('fee06_mean')),
                  f'<span class="micro">{esc(states)}</span>']
        output.append(f'<tr class="{"recent" if recent else "historical"}">'
                      + ''.join(f'<td>{v}</td>' for v in fields) + '</tr>')
    return ''.join(output)


def metrics_table(metrics: list[dict]) -> str:
    return ('<div class="table-scroll"><table class="metrics"><thead><tr>'
            '<th>规则</th><th>区间</th><th>退出</th><th>成熟笔数</th><th>不同股票</th>'
            '<th>胜率</th><th>胜率95%区间</th><th>平均净收益</th><th>中位净收益</th>'
            '<th>盈利因子</th><th>最差单笔</th><th>平均基准超额</th><th>平均收益聚类区间</th>'
            '<th>成本0.2pp平均</th><th>成本0.6pp平均</th>'
            '<th>完成／跳过情况</th></tr></thead><tbody>' + metric_rows(metrics)
            + '</tbody></table></div>')


def strategies_section(metrics: list[dict], portfolios: dict) -> str:
    main = [m for m in metrics if m['horizon'] == 5 and not m['managed']]
    rest = [m for m in metrics if m not in main]
    result = ('<p class="muted">全部规则完整展示。主口径为5日固定持有；3／10日与风险退出为敏感性。'
              '不同规则不能拼成一条净值线。胜率分母只计完成交易，同股持仓不重复开仓。</p>'
              '<div class="notice">历史对照是同一套当前本地数据的重放，不能当作严格样本外验证。'
              'C／缠论在较早历史对照区间缺乏信号覆盖，显示“无样本”并不代表该策略当时无机会。</div>'
              + metrics_table(main)
              + '<details class="panel-details"><summary>展开全部3日／10日／风险退出结果</summary>'
              + metrics_table(rest) + '</details>')
    portfolio_rows = []
    for key, values in portfolios.items():
        rule, period, horizon, mode = key.split(':', 3)
        title = RULES.get(rule, (rule, ''))[0]
        unavailable = values.get('status') == 'DATA_INCOMPLETE' or values.get('data_status') == 'DATA_INCOMPLETE'
        cells = [esc(title), '近20日' if period == 'recent' else '历史对照',
                 '风险退出' if mode == 'managed' else f'{horizon}日固定']
        if unavailable:
            cells += ['<strong class="pending">不可用 · DATA_INCOMPLETE</strong>', '—', '—', '—',
                      esc(values.get('reason', '数据不完整'))]
        else:
            cells += [percent(values.get('return_nav')), percent(values.get('max_drawdown')),
                      esc(values.get('entries', '—')), esc(values.get('open_positions', '—')),
                      '末日未平仓按收盘估值']
        portfolio_rows.append('<tr>' + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr>')
    return (result + '<details class="panel-details"><summary>展开5槽位组合模拟及回撤</summary>'
            '<p class="micro">最多5只、每次最多投入当时总资产20%，按信号日RPS250降序、代码升序。'
            '现金槽位有限，组合和逐笔均值不是同一口径；不模拟整手限制和最低佣金。'
            '数据不完整的组合不展示收益或回撤。</p><div class="table-scroll"><table><thead><tr>'
            '<th>规则</th><th>区间</th><th>退出</th><th>组合收益</th><th>最大回撤</th>'
            '<th>开仓次数</th><th>末日持仓</th><th>状态</th></tr></thead><tbody>'
            + ''.join(portfolio_rows) + '</tbody></table></div></details>')


def coverage_section(evidence: dict, audit: dict) -> str:
    rows = []
    for row in evidence.get('coverage', []):
        fields = [row['date'], row.get('research_members', 0), row.get('c_ok', 0),
                  row.get('chan_ready', 0), row.get('archive_covered', 0)]
        rows.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in fields) + '</tr>')
    summary = evidence.get('summary', {})
    text = (f'当前池内保存信号 {summary.get("current_signals", "—")} 条；'
            f'开盘前存档信号 {summary.get("archived_signals", "—")} 条；'
            f'有及时存档的日期 {summary.get("dates_with_timely_archive", "—")} 天。')
    resources = audit.get('resources', {})
    return ('<p>' + esc(text) + '</p><div class="table-scroll"><table><thead><tr><th>日期</th>'
            '<th>研究股票在当日20日池内</th><th>C 可用</th><th>缠论可用</th><th>及时存档覆盖</th>'
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
            '<p class="micro">当前保存记录可能经过历史补算，时间戳也有无时区旧值；'
            '仅有下一交易日开盘前存档的证据更强，但仍非逐笔真实交易。ZIG含未来函数并可回绘，'
            '以首次可见确认日记录，不能以历史拐点日回买。</p>'
            f'<p class="micro">行情读取 {esc(audit.get("price_rows", "—"))} 行；'
            f'未复权收盘缺失 {esc(audit.get("raw_missing", "—"))} 行（全研究输入范围）；'
            f'计算耗时 {esc(resources.get("elapsed_seconds", "—"))} 秒，'
            f'峰值RSS {esc(resources.get("peak_rss_mb", "—"))} MB。</p>')


CSS = r'''
:root{--ink:#172c33;--muted:#63757c;--line:#dce4e2;--paper:#f4f5ef;--white:#fff;--teal:#21666c;--gold:#aa782f;--loss:#355d88;--gain:#a74835}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft Yahei",sans-serif;font-size:14px;line-height:1.65}a{color:var(--teal)}header{background:#183d43;color:#fff;padding:44px max(24px,calc((100vw - 1360px)/2)) 38px}.eyebrow{font-size:11px;letter-spacing:3px;color:#adc7c9;text-transform:uppercase}h1{font-size:clamp(27px,4vw,42px);line-height:1.25;font-weight:650;letter-spacing:-1px;margin:14px 0 13px}.subtitle{color:#d3e1df;max-width:830px;margin:0}nav{display:flex;gap:24px;flex-wrap:wrap;padding-top:24px}nav a{color:#dce9e5;text-decoration:none;font-size:13px;border-bottom:1px solid #63858a;padding-bottom:3px}main{max-width:1410px;margin:auto;padding:28px 25px 72px}section{scroll-margin-top:16px;margin:28px 0 40px}.section-head{display:flex;align-items:baseline;justify-content:space-between;gap:20px}h2{font-size:22px;letter-spacing:-.5px;margin:0 0 12px}h3{font-size:18px;margin:0}h4{margin:24px 0 8px;font-size:15px}p{margin:10px 0 16px}.muted,.micro{color:var(--muted)}.micro{font-size:12px;line-height:1.7}.number{font-variant-numeric:tabular-nums}.gain{color:var(--gain)}.loss{color:var(--loss)}.pending{color:#8a6c3b;font-size:12px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:var(--white);border:1px solid var(--line);border-radius:8px;padding:18px 20px}.card-label{font-size:12px;color:var(--muted)}.card-value{font-size:29px;font-weight:650;letter-spacing:-1px;margin-top:4px}.card small{color:var(--muted)}.conclusion{background:#e8eee8;border-left:4px solid var(--teal);padding:18px 22px;border-radius:4px;margin:0 0 20px}.conclusion strong{font-size:19px}.conclusion p{margin:7px 0 0}.notice{background:#f4ecd9;color:#725525;border-left:3px solid #c29d58;padding:12px 16px;font-size:13px;margin:16px 0}.controls{display:flex;gap:12px;flex-wrap:wrap;background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px;margin:16px 0}input,select,button{font:inherit}input,select{border:1px solid #cbd6d3;border-radius:5px;background:#fff;color:var(--ink);padding:10px 12px;min-height:42px}input{min-width:250px;flex:1}button{cursor:pointer}.reset{background:#eef2ef;color:var(--teal);border:1px solid var(--line);border-radius:5px;padding:8px 14px}.controls label{display:flex;gap:7px;align-items:center;font-size:12px;color:var(--muted)}.table-scroll{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:7px}table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}th{font-weight:550;text-align:left;background:#edf2ef;color:#466168}th,td{padding:12px 13px;border-bottom:1px solid #e7ece8;vertical-align:top}tr:last-child td{border-bottom:0}tbody.stock-group .stock-row:hover{background:#f2f7f4}.stock-group[hidden]{display:none}.stock-name{min-width:115px}.stock-name strong{font-size:14px}.stock-name small,td small{display:block;color:var(--muted);font-size:11px}.expand{border:1px solid #bacdc9;width:28px;height:28px;border-radius:50%;color:var(--teal);background:#f3f7f4;line-height:24px;padding:0}.expand[aria-expanded=true]{background:var(--teal);color:white}.detail-row>td{padding:0;white-space:normal}.stock-detail{background:#f6f8f4;border-top:1px solid #c4d6cd;border-bottom:1px solid #c4d6cd;padding:24px}.detail-heading{display:flex;align-items:center;gap:16px}.detail-heading>span{font-size:12px;color:var(--teal);padding:3px 10px;background:#e3eeea;border-radius:20px}.inner{font-size:12px}.inner .wrap{min-width:260px;max-width:480px;white-space:normal}.date{font-variant-numeric:tabular-nums}.badge{font-size:11px}.verified{color:var(--teal)}.heatmap th,.heatmap td{text-align:center;padding:10px 8px;min-width:47px;font-variant-numeric:tabular-nums}.heatmap th:first-child{text-align:left;position:sticky;left:0;min-width:98px;background:#e9efea}.heatmap .heat{border:2px solid #fff}.panel-details{border:1px solid var(--line);border-radius:7px;margin-top:16px;background:#fff;padding:0 16px 16px}.panel-details summary{cursor:pointer;padding:16px 0 0;font-weight:550}.panel-details[open] summary{padding-bottom:16px}.metrics td:first-child{min-width:190px}.metrics .historical{color:#5f737b;background:#f8faf7}.metrics td:last-child{white-space:normal;min-width:170px}.methods{display:grid;grid-template-columns:1fr 1fr;gap:16px}.methods article{padding:18px 22px;background:#fff;border:1px solid var(--line);border-radius:7px}.methods h3{font-size:15px}.methods p{font-size:13px;margin-bottom:0}.files{display:flex;flex-wrap:wrap;gap:16px;font-size:12px}footer{color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:18px}.empty{padding:32px;text-align:center;color:var(--muted)}:focus-visible{outline:3px solid #93b7ac;outline-offset:3px}@media(max-width:800px){main{padding:20px 14px}.cards{grid-template-columns:1fr 1fr}.card{padding:14px}.card-value{font-size:24px}.methods{grid-template-columns:1fr}header{padding:32px 22px}.stock-detail{padding:16px}th,td{padding:10px}.controls label{flex:1}.controls select{max-width:100%}.section-head{display:block}}@media print{body{background:white}header{background:white;color:var(--ink);padding:20px 0}.subtitle,.eyebrow{color:var(--muted)}nav,.controls{display:none}main{padding:0}.table-scroll{overflow:visible}details:not([open]){display:none}section{break-inside:avoid}.stock-group{break-inside:avoid}.cards{grid-template-columns:repeat(4,1fr)}}
'''


JS = r'''
(()=>{'use strict';
const search=document.getElementById('search'),industry=document.getElementById('industry'),sort=document.getElementById('sort');
const table=document.getElementById('stocks'),groups=[...table.querySelectorAll('.stock-group')];
function update(){const q=search.value.trim().toLowerCase(),label=industry.value;let count=0;
for(const group of groups){const visible=(!q||group.dataset.search.toLowerCase().includes(q))&&(!label||group.dataset.industry===label);group.hidden=!visible;if(visible)count++;}
document.getElementById('stock-count').textContent=`显示 ${count} / ${groups.length} 只`;
document.getElementById('empty').hidden=count!==0;
const [key,dir]=sort.value.split(':'),sign=dir==='desc'?-1:1;
groups.sort((a,b)=>{let x=a.dataset[key],y=b.dataset[key];if(key==='count'||key==='return'){if(x===''&&y==='')return a.dataset.symbol.localeCompare(b.dataset.symbol);if(x==='')return 1;if(y==='')return -1;x=Number(x);y=Number(y);return sign*(x-y)||a.dataset.symbol.localeCompare(b.dataset.symbol);}return sign*x.localeCompare(y)||a.dataset.symbol.localeCompare(b.dataset.symbol);});
const fragment=document.createDocumentFragment();groups.forEach(g=>fragment.appendChild(g));table.appendChild(fragment);}
search.addEventListener('input',update);industry.addEventListener('change',update);sort.addEventListener('change',update);
document.getElementById('reset').addEventListener('click',()=>{search.value='';industry.value='';sort.value='first:asc';update();});
table.addEventListener('click',event=>{const button=event.target.closest('.expand');if(!button)return;const open=button.getAttribute('aria-expanded')==='true';button.setAttribute('aria-expanded',String(!open));button.textContent=open?'＋':'−';document.getElementById(button.getAttribute('aria-controls')).hidden=open;});
update();
})();
'''


def main() -> None:
    stocks = csv_rows('stock_review.csv')
    hits = csv_rows('hit_outcomes.csv')
    daily = csv_rows('daily_review.csv')
    industries = csv_rows('industry_daily.csv')
    metrics = read_json('strategy_metrics.json')
    portfolios = read_json('portfolios.json')
    evidence = read_json('signal_evidence.json')
    signal_outcomes = csv_rows('signal_outcomes.csv') if (HERE / 'signal_outcomes.csv').exists() else []
    audit = read_json('audit.json') if (HERE / 'audit.json').exists() else {}
    window = evidence['window']
    labels = sorted({s['industry'] for s in stocks})
    options = '<option value="">全部行业</option>' + ''.join(
        f'<option value="{esc(label)}">{esc(label)}</option>' for label in labels)
    baseline = next((m for m in metrics if m['rule'] == 'daily' and m['period'] == 'recent'
                     and m['horizon'] == 5 and not m['managed']), {})
    raw_hits = sum(int(row['selected_count']) for row in industries)
    cards = [
        ('研究股票', str(len(stocks)), f'{len(labels)}个行业大类'),
        ('MRGC 入选股票日', str(raw_hits), f'{len(hits)}行后续收益记录'),
        ('基线5日胜率', percent(baseline.get('win_rate'), False), f'{baseline.get("n", 0)}笔成熟、去重交易'),
        ('基线5日平均净收益', percent(baseline.get('mean')), '往返成本0.4个百分点'),
    ]
    card_html = ''.join(f'<div class="card"><div class="card-label">{esc(label)}</div>'
                        f'<div class="card-value number">{value}</div><small>{esc(note)}</small></div>'
                        for label, value, note in cards)
    rules = ''.join(f'<p><strong>{esc(name)}</strong>：{esc(description)}</p>'
                    for name, description in RULES.values())
    content = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>MRGC 近20日 · 策略研究明细</title><style>{CSS}</style></head>
<body><header><div class="eyebrow">EVOALPHA · RESEARCH NOTE / 20 SESSIONS</div>
<h1>MRGC 近20日<br>从入选时机到后续表现</h1><p class="subtitle">{esc(window['start'])} — {esc(window['end'])} · 本地历史重放 · 个股、行业与买卖点逐项核对</p>
<nav aria-label="报告导航"><a href="#findings">研究结论</a><a href="#stock-section">{len(stocks)}只股票明细</a><a href="#industry-section">行业变化</a><a href="#strategy-section">全部规则结果</a><a href="#evidence-section">数据与证据</a></nav></header>
<main><section id="findings"><div class="conclusion"><strong>本轮未发现稳定的短期高胜率策略。</strong>
<p>入选 MRGC 与适合买入之间仍有明显距离。以下完整展示可评估结果与缺口，不自动生成买入建议。</p></div>
<div class="cards">{card_html}</div><p class="micro">主收益口径：信号日收盘后识别 → 下一交易日开盘买入 → 持有 h 个完整交易日后开盘卖出；5日对应 t+1 开盘到 t+6 开盘。末端未满期单独标明，不补成0收益。股票日可重复，策略交易已控制同股重叠。</p></section>
<section id="stock-section"><div class="section-head"><h2>逐股复盘</h2><span id="stock-count" class="muted">{len(stocks)}只</span></div>
<p class="muted">主表展示“本窗口首次 MRGC 入选”后的净收益。点左侧 ＋，展开全部入选日及 C／缠论买卖点时间线。</p>
<div class="controls"><input id="search" type="search" aria-label="搜索股票代码、名称或行业" placeholder="搜索股票代码、名称或行业，例如迎驾贡酒">
<label>行业<select id="industry" aria-label="筛选行业">{options}</select></label>
<label>排序<select id="sort" aria-label="排序股票"><option value="first:asc">首次入选 · 从早到晚</option><option value="first:desc">首次入选 · 从晚到早</option><option value="return:desc">首次5日收益 · 从高到低</option><option value="return:asc">首次5日收益 · 从低到高</option><option value="count:desc">入选次数 · 从多到少</option></select></label><button class="reset" id="reset">重置</button></div>
{stock_table(stocks,hits,evidence,signal_outcomes)}<p id="empty" class="empty" hidden>没有匹配的股票，请更换搜索词或行业。</p></section>
<section id="industry-section"><h2>20日行业构成与变化</h2>{industry_section(industries,daily)}</section>
<section id="strategy-section"><h2>全部候选规则与风险结果</h2>{strategies_section(metrics,portfolios)}
<details class="panel-details"><summary>展开规则定义</summary>{rules}</details></section>
<section id="evidence-section"><h2>先看证据，再看胜率</h2><div class="methods">
<article><h3>历史对照的边界</h3><p>历史对照 {esc(audit.get('history_start','—'))}—{esc(audit.get('history_end','—'))}；近期窗口 {esc(window['start'])}—{esc(window['end'])}。使用各历史日保存候选，未只用近期135只回看。但价格、MRGC和信号可能被修订，且规则在同一研究中提出，因此不是严格样本外。</p></article>
<article><h3>交易可执行性与费用</h3><p>次日缺行情不追补买入，近涨停开盘跳过；退出缺行情或近跌停顺延，超出数据末端标未完成。往返0.4个百分点为总成本情景，另测0.2／0.6个百分点。日线不能验证排队、滑点及盘中路径。</p></article>
<article><h3>风险退出口径</h3><p>最长5日；期间收盘跌破入场价 − 1.5×信号日ATR14，或出现 C 退出／风险事件，则下一交易日开盘退出。它不是盘中精确止损，跳空可能使损失超过阈值。</p></article>
<article><h3>行业与信号覆盖</h3><p>行业按本地固定大类，08-26以前分类属于后见快照。C／缠论的较早历史覆盖不足，未保存信号不能填成“无买点”。当前重算与及时存档版本分别比较，缠论历史拐点不能当作实时信号日。</p></article></div>
<details class="panel-details"><summary>展开每日信号覆盖、存档证据和资源记录</summary>{coverage_section(evidence,audit)}</details></section>
<footer><p>离线研究明细 · 无外部脚本、字体、图表库或网络请求 · 所有收益来自本目录的已生成研究文件。</p>
<div class="files"><a href="stock_review.csv">逐股CSV</a><a href="hit_outcomes.csv">入选日收益CSV</a><a href="industry_daily.csv">行业日度CSV</a><a href="strategy_metrics.json">策略指标JSON</a><a href="signal_evidence.json">信号证据JSON</a><a href="protocol.md">研究口径</a></div></footer></main>
<script>{JS}</script></body></html>'''
    target = HERE / 'report.html'
    target.write_text(content)
    print(json.dumps(dict(output=str(target), stocks=len(stocks), hit_rows=len(hits),
                          metrics=len(metrics), bytes=target.stat().st_size), ensure_ascii=False))


if __name__ == '__main__':
    main()
