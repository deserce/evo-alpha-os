'use client';

import { TopbarTradeDate } from '@/components/layout/TopbarTradeDate';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { marketApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import {
  AmountAndRiskPanel,
  LimitMoodChart,
  MarketBreadthTrend,
  MarketIndexPanel,
  MarketOverviewCard,
  StrengthDiffusionChart,
} from '@/components/market';

interface RiskNote {
  level: 'low' | 'medium' | 'high';
  title: string;
  description: string;
}

interface MarketHero {
  tradeDate: string;
  marketState: 'M0' | 'M1' | 'M2' | 'M3' | 'M4';
  marketStrengthScore: number;
  suggestedPositionMin: number;
  suggestedPositionMax: number;
  tradingPermission: 'forbidden' | 'observe' | 'limited' | 'normal' | 'active';
  summary: string;
  riskNotes: RiskNote[];
  updatedAt: string;
}

interface MarketBreadth {
  upCount: number;
  downCount: number;
  upRatio: number;
  limitUpCount: number;
  limitDownCount: number;
  newHigh20Count: number;
  newHigh60Count: number;
  newHigh120Count: number;
  rps90Count: number;
  totalAmount: number;
  amountRatio20: number;
}

interface MarketDashboard {
  tradeDate: string;
  updatedAt: string;
  hero: MarketHero;
  indices: any[];
  defaultIndexCode: string;
  breadth: MarketBreadth;
  breadthHistory: any[];
  limitStatsHistory: any[];
  strengthDiffusionHistory: any[];
  amountHistory: any[];
  riskNotes: RiskNote[];
}

function formatAmount(value?: number) {
  if (!value) return '-';
  if (value >= 100000000) return `${(value / 100000000).toFixed(0)}亿`;
  if (value >= 10000) return `${(value / 10000).toFixed(0)}万`;
  return value.toFixed(0);
}

function BreadthMetric({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: 'up' | 'down' | 'neutral' | 'warn';
}) {
  const toneClass = {
    up: 'text-success',
    down: 'text-danger',
    neutral: 'text-text-primary',
    warn: 'text-warning',
  }[tone];

  return (
    <div className="min-h-[74px] border-b border-r border-border-secondary/50 p-3 last:border-r-0">
      <span className="dashboard-eyebrow text-[10px]">{label}</span>
      <strong className={`mt-1 block text-xl font-black font-mono ${toneClass}`}>{value}</strong>
      {sub && <p className="mt-1 text-[11px] text-text-tertiary">{sub}</p>}
    </div>
  );
}

function MarketBreadthCards({ breadth }: { breadth?: MarketBreadth }) {
  if (!breadth) {
    return (
      <section className="dashboard-panel min-h-[420px]">
        <div className="dashboard-empty">暂无市场宽度</div>
      </section>
    );
  }

  return (
    <section className="dashboard-panel min-h-[420px]">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>03</span>
          <div>
            <h2>市场宽度</h2>
            <p>赚钱效应与扩散</p>
          </div>
        </div>
      </div>
      <div className="dashboard-panel-body !p-0">
        <div className="grid flex-1 grid-cols-2">
          <BreadthMetric label="上涨家数" value={breadth.upCount} sub={`${breadth.upRatio.toFixed(1)}%`} tone="up" />
          <BreadthMetric label="下跌家数" value={breadth.downCount} tone="down" />
          <BreadthMetric label="涨停数量" value={breadth.limitUpCount} tone="up" />
          <BreadthMetric label="跌停数量" value={breadth.limitDownCount} tone={breadth.limitDownCount > 10 ? 'down' : 'neutral'} />
          <BreadthMetric label="创60日新高" value={breadth.newHigh60Count} tone="warn" />
          <BreadthMetric label="创120日新高" value={breadth.newHigh120Count} tone="warn" />
          <BreadthMetric label="RPS90股票数" value={breadth.rps90Count} tone="neutral" />
          <BreadthMetric label="全市场成交额" value={formatAmount(breadth.totalAmount)} sub={`×${breadth.amountRatio20.toFixed(2)}`} tone="neutral" />
        </div>
      </div>
    </section>
  );
}

const marketViews = [
  ['breadth', '市场宽度'],
  ['trend', '宽度趋势'],
  ['limits', '涨跌停'],
  ['diffusion', '强势扩散'],
  ['amount', '成交与风险'],
] as const;

export default function MarketPage() {
  const [view, setView] = useState<(typeof marketViews)[number][0]>('breadth');
  const { globalDate, setGlobalDate } = useGlobalStore();

  const datesQuery = useQuery({
    queryKey: ['marketDates'],
    queryFn: () => marketApi.getDates(160),
  });

  const dates: string[] = datesQuery.data?.dates || [];
  const selectedDate = dates.includes(globalDate) ? globalDate : datesQuery.data?.latest || '';

  const dashboardQuery = useQuery<MarketDashboard>({
    queryKey: ['marketDashboard', selectedDate],
    queryFn: () => marketApi.getDashboard(selectedDate),
    enabled: Boolean(selectedDate),
  });

  const dashboard = dashboardQuery.data;
  const hero = dashboard?.hero;
  const pageHeader = (
    <section className="evo-page-header dashboard-span-12 flex flex-wrap items-center justify-between gap-2">
      <h1 className="evo-page-title"><span className="evo-page-index">02</span> 市场强度与交易权限</h1>
      <span className="dashboard-sync ml-auto min-h-8 !h-auto max-w-full !whitespace-normal">
        <span className={dashboard ? 'status-dot bg-success text-success' : 'status-dot bg-current'} />
        {dashboard
          ? `交易日 ${dashboard.tradeDate} · 数据更新 ${dashboard.updatedAt || '—'}`
          : dashboardQuery.isLoading ? '市场数据加载中' : '暂无更新时间'}
      </span>
    </section>
  );

  if (dashboardQuery.isLoading && !dashboard) {
    return (
      <main className="dashboard-page">
      <TopbarTradeDate value={selectedDate} dates={dates} onChange={setGlobalDate} />
        <div className="dashboard-grid">
          {pageHeader}
          <section className="dashboard-panel dashboard-panel-span-full">
            <div className="dashboard-empty">市场数据加载中</div>
          </section>
        </div>
      </main>
    );
  }

  if (dashboardQuery.error || !dashboard || !hero) {
    return (
      <main className="dashboard-page">
      <TopbarTradeDate value={selectedDate} dates={dates} onChange={setGlobalDate} />
        <div className="dashboard-grid">
          {pageHeader}
          <section className="dashboard-panel dashboard-panel-span-full">
            <div className="dashboard-empty">后台真实市场数据暂不可用。 <button type="button" className="dashboard-primary-button" onClick={() => { void datesQuery.refetch(); void dashboardQuery.refetch(); }}>
              <RefreshCw size={14} />
              重试
            </button></div>
          </section>
        </div>
      </main>
    );
  }

  return (
    <main className="dashboard-page market-overview">
      <TopbarTradeDate value={selectedDate} dates={dates} onChange={setGlobalDate} />
      <div className="dashboard-grid">
        {pageHeader}

        <div className="dashboard-panel-span-full market-overview-hero">
          <MarketOverviewCard
            state={hero.marketState}
            score={hero.marketStrengthScore}
            suggestedPositionMin={hero.suggestedPositionMin}
            suggestedPositionMax={hero.suggestedPositionMax}
            tradingPermission={hero.tradingPermission}
            upCount={dashboard.breadth.upCount}
            downCount={dashboard.breadth.downCount}
            limitUpCount={dashboard.breadth.limitUpCount}
            limitDownCount={dashboard.breadth.limitDownCount}
            newHigh20Count={dashboard.breadth.newHigh20Count}
            rps90Count={dashboard.breadth.rps90Count}
            amount={dashboard.breadth.totalAmount}
            amountRatio20={dashboard.breadth.amountRatio20}
            summary={hero.summary}
            riskNotes={hero.riskNotes}
          />
        </div>

        <div className="dashboard-panel-span-7 market-overview-index">
          <MarketIndexPanel
            indices={dashboard.indices}
            defaultIndexCode={dashboard.defaultIndexCode}
            tradeDate={dashboard.tradeDate}
          />
        </div>
        <div className="dashboard-panel-span-5 market-overview-detail">
          <div className="dashboard-tabs" role="group" aria-label="市场分析视图">
            {marketViews.map(([key, label]) => (
              <button key={key} type="button" aria-pressed={view === key}
                className={view === key ? 'is-active' : ''}
                onClick={() => setView(key)}>{label}</button>
            ))}
          </div>
          {view === 'breadth' && <MarketBreadthCards breadth={dashboard.breadth} />}
          {view === 'trend' && <MarketBreadthTrend breadthHistory={dashboard.breadthHistory} />}
          {view === 'limits' && <LimitMoodChart limitStatsHistory={dashboard.limitStatsHistory} />}
          {view === 'diffusion' && <StrengthDiffusionChart strengthDiffusionHistory={dashboard.strengthDiffusionHistory} />}
          {view === 'amount' && <AmountAndRiskPanel amountHistory={dashboard.amountHistory} riskNotes={dashboard.riskNotes} />}
        </div>
      </div>
    </main>
  );
}
