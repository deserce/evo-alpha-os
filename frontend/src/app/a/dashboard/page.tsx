'use client';

import { TopbarTradeDate } from '@/components/layout/TopbarTradeDate';
import Link from 'next/link';
import type { CSSProperties, ReactNode } from 'react';
import { useState } from 'react';
import dynamic from 'next/dynamic';
import type { DailyHintsContext } from '@/components/stock/DailyHintsPanel';
import { useQuery } from '@tanstack/react-query';
import {
  Bot,
  ChevronRight,
  FileText,
  Layers3,
  ShieldAlert,
  Sparkles,
  Target,
  Zap,
} from 'lucide-react';
import { portfolioApi, quantApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';

type Row = Record<string, unknown>;
type Tone = 'green' | 'red' | 'blue' | 'amber' | 'purple' | 'muted';
const WatchlistHints = dynamic(() =>
  import('@/components/stock/StockWorkspace').then((module) => module.AllWatchlistDailyHints),
  { ssr: false },
);

const strategies = [
  ['mrgc_v1', '陶博士 MRGC'],
  ['rps_acceleration_v1', 'RPS50 加速启动'],
  ['rps_platform_breakout_v1', '20 日启动点突破'],
  ['rps_long_pullback_v1', '年度主线回踩'],
  ['rps_resonance_v1', '多周期 RPS 共振'],
  ['monthly_reversal_v65', '月线反转 6.5'],
  ['near_year_high_v1', '接近一年新高'],
] as const;

const riskCategory = { executable: '触发时可执行', watch: '观察', blocked: '待校验' };
const eventStatus = { OPEN: '待处理', ACKNOWLEDGED: '已确认', RESOLVED: '已解决', ALL: '—' };

const ignoreSymbol = () => {};

const toneClass: Record<Tone, string> = {
  green: 'is-green',
  red: 'is-red',
  blue: 'is-blue',
  amber: 'is-amber',
  purple: 'is-purple',
  muted: 'is-muted',
};

function rows<T = Row>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function firstValue(row: Row, keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (value !== null && value !== undefined && value !== '') return value;
  }
  return undefined;
}

function text(value: unknown, fallback = '-') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

function firstText(row: Row, keys: string[], fallback = '-') {
  return text(firstValue(row, keys), fallback);
}

function toNumber(value: unknown, fallback = 0) {
  const parsed = value === null || value === undefined || value === '' ? Number.NaN : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function int(value: unknown) {
  const parsed = toNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) return '-';
  return Math.round(parsed).toLocaleString('zh-CN');
}

function num(value: unknown, digits = 1) {
  const parsed = toNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) return '-';
  return parsed.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function pct(value: unknown, digits = 1) {
  const parsed = toNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) return '-';
  const normalized = Math.abs(parsed) <= 1 ? parsed * 100 : parsed;
  return `${normalized >= 0 ? '+' : ''}${normalized.toFixed(digits)}%`;
}

function clamp(value: number, min = 0, max = 100) {
  return Math.max(min, Math.min(max, value));
}

function suggestedPosition(score: number) {
  if (score >= 82) return '60% - 90%';
  if (score >= 72) return '50% - 70%';
  if (score >= 60) return '40% - 60%';
  if (score >= 45) return '20% - 40%';
  return '0% - 20%';
}

function stateTone(value: unknown): Tone {
  const state = text(value, '');
  if (['M3', 'M4', 'I3', 'I4', 'S4', 'S5', 'P0', 'P1'].includes(state)) return 'green';
  if (['M2', 'I2', 'S3', 'P2'].includes(state)) return 'amber';
  if (['M0', 'M1', 'I0', 'I1', 'S0', 'S1', 'P3'].includes(state)) return 'red';
  return 'blue';
}

function countState(counts: Row[], field: string, state: string) {
  return toNumber(counts.find((row) => row[field] === state)?.count);
}

function queryMessage(query: { isError: boolean; isPending: boolean }, empty: string) {
  if (query.isError) return '加载失败，请刷新重试。';
  if (query.isPending) return '正在加载…';
  return empty;
}

function StatusPill({ children, tone = 'blue' }: { children: ReactNode; tone?: Tone }) {
  return <span className={`dashboard-pill ${toneClass[tone]}`}>{children}</span>;
}

function ScoreRing({ value, label }: { value: number; label: string }) {
  return (
    <div
      className="dashboard-score-ring is-green"
      style={{ '--dashboard-score': `${clamp(value) * 3.6}deg` } as CSSProperties}
    >
      <div>
        <strong>{num(value, 0)}</strong>
        <span>{label}</span>
      </div>
    </div>
  );
}

function Panel({
  title,
  subtitle,
  icon,
  span,
  id,
  extra,
  children,
}: {
  title: string;
  subtitle?: string;
  icon: ReactNode;
  span: 3 | 4 | 5;
  id?: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} className={`dashboard-panel dashboard-span-${span}`}>
      <header className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>{icon}</span>
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
        </div>
        {extra}
      </header>
      <div className="dashboard-panel-body">{children}</div>
    </section>
  );
}

function MiniTable({
  columns,
  data,
  limit = 6,
  empty = '暂无数据',
  onRowClick,
}: {
  columns: Array<{ key: string; label: string; align?: 'left' | 'right'; render?: (row: Row, index: number) => ReactNode }>;
  data: Row[];
  limit?: number;
  empty?: string;
  onRowClick?: (row: Row, index: number) => void;
}) {
  const visibleRows = data.slice(0, limit);
  return (
    <div className="dashboard-table-wrap">
      <table className="dashboard-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.align === 'right' ? 'is-right' : ''}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visibleRows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="dashboard-empty">{empty}</td>
            </tr>
          ) : visibleRows.map((row, index) => (
            <tr
              key={`${firstText(row, ['symbol', 'sector_name', 'theme_name', 'name'], 'row')}-${index}`}
              onClick={onRowClick ? () => onRowClick(row, index) : undefined}
              className={onRowClick ? 'cursor-pointer' : ''}
            >
              {columns.map((column) => (
                <td key={column.key} className={column.align === 'right' ? 'is-right' : ''}>
                  {column.render ? column.render(row, index) : text(row[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DashboardPage() {
  const { globalDate, setGlobalDate, setStockSidebarUi } = useGlobalStore();
  const [strategy, setStrategy] = useState<string>('mrgc_v1');
  const [hints, setHints] = useState<DailyHintsContext>();
  const [showFullBrief, setShowFullBrief] = useState(false);

  const datesQuery = useQuery({
    queryKey: ['dashboardDates'],
    queryFn: () => quantApi.getDates(160),
  });
  const dates = rows<string>(datesQuery.data?.dates);
  const selectedDate = dates.includes(globalDate) ? globalDate : (dates.includes(datesQuery.data?.latest_quant) ? datesQuery.data.latest_quant : datesQuery.data?.latest) || '';
  const dateOptions = dates.length ? dates : (selectedDate ? [selectedDate] : []);

  const dashboardQuery = useQuery({
    queryKey: ['dashboardSummary', selectedDate],
    queryFn: () => quantApi.getDashboard(selectedDate, true),
    enabled: Boolean(selectedDate),
  });
  const industryQuery = useQuery({
    queryKey: ['dashboardIndustryStrength', selectedDate],
    queryFn: () => quantApi.getIndustryStrength(selectedDate, 8, undefined, 'desc', true),
    enabled: Boolean(selectedDate),
  });
  const strategyQuery = useQuery({
    queryKey: ['dashboardStrategy', selectedDate, strategy],
    queryFn: () => quantApi.getStrategyResults({ tradeDate: selectedDate, strategyName: strategy, limit: 12 }),
    enabled: Boolean(selectedDate),
  });
  const portfolioQuery = useQuery({
    queryKey: ['portfolio', 'dashboard-risk', selectedDate],
    queryFn: () => portfolioApi.getDashboardRisk(selectedDate),
    enabled: Boolean(selectedDate),
  });
  const researchQuery = useQuery({
    queryKey: ['dashboardResearchSnapshot', selectedDate],
    queryFn: () => quantApi.getResearchSnapshot(selectedDate, 7, true),
    enabled: Boolean(selectedDate),
  });
  const identity = useQuery({
    queryKey: ['navigation-identity'],
    queryFn: async () => {
      const response = await fetch('/api/v1/users/me', { cache: 'no-store' });
      return response.ok ? await response.json() as { role: string } : null;
    },
    retry: false,
  });
  const canViewBrief = identity.data?.role === 'admin';
  const briefQuery = useQuery({
    queryKey: ['dashboardBrief', selectedDate],
    queryFn: () => quantApi.getAiBrief(selectedDate, true),
    enabled: canViewBrief && Boolean(selectedDate),
  });

  const dashboard = dashboardQuery.data ?? {};
  const market = dashboard.market ?? {};
  const marketState = text(market.market_state);
  const marketScore = toNumber(market.market_strength_score, Number.NaN);
  const rankedIndustries = rows<Row>(industryQuery.data?.rows);
  const industryCounts = rows<Row>(dashboard.industry_counts);
  const aiLines = rows<string>(briefQuery.data?.bullets);
  const i4Count = countState(industryCounts, 'industry_state', 'I4');
  const i3Count = countState(industryCounts, 'industry_state', 'I3');
  const themeRows = rows<Row>(researchQuery.data?.themes);
  const currentHints = hints?.date === selectedDate ? hints : undefined;
  const buyHints = currentHints?.items.filter((item) => item.direction === 'buy') ?? [];
  const sellHints = currentHints?.items.filter((item) => item.direction === 'sell') ?? [];
  const hintsPending = !currentHints || currentHints.pending;
  const hintsCount = (count: number) => hintsPending || (!count && currentHints?.availability)
    ? '—' : count;
  const strategyResult = strategyQuery.data;
  const strategyReady = strategyResult?.calculation?.calculated;
  const strategyRows = strategyReady ? rows<Row>(strategyResult.rows) : [];
  const strategyTotal = strategyResult?.calculation?.status === 'legacy_result' && strategyRows.length === 12
    ? '至少 12' : int(strategyResult?.calculation?.signal_count);
  const portfolio = portfolioQuery.data;
  const riskTotal = portfolio ? Object.values(portfolio.counts).reduce((sum, count) => sum + count, 0) : 0;
  const portfolioStatus = queryMessage(portfolioQuery, !portfolio?.snapshot_ready
    ? '持仓快照缺失' : riskTotal ? `${riskTotal} 条提醒` : '当日无提醒');
  const openHints = (direction: 'buy' | 'sell') => {
    setGlobalDate(selectedDate);
    setStockSidebarUi({ tab: 'hints', hintsFilter: direction, assistantLink: null, infoHidden: false });
  };

  return (
    <main className="dashboard-page quant-overview">
      {selectedDate && <WatchlistHints key={selectedDate} date={selectedDate}
        onSelectSymbol={ignoreSymbol} onContext={setHints} summaryOnly />}
      <div className="dashboard-grid">
        <section className="evo-page-header dashboard-span-12 flex flex-wrap items-center justify-between gap-2">
          <h1 className="evo-page-title"><span className="evo-page-index">01</span> 今日作战中枢</h1>
          <div className="dashboard-header-actions">
            <TopbarTradeDate value={selectedDate} dates={dateOptions} onChange={setGlobalDate} />
            <span className="dashboard-sync !h-8 max-w-full !whitespace-normal">
              <span className="status-dot bg-success text-success" />
              {selectedDate ? `查看 ${selectedDate} · 最新行情 ${datesQuery.data?.latest ?? '—'}` : queryMessage(datesQuery, '暂无交易日')}
            </span>
          </div>
        </section>

        <Link href="/a/market" className="dashboard-status-card dashboard-span-3">
          <div className="dashboard-status-head">
            <span>市场状态</span>
            <StatusPill tone={stateTone(marketState)}>{marketState}</StatusPill>
          </div>
          <div className="dashboard-status-main">
            <strong>{Number.isFinite(marketScore) ? `${num(marketScore, 0)} / 100` : queryMessage(dashboardQuery, '尚未生成')}</strong>
            {Number.isFinite(marketScore) && <p>建议仓位 {suggestedPosition(marketScore)}</p>}
          </div>
          <div className="dashboard-status-foot">
            数据库口径 {text(market.index_name, '市场强度')}
            <ChevronRight size={14} />
          </div>
        </Link>

        <Link href="/a/sector" className="dashboard-status-card dashboard-span-3">
          <div className="dashboard-status-head">
            <span>强行业概览</span>
            <StatusPill tone="green">I3 / I4</StatusPill>
          </div>
          <div className="dashboard-two-metric">
            <strong>{industryCounts.length ? i4Count : '—'}<span>I4 核心主线</span></strong>
            <strong>{industryCounts.length ? i3Count : '—'}<span>I3 强势行业</span></strong>
          </div>
          <div className="dashboard-top-list">
            {rankedIndustries.slice(0, 3).map((row, index) => (
              <span key={`${firstText(row, ['sector_name'])}-${index}`}>
                {firstText(row, ['sector_name'])}
                <em>{text(row.industry_state, 'I-')}</em>
              </span>
            ))}
          </div>
        </Link>

        <Link href="/a/watchlist" onClick={() => openHints('sell')} className="dashboard-status-card dashboard-span-3">
          <div className="dashboard-status-head">
            <span>当日卖出关注</span>
            <StatusPill tone="amber">自选股</StatusPill>
          </div>
          <div className="dashboard-status-main">
            <strong>{hintsCount(sellHints.length)} <small>只</small></strong>
            {sellHints.slice(0, 3).map((item) => (
              <p key={item.symbol}>{item.name} · {item.signals.find((signal) => signal.direction === 'sell')?.label}</p>
            ))}
            {hintsPending && <p>正在读取当日提示…</p>}
            {!hintsPending && currentHints?.availability && <p>{currentHints.availability}</p>}
          </div>
          <div className="dashboard-status-foot">查看卖出关注 <ChevronRight size={14} /></div>
        </Link>

        <Link href="#portfolio-risk" className="dashboard-status-card dashboard-span-3">
          <div className="dashboard-status-head">
            <span>持仓风险</span>
            <StatusPill tone={riskTotal || !portfolio?.snapshot_ready ? 'amber' : 'green'}>{portfolioStatus}</StatusPill>
          </div>
          <div className="dashboard-risk-grid">
            <span>持仓标的 <b>{portfolio?.snapshot_ready ? portfolio.position_count : '—'}</b></span>
            <span>触发时可执行 <b>{portfolio?.snapshot_ready ? portfolio.counts.executable : '—'}</b></span>
            <span>观察 <b>{portfolio?.snapshot_ready ? portfolio.counts.watch : '—'}</b></span>
            <span>待校验 <b>{portfolio?.snapshot_ready ? portfolio.counts.blocked : '—'}</b></span>
          </div>
          <div className="dashboard-status-foot">查看当日持仓提醒 <ChevronRight size={14} /></div>
        </Link>
        <Panel id="industry-ranking" span={5} title="强行业榜" subtitle="按行业综合分排序" icon={<Layers3 size={16} />}
          extra={<Link href="/a/sector" onClick={() => setGlobalDate(selectedDate)}>查看行业</Link>}>
          <MiniTable
            data={rankedIndustries}
            limit={8}
            empty={queryMessage(industryQuery, '当日行业强度尚未生成。')}
            columns={[
              { key: 'rank', label: '排名', render: (_row, index) => index + 1 },
              { key: 'sector_name', label: '行业', render: (row) => {
                const name = firstText(row, ['sector_name'], '');
                return name ? <Link className="text-accent hover:underline" href={`/a/sector?sector=${encodeURIComponent(name)}`}>{name}</Link> : '—';
              } },
              { key: 'industry_state', label: '状态', render: (row) => <StatusPill tone={stateTone(row.industry_state)}>{text(row.industry_state, 'I-')}</StatusPill> },
              { key: 'industry_strength_score', label: '综合分（100）', align: 'right', render: (row) => num(row.industry_strength_score, 0) },
              { key: 'return_20d', label: '20日', align: 'right', render: (row) => pct(row.return_20d, 1) },
              { key: 'sector_rps_20', label: '板块 RPS20', align: 'right', render: (row) => row.sector_rps_20 == null ? '—' : num(row.sector_rps_20, 0) },
              { key: 'rps90_ratio', label: '强势股占比', align: 'right', render: (row) => `${num(toNumber(row.rps90_stock_ratio, Number.NaN) * 100, 0)}%` },
              { key: 'leader_name', label: '龙头', render: (row) => {
                const symbol = firstText(row, ['leader_symbol'], '');
                const label = firstText(row, ['leader_name', 'leader_symbol']);
                return /^[0-9]{6}$/.test(symbol)
                  ? <Link className="text-accent hover:underline" href={`/a/stock/${symbol}?returnTo=%2Fa%2Fdashboard`}>{label}</Link>
                  : label;
              } },
            ]}
          />
        </Panel>

        <Panel span={4} title="当日买入关注" subtitle={`全部自选分组 · ${hintsCount(buyHints.length)} 只`}
          icon={<Zap size={16} />} extra={<Link href="/a/watchlist" onClick={() => openHints('buy')}>查看全部</Link>}>
          <div className="dashboard-alert-list quant-buy-list">
            {buyHints.slice(0, 7).map((item) => {
              const signal = item.signals.find((entry) => entry.direction === 'buy');
              return <Link key={item.symbol} href={`/a/stock/${item.symbol}`} onClick={() => setGlobalDate(selectedDate)} className="dashboard-alert-row">
                <strong>{item.name} {item.symbol}</strong>
                <span>{signal?.label || '买入关注'}</span>
              </Link>;
            })}
            {(hintsPending || currentHints?.availability || !buyHints.length) && (
              <p className="dashboard-empty">{hintsPending ? '正在读取当日提示…' : currentHints?.availability || '当日暂无买入关注。'}</p>
            )}
          </div>
        </Panel>

        {canViewBrief && <Panel id="ai-brief" span={3} title="盘后规则简报" subtitle="今日结论" icon={<Bot size={16} />}>
          <div className="dashboard-brief-box">
            {Number.isFinite(marketScore) && <ScoreRing value={marketScore} label="作战分" />}
            <div className="dashboard-brief-lines">
              {aiLines.length ? aiLines.slice(0, showFullBrief ? aiLines.length : 3).map((line, index) => (
                <p key={`${line}-${index}`}>{index + 1}. {line}</p>
              )) : <p>{queryMessage(briefQuery, '当日暂无简报数据。')}</p>}
            </div>
          </div>
          <div className="dashboard-brief-actions">
            <button type="button" onClick={() => setShowFullBrief(!showFullBrief)}><FileText size={14} />{showFullBrief ? '收起' : '完整日报'}</button>
          </div>
        </Panel>}

        <Panel span={3} title="研究池动态" icon={<Sparkles size={16} />}
          extra={<Link href="/a/research">查看研究</Link>}>
          <MiniTable
            data={themeRows}
            limit={7}
            empty={queryMessage(researchQuery, researchQuery.data?.configured ? '当日研究池快照尚未生成。' : '尚未配置量化研究池。')}
            columns={[
              { key: 'theme_name', label: '研究主题' },
              { key: 'stock_count', label: '股票数', align: 'right', render: (row) => int(row.stock_count) },
              { key: 'core', label: '核心标的', align: 'right', render: (row) => int(row.core) },
              { key: 'status', label: '最高状态', render: (row) => <StatusPill tone={stateTone(row.s5 ? 'S5' : row.s4 ? 'S4' : row.s3 ? 'S3' : '')}>{row.s5 ? 'S5' : row.s4 ? 'S4' : row.s3 ? 'S3' : '—'}</StatusPill> },
            ]}
          />
        </Panel>

        <Panel id="strategy-picks" span={5} title="策略筛选结果"
          subtitle={strategyReady ? `当日命中 ${strategyTotal} 只 · 展示前 ${strategyRows.length} 只` : '当日策略筛选'}
          icon={<Target size={16} />}
          extra={<div className="flex items-center gap-3">
            <select aria-label="筛选策略" value={strategy} onChange={(event) => setStrategy(event.target.value)}
              className="rounded-lg border border-border-primary bg-bg-primary p-2 text-xs text-text-primary">
              {strategies.map(([id, label]) => <option value={id} key={id}>{label}</option>)}
            </select>
            <Link href="/a/strategy" onClick={() => {
              setGlobalDate(selectedDate);
              setStockSidebarUi({ strategy: { source: strategy === 'mrgc_v1' ? 'mrgc' : strategy, symbol: '', detailOpen: false } });
            }}>查看全部</Link>
          </div>}>
          <MiniTable data={strategyRows} limit={12} empty={queryMessage(strategyQuery, strategyReady
            ? '策略已运行，当日没有命中。' : strategyResult?.calculation?.status === 'failed'
              ? '当日策略运行失败。' : '所选日期策略尚未运行。')}
            columns={[
              { key: 'symbol', label: '代码' },
              { key: 'name', label: '股票', render: (row) => <Link href={`/a/stock/${row.symbol}`} onClick={() => setGlobalDate(selectedDate)}>{firstText(row, ['name', 'symbol'])}</Link> },
              { key: 'sector_name', label: '行业' },
              { key: 'close', label: '收盘', align: 'right', render: (row) => num(row.close, 2) },
              { key: 'rps_50', label: 'RPS50', align: 'right', render: (row) => num(row.rps_50, 0) },
            ]}
          />
        </Panel>

        <Panel id="portfolio-risk" span={4} title="持仓提醒"
          subtitle={`${selectedDate} · 数量按事件触发时记录`} icon={<ShieldAlert size={16} />}
          extra={<Link href="/a/portfolio">查看持仓</Link>}>
          <div className="dashboard-alert-list">
            {portfolio?.rows.map((row) => (
              <Link key={row.id} href={`/a/stock/${row.symbol}`} onClick={() => setGlobalDate(selectedDate)} className="dashboard-alert-row">
                <StatusPill tone={row.category === 'executable' ? 'red' : 'amber'}>{riskCategory[row.category]}</StatusPill>
                <div>
                  <strong>{row.name} {row.symbol} · {row.account_name}</strong>
                  <span>{row.reason}</span>
                  <span>日终持仓 {int(row.quantity)} 股 · 触发时可卖 {int(row.available_quantity)} 股 · 建议 {int(row.recommended_quantity)} 股</span>
                </div>
                <em>{eventStatus[row.event_status]} · {(row.bar_time || row.triggered_at).slice(11, 16)}</em>
              </Link>
            ))}
            {!portfolio?.rows.length && <div className="dashboard-empty">{portfolioStatus}</div>}
          </div>
        </Panel>
      </div>
    </main>
  );
}
