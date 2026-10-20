'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import type { CSSProperties, ReactNode } from 'react';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  Bot,
  BriefcaseBusiness,
  CalendarDays,
  ChevronRight,
  FileText,
  Layers3,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Target,
  Zap,
} from 'lucide-react';
import { quantApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';

type Row = Record<string, unknown>;
type Tone = 'green' | 'red' | 'blue' | 'amber' | 'purple' | 'muted';
type RiskLevel = 'P0' | 'P1' | 'P2' | 'P3';

const signalNames: Record<string, string> = {
  pocket_pivot: '口袋买点',
  breakout: '突破买点',
  supply_exhaustion: '抛压衰竭',
  earnings_gap: '业绩二次突破',
};

const toneClass: Record<Tone, string> = {
  green: 'is-green',
  red: 'is-red',
  blue: 'is-blue',
  amber: 'is-amber',
  purple: 'is-purple',
  muted: 'is-muted',
};

const RPS_PERIODS = [5, 10, 20, 50, 120, 250];

function RpsSelect({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(Number(event.target.value))}
      className="h-8 rounded-lg border border-border-primary bg-bg-primary px-2 text-xs font-mono text-text-primary outline-none"
    >
      {RPS_PERIODS.map((period) => (
        <option key={period} value={period}>
          RPS{period}
        </option>
      ))}
    </select>
  );
}

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
  const parsed = Number(value);
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

function signalLabel(value: unknown) {
  const key = text(value, '');
  return signalNames[key] || key || '-';
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

function countLifecycle(counts: Row[], sourceRows: Row[], state: string) {
  const row = counts.find((item) => text(item.lifecycle_state, '') === state);
  if (row) return toNumber(row.count);
  return sourceRows.filter((item) => text(item.lifecycle_state, '') === state).length;
}

function buildThemeSummaries(researchRows: Row[]) {
  const map = new Map<string, {
    themeName: string;
    stockCount: number;
    s3: number;
    s4: number;
    s5: number;
    core: number;
    signalCount: number;
    score: number;
    rows: Row[];
  }>();

  researchRows.forEach((row) => {
    const themeName = firstText(row, ['theme_name', 'node_name'], '未命名主题');
    const state = text(row.lifecycle_state, '');
    const item = map.get(themeName) ?? {
      themeName,
      stockCount: 0,
      s3: 0,
      s4: 0,
      s5: 0,
      core: 0,
      signalCount: 0,
      score: 0,
      rows: [],
    };
    item.stockCount += 1;
    item.rows.push(row);
    item.score += toNumber(row.structure_score);
    item.signalCount += toNumber(row.timing_signal_count);
    if (state === 'S3') item.s3 += 1;
    if (state === 'S4') item.s4 += 1;
    if (state === 'S5') item.s5 += 1;
    if (state === 'S4' || state === 'S5' || toNumber(row.timing_signal_count) > 0) item.core += 1;
    map.set(themeName, item);
  });

  return Array.from(map.values()).sort((a, b) => {
    if (b.core !== a.core) return b.core - a.core;
    if (b.signalCount !== a.signalCount) return b.signalCount - a.signalCount;
    return b.score - a.score;
  });
}

function buildCoreRows(researchRows: Row[], alphaRows: Row[]) {
  const researchCore = researchRows
    .filter((row) => ['S4', 'S5'].includes(text(row.lifecycle_state, '')) || toNumber(row.timing_signal_count) > 0)
    .sort((a, b) => {
      const aScore = toNumber(a.timing_signal_count) * 20 + toNumber(a.structure_score);
      const bScore = toNumber(b.timing_signal_count) * 20 + toNumber(b.structure_score);
      return bScore - aScore;
    });

  if (researchCore.length > 0) return researchCore;
  return alphaRows.filter((row) => ['S4', 'S5'].includes(text(row.lifecycle_state, '')));
}

function alphaSortScore(row: Row, rpsPeriod: number) {
  return toNumber(firstValue(row, ['rps_value', `rps_${rpsPeriod}`]))
    + toNumber(row.signal_score) * 0.2
    + toNumber(row.structure_score) * 0.1;
}

function getRiskLevel(row: Row): RiskLevel {
  const planStatus = text(row.plan_status, '').toLowerCase();
  const action = text(row.recommended_action, '').toLowerCase();
  const riskStatus = text(row.risk_status, '').toLowerCase();
  const exitReason = text(row.exit_reason, '');
  if (planStatus.includes('exit') || action.includes('exit') || riskStatus.includes('stop') || exitReason) return 'P3';
  if (planStatus.includes('reduce') || action.includes('reduce')) return 'P2';
  if (riskStatus && riskStatus !== 'normal') return 'P1';
  return 'P0';
}

function riskReason(row: Row) {
  const reason = text(row.exit_reason, '');
  if (reason !== '-') return reason;
  const marketAction = text(row.market_risk_action, '');
  if (marketAction !== '-') return marketAction;
  const industryAction = text(row.industry_risk_action, '');
  if (industryAction !== '-') return industryAction;
  const riskStatus = text(row.risk_status, '');
  return riskStatus !== '-' ? riskStatus : '正常跟踪';
}

function riskAction(row: Row) {
  const action = text(row.recommended_action, '');
  if (action !== '-') return action;
  const level = getRiskLevel(row);
  if (level === 'P3') return '退出';
  if (level === 'P2') return '减仓';
  if (level === 'P1') return '观察';
  return '持有';
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
  span: 3 | 4 | 5 | 12;
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
        {extra ?? <ChevronRight size={15} />}
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
  const router = useRouter();
  const { globalDate, setGlobalDate, setSelectedSector } = useGlobalStore();
  const [alphaRpsPeriod, setAlphaRpsPeriod] = useState(20);

  const datesQuery = useQuery({
    queryKey: ['dashboardDates'],
    queryFn: () => quantApi.getDates(160),
  });
  const dates = rows<string>(datesQuery.data?.dates);
  const selectedDate = dates.includes(globalDate) ? globalDate : datesQuery.data?.latest || '';
  const dateOptions = dates.length ? dates : (selectedDate ? [selectedDate] : []);

  const dashboardQuery = useQuery({
    queryKey: ['dashboardSummary', selectedDate],
    queryFn: () => quantApi.getDashboard(selectedDate),
    enabled: Boolean(selectedDate),
  });
  const industryQuery = useQuery({
    queryKey: ['dashboardIndustryStrength', selectedDate],
    queryFn: () => quantApi.getIndustryStrength(selectedDate, 100),
    enabled: Boolean(selectedDate),
  });
  const signalsQuery = useQuery({
    queryKey: ['dashboardSignals', selectedDate],
    queryFn: () => quantApi.getSignals({ tradeDate: selectedDate, limit: 80 }),
    enabled: Boolean(selectedDate),
  });
  const plansQuery = useQuery({
    queryKey: ['dashboardPlans', selectedDate],
    queryFn: () => quantApi.getTradePlans({ tradeDate: selectedDate, limit: 80 }),
    enabled: Boolean(selectedDate),
  });
  const lifecycleQuery = useQuery({
    queryKey: ['dashboardLifecycle', selectedDate],
    queryFn: () => quantApi.getLifecycle({ tradeDate: selectedDate, state: undefined, limit: 240 }),
    enabled: Boolean(selectedDate),
  });
  const alphaQuery = useQuery({
    queryKey: ['dashboardAlphaOpportunities', selectedDate, alphaRpsPeriod],
    queryFn: () => quantApi.getAlphaOpportunities({ tradeDate: selectedDate, rpsPeriod: alphaRpsPeriod, limit: 80 }),
    enabled: Boolean(selectedDate),
  });
  const researchQuery = useQuery({
    queryKey: ['dashboardResearchSnapshot', selectedDate],
    queryFn: () => quantApi.getResearchSnapshot(selectedDate, 240),
    enabled: Boolean(selectedDate),
  });
  const briefQuery = useQuery({
    queryKey: ['dashboardBrief', selectedDate],
    queryFn: () => quantApi.getAiBrief(selectedDate),
    enabled: Boolean(selectedDate),
  });

  const dashboard = dashboardQuery.data ?? {};
  const market = dashboard.market ?? {};
  const dashboardIndustries = rows<Row>(dashboard.top_industries);
  const liveIndustryRows = rows<Row>(industryQuery.data?.rows);
  const industryRows = liveIndustryRows.length ? liveIndustryRows : dashboardIndustries;
  const rankedIndustries = [...industryRows]
    .sort((a, b) => toNumber(b.industry_strength_score) - toNumber(a.industry_strength_score))
    .slice(0, 8);
  const opportunities = rows<Row>(alphaQuery.data?.rows);
  const lifecycleCounts = rows<Row>(dashboard.lifecycle_counts);
  const signalCounts = rows<Row>(dashboard.signal_counts);
  const signals = rows<Row>(signalsQuery.data?.rows);
  const plans = rows<Row>(plansQuery.data?.rows);
  const lifecycle = rows<Row>(lifecycleQuery.data?.rows);
  const researchRows = rows<Row>(researchQuery.data?.rows);
  const briefLines = rows<string>(briefQuery.data?.bullets);

  const marketState = text(market.market_state, '-');
  const marketScore = toNumber(market.market_strength_score, 0);
  const i4Count = industryRows.filter((row) => text(row.industry_state, '') === 'I4').length;
  const i3Count = industryRows.filter((row) => text(row.industry_state, '') === 'I3').length;
  const s4Count = countLifecycle(lifecycleCounts, lifecycle, 'S4');
  const s5Count = countLifecycle(lifecycleCounts, lifecycle, 'S5') || signals.filter((row) => text(row.lifecycle_state, '') === 'S5').length;
  const coreRows = buildCoreRows(researchRows, opportunities);
  const alphaRows = opportunities
    .filter((row) => ['S4', 'S5'].includes(text(row.lifecycle_state, '')))
    .sort((a, b) => alphaSortScore(b, alphaRpsPeriod) - alphaSortScore(a, alphaRpsPeriod));
  const coreAlphaRows = alphaRows.length ? alphaRows : coreRows;
  const alphaS4Count = alphaRows.filter((row) => text(row.lifecycle_state, '') === 'S4').length || s4Count;
  const alphaS5Count = alphaRows.filter((row) => text(row.lifecycle_state, '') === 'S5').length || s5Count;
  const signalTotal = signals.length || signalCounts.reduce((sum, row) => sum + toNumber(row.count), 0);
  const themes = buildThemeSummaries(researchRows);
  const themeRows = themes.map((item) => ({
    theme_name: item.themeName,
    stock_count: item.stockCount,
    s3: item.s3,
    s4: item.s4,
    s5: item.s5,
    core: item.core,
    status: item.s5 > 0 ? 'S5' : item.s4 > 0 ? 'S4' : 'S3',
  }));
  const riskRows = plans.filter((row) => getRiskLevel(row) !== 'P0');
  const p0Count = plans.filter((row) => getRiskLevel(row) === 'P0').length;
  const p1Count = plans.filter((row) => getRiskLevel(row) === 'P1').length;
  const p2Count = plans.filter((row) => getRiskLevel(row) === 'P2').length;
  const p3Count = plans.filter((row) => getRiskLevel(row) === 'P3').length;
  const aiLines = briefLines;
  const isRefreshing = dashboardQuery.isFetching || industryQuery.isFetching || signalsQuery.isFetching || alphaQuery.isFetching;

  function refreshAll() {
    void Promise.all([
      dashboardQuery.refetch(),
      industryQuery.refetch(),
      signalsQuery.refetch(),
      plansQuery.refetch(),
      lifecycleQuery.refetch(),
      alphaQuery.refetch(),
      researchQuery.refetch(),
      briefQuery.refetch(),
    ]);
  }

  return (
    <main className="dashboard-page">
      <div className="dashboard-grid">
        <section className="dashboard-header dashboard-span-12">
          <div className="dashboard-header-copy">
            <span className="dashboard-eyebrow">Dashboard / 总览</span>
            <h1>今日作战中枢</h1>
            <p>从市场强度、行业主线、Alpha 机会、研究池变化和持仓风险五个维度，生成今日作战视图。</p>
          </div>
          <div className="dashboard-header-actions">
            <label className="dashboard-date-select">
              <CalendarDays size={14} />
              <select value={selectedDate || ''} onChange={(event) => setGlobalDate(event.target.value)}>
                {dateOptions.map((date) => <option key={date} value={date}>{date}</option>)}
              </select>
            </label>
            <span className="dashboard-sync">
              <span className="status-dot bg-success text-success" />
              数据更新时间 15:30
            </span>
            <button type="button" className="dashboard-icon-button" onClick={refreshAll} title="刷新">
              <RefreshCw size={15} className={isRefreshing ? 'dashboard-spin' : ''} />
            </button>
            <button type="button" className="dashboard-primary-button" onClick={() => void briefQuery.refetch()}>
              <Bot size={15} />
              生成 AI 日报
            </button>
          </div>
        </section>

        <Link href="/a/market" className="dashboard-status-card dashboard-span-3">
          <div className="dashboard-status-head">
            <span>市场状态</span>
            <StatusPill tone={stateTone(marketState)}>{marketState}</StatusPill>
          </div>
          <div className="dashboard-status-main">
            <strong>{num(marketScore, 0)} / 100</strong>
            <p>建议仓位 {suggestedPosition(marketScore)}</p>
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
            <strong>{i4Count}<span>I4 核心主线</span></strong>
            <strong>{i3Count}<span>I3 强势行业</span></strong>
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

        <Link href="/a/alpha-pool" className="dashboard-status-card dashboard-span-3">
          <div className="dashboard-status-head">
            <span>Alpha 机会</span>
            <StatusPill tone="purple">Core</StatusPill>
          </div>
          <div className="dashboard-two-metric">
            <strong>{s4Count}<span>S4 结构准备</span></strong>
            <strong>{s5Count}<span>S5 买点触发</span></strong>
          </div>
          <div className="dashboard-status-foot">
            Core Alpha {coreAlphaRows.length} 只
            <ChevronRight size={14} />
          </div>
        </Link>

        <Link href="/a/portfolio" className="dashboard-status-card dashboard-span-3">
          <div className="dashboard-status-head">
            <span>持仓风险</span>
            <StatusPill tone={p2Count + p3Count > 0 ? 'amber' : 'green'}>{p2Count + p3Count > 0 ? '注意' : '正常'}</StatusPill>
          </div>
          <div className="dashboard-risk-grid">
            <span>P0 <b>{p0Count}</b></span>
            <span>P1 <b>{p1Count}</b></span>
            <span>P2 <b>{p2Count}</b></span>
            <span>P3 <b>{p3Count}</b></span>
          </div>
          <div className="dashboard-status-foot">
            今日新增风险 {riskRows.length || p2Count}
            <ChevronRight size={14} />
          </div>
        </Link>

        <Panel span={5} title="强行业榜" subtitle="主战场在哪里" icon={<Layers3 size={16} />}>
          <MiniTable
            data={rankedIndustries}
            limit={8}
            empty="暂无数据库行业强度，请先运行 P0/P1 计算。"
            onRowClick={(row) => {
              const sectorName = firstText(row, ['sector_name'], '');
              if (sectorName) {
                setSelectedSector(sectorName);
                router.push('/a/sector');
              }
            }}
            columns={[
              { key: 'rank', label: '排名', render: (_row, index) => index + 1 },
              { key: 'sector_name', label: '行业' },
              { key: 'industry_state', label: '状态', render: (row) => <StatusPill tone={stateTone(row.industry_state)}>{text(row.industry_state, 'I-')}</StatusPill> },
              { key: 'industry_strength_score', label: '强度', align: 'right', render: (row) => num(row.industry_strength_score, 0) },
              { key: 'return_20d', label: '20日', align: 'right', render: (row) => pct(row.return_20d, 1) },
              { key: 'rps90_ratio', label: 'RPS90', align: 'right', render: (row) => `${num(row.rps90_ratio, 0)}%` },
              { key: 'leader_name', label: '龙头', render: (row) => firstText(row, ['leader_name', 'stock_name', 'name'], '-') },
            ]}
          />
        </Panel>

        <Panel span={4} title="今日买点触发" subtitle={`${signalTotal} 个信号等待筛选`} icon={<Zap size={16} />}>
          <div className="dashboard-tabs">
            {['全部', '口袋买点', '突破买点', '抛压衰竭'].map((item, index) => (
              <span key={item} className={index === 0 ? 'is-active' : ''}>{item}</span>
            ))}
          </div>
          <MiniTable
            data={signals}
            limit={7}
            empty="暂无数据库买点信号，timing_signals_daily 未生成该日记录。"
            onRowClick={(row) => {
              const symbol = firstText(row, ['symbol'], '');
              if (symbol) {
                router.push(`/a/stock/${symbol}`);
              }
            }}
            columns={[
              { key: 'name', label: '股票', render: (row) => firstText(row, ['name', 'stock_name', 'symbol']) },
              { key: 'sector_name', label: '行业', render: (row) => firstText(row, ['sector_name'], '-') },
              { key: 'signal_type', label: '买点', render: (row) => signalLabel(row.signal_type) },
              { key: 'structure_score', label: '结构', align: 'right', render: (row) => num(row.structure_score, 0) },
              { key: 'stop_loss_pct', label: '止损', align: 'right', render: (row) => pct(row.stop_loss_pct, 1) },
              { key: 'signal_score', label: '信号', align: 'right', render: (row) => num(row.signal_score, 0) },
            ]}
          />
        </Panel>

        <Panel id="ai-brief" span={3} title="AI 每日作战简报" subtitle="今日结论" icon={<Bot size={16} />}>
          <div className="dashboard-brief-box">
            <ScoreRing value={marketScore} label="作战分" />
            <div className="dashboard-brief-lines">
              {aiLines.length ? aiLines.slice(0, 3).map((line, index) => (
                <p key={`${line}-${index}`}>{index + 1}. {line}</p>
              )) : <p>暂无数据库简报，请检查 market_strength_daily、industry_strength_daily 与信号表。</p>}
            </div>
          </div>
          <div className="dashboard-brief-actions">
            <button type="button" onClick={() => void briefQuery.refetch()}><FileText size={14} />完整日报</button>
            <button type="button" onClick={() => void briefQuery.refetch()}><RefreshCw size={14} />重新生成</button>
          </div>
        </Panel>

        <Panel span={5} title="研究池动态" subtitle="研究主题是否被市场验证" icon={<Sparkles size={16} />}>
          <MiniTable
            data={themeRows}
            limit={7}
            empty="研究池快照未生成，research_stock_daily_snapshot 当前无记录。"
            columns={[
              { key: 'theme_name', label: '研究主题' },
              { key: 'stock_count', label: '股票数', align: 'right', render: (row) => int(row.stock_count) },
              { key: 's3', label: 'S3', align: 'right', render: (row) => int(row.s3) },
              { key: 's4', label: 'S4', align: 'right', render: (row) => int(row.s4) },
              { key: 's5', label: 'S5', align: 'right', render: (row) => int(row.s5) },
              { key: 'core', label: 'Core', align: 'right', render: (row) => int(row.core) },
              { key: 'status', label: '变化', render: (row) => <StatusPill tone={stateTone(row.status)}>{text(row.status)}</StatusPill> },
            ]}
          />
        </Panel>

        <Panel
          id="alpha-pool"
          span={12}
          title="Alpha Pool / S4-S5 主表"
          subtitle={`真实生命周期机会池 · RPS${alphaRpsPeriod}排序 · 点击进入个股工作台`}
          icon={<Target size={16} />}
          extra={<RpsSelect value={alphaRpsPeriod} onChange={setAlphaRpsPeriod} />}
        >
          <div className="mb-3 grid gap-2 sm:grid-cols-4">
            <div className="evo-stat">
              <div className="truncate text-[10px] text-text-tertiary">Alpha机会</div>
              <div className="mt-1 truncate font-mono text-[18px] font-black leading-none text-brand-blue-light">{int(coreAlphaRows.length)}</div>
              <div className="mt-1 truncate text-[10px] text-text-secondary">stock_lifecycle_state</div>
            </div>
            <div className="evo-stat">
              <div className="truncate text-[10px] text-text-tertiary">S4 结构准备</div>
              <div className="mt-1 truncate font-mono text-[18px] font-black leading-none text-warning">{int(alphaS4Count)}</div>
              <div className="mt-1 truncate text-[10px] text-text-secondary">临界观察</div>
            </div>
            <div className="evo-stat">
              <div className="truncate text-[10px] text-text-tertiary">S5 买点触发</div>
              <div className="mt-1 truncate font-mono text-[18px] font-black leading-none text-success">{int(alphaS5Count)}</div>
              <div className="mt-1 truncate text-[10px] text-text-secondary">可进入作战室</div>
            </div>
            <div className="evo-stat">
              <div className="truncate text-[10px] text-text-tertiary">当日买点信号</div>
              <div className="mt-1 truncate font-mono text-[18px] font-black leading-none text-danger">{int(signalTotal)}</div>
              <div className="mt-1 truncate text-[10px] text-text-secondary">timing_signals_daily</div>
            </div>
          </div>
          <MiniTable
            data={coreAlphaRows}
            limit={18}
            empty="暂无数据库 Alpha 机会，stock_lifecycle_state 未生成 S4/S5 记录。"
            onRowClick={(row) => {
              const symbol = firstText(row, ['symbol'], '');
              if (symbol) {
                router.push(`/a/stock/${symbol}`);
              }
            }}
            columns={[
              { key: 'symbol', label: '代码', render: (row) => <span className="font-mono text-brand-blue-light">{firstText(row, ['symbol'])}</span> },
              { key: 'name', label: '股票', render: (row) => firstText(row, ['name', 'stock_name', 'symbol']) },
              { key: 'sector_name', label: '行业', render: (row) => firstText(row, ['sector_name', 'industry'], '-') },
              { key: 'market_state', label: '市场', render: (row) => <StatusPill tone={stateTone(row.market_state)}>{text(row.market_state, 'M-')}</StatusPill> },
              { key: 'industry_state', label: '行业状态', render: (row) => <StatusPill tone={stateTone(row.industry_state)}>{text(row.industry_state, 'I-')}</StatusPill> },
              { key: 'lifecycle_state', label: '状态', render: (row) => <StatusPill tone={stateTone(row.lifecycle_state)}>{text(row.lifecycle_state, 'S-')}</StatusPill> },
              { key: 'structure_score', label: '结构', align: 'right', render: (row) => num(row.structure_score, 0) },
              { key: 'rps_value', label: `RPS${alphaRpsPeriod}`, align: 'right', render: (row) => num(firstValue(row, ['rps_value', `rps_${alphaRpsPeriod}`, 'signal_score']), 0) },
              { key: 'signal_type', label: '买点', render: (row) => signalLabel(row.signal_type) },
              { key: 'entry_price', label: '入场', align: 'right', render: (row) => num(row.entry_price, 2) },
              { key: 'stop_loss_price', label: '止损', align: 'right', render: (row) => num(row.stop_loss_price, 2) },
            ]}
          />
        </Panel>

        <Panel span={3} title="计划风险提醒" subtitle="信号建议风险，非真实持仓" icon={<ShieldAlert size={16} />}>
          <div className="dashboard-alert-list">
            {riskRows.length ? riskRows.slice(0, 5).map((row, index) => {
              const level = getRiskLevel(row);
              const symbol = firstText(row, ['symbol'], '');
              return (
                <div
                  key={`${firstText(row, ['name', 'symbol'], 'risk')}-${index}`}
                  className={`dashboard-alert-row${symbol ? ' cursor-pointer' : ''}`}
                  onClick={symbol ? () => {
                    router.push(`/a/stock/${symbol}`);
                  } : undefined}
                >
                  <StatusPill tone={stateTone(level)}>{level}</StatusPill>
                  <div>
                    <strong>{firstText(row, ['name', 'stock_name', 'symbol'], '持仓标的')}</strong>
                    <span>{riskReason(row)}</span>
                  </div>
                  <em>{riskAction(row)}</em>
                </div>
              );
            }) : <div className="dashboard-empty">暂无数据库风险提醒，trade_plans 未生成该日风险记录。</div>}
          </div>
        </Panel>
      </div>
    </main>
  );
}
