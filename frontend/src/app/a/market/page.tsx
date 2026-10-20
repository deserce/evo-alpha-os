'use client';

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

export default function MarketPage() {
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
  const isRefreshing = datesQuery.isFetching || dashboardQuery.isFetching;

  const refresh = () => {
    datesQuery.refetch();
    dashboardQuery.refetch();
  };

  if (dashboardQuery.isLoading && !dashboard) {
    return (
      <main className="dashboard-page">
        <div className="dashboard-grid">
          <section className="dashboard-header dashboard-span-12">
            <div>
              <span className="dashboard-eyebrow">MARKET / 市场温度</span>
              <h1>市场强度与交易权限</h1>
            </div>
          </section>
          <section className="dashboard-panel dashboard-panel-span-full">
            <div className="dashboard-empty">Market 数据加载中</div>
          </section>
        </div>
      </main>
    );
  }

  if (dashboardQuery.error || !dashboard || !hero) {
    return (
      <main className="dashboard-page">
        <div className="dashboard-grid">
          <section className="dashboard-header dashboard-span-12">
            <div>
              <span className="dashboard-eyebrow">MARKET / 市场温度</span>
              <h1>市场强度与交易权限</h1>
              <p>后台真实市场数据暂不可用。</p>
            </div>
            <button type="button" className="dashboard-primary-button" onClick={refresh}>
              <RefreshCw size={14} />
              重试
            </button>
          </section>
        </div>
      </main>
    );
  }

  return (
    <main className="dashboard-page">
      <div className="dashboard-grid">
        <section className="dashboard-header dashboard-span-12">
          <div>
            <span className="dashboard-eyebrow">MARKET / 市场温度</span>
            <h1>市场强度与交易权限</h1>
            <p>交易日 {dashboard.tradeDate} · 数据更新 {dashboard.updatedAt}</p>
          </div>
          <div className="dashboard-header-actions market-header-actions">
            <label className="dashboard-date-select market-action-control">
              <select value={dashboard.tradeDate} onChange={(event) => setGlobalDate(event.target.value)}>
                {dates.map((date) => (
                  <option key={date} value={date}>
                    {date}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="dashboard-primary-button market-action-control" onClick={refresh}>
              <RefreshCw size={14} className={isRefreshing ? 'dashboard-spin' : ''} />
              刷新
            </button>
          </div>
        </section>

        <div className="dashboard-panel-span-full">
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

        <div className="dashboard-panel-span-7">
          <MarketIndexPanel
            indices={dashboard.indices}
            defaultIndexCode={dashboard.defaultIndexCode}
            tradeDate={dashboard.tradeDate}
          />
        </div>
        <div className="dashboard-panel-span-5">
          <MarketBreadthCards breadth={dashboard.breadth} />
        </div>

        <div className="dashboard-panel-span-6">
          <MarketBreadthTrend breadthHistory={dashboard.breadthHistory} />
        </div>
        <div className="dashboard-panel-span-6">
          <LimitMoodChart limitStatsHistory={dashboard.limitStatsHistory} />
        </div>

        <div className="dashboard-panel-span-6">
          <StrengthDiffusionChart strengthDiffusionHistory={dashboard.strengthDiffusionHistory} />
        </div>
        <div className="dashboard-panel-span-6">
          <AmountAndRiskPanel amountHistory={dashboard.amountHistory} riskNotes={dashboard.riskNotes} />
        </div>
      </div>
    </main>
  );
}
