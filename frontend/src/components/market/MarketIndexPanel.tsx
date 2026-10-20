'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react';
import { marketApi } from '@/lib/api';
import {
  CHART_PERIODS, chartPeriodLabel, formatChartAxisTime, isMinutePeriod, type ChartPeriod,
} from '@/lib/chartPeriods';

interface IndexItem {
  indexCode: string;
  indexName: string;
  tradeDate?: string;
  close: number;
  pctChange: number;
  amount: number;
  ma20?: number | null;
  ma50?: number | null;
  ma120?: number | null;
  ma250?: number | null;
  aboveMa20: boolean;
  aboveMa50: boolean;
  aboveMa120: boolean;
  aboveMa250?: boolean;
  trendState: 'weak' | 'neutral' | 'strong';
  return20d?: number | null;
  return50d?: number | null;
  return120d?: number | null;
}

interface IndexKlineItem {
  tradeDate: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  pctChange?: number | null;
  ma20?: number | null;
  ma50?: number | null;
  ma120?: number | null;
  ma250?: number | null;
}

interface MarketIndexPanelProps {
  indices?: IndexItem[];
  defaultIndexCode?: string;
  tradeDate?: string;
}

const trendConfig = {
  weak: { label: '弱', color: '#28e070' },
  neutral: { label: '中', color: '#ffb020' },
  strong: { label: '强', color: '#ff4f6d' },
};

function formatNumber(value?: number | null, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return '-';
  return value.toFixed(digits);
}

function formatAmount(value?: number | null) {
  if (!value || Number.isNaN(value)) return '-';
  if (value >= 100000000) return `${(value / 100000000).toFixed(0)}亿`;
  if (value >= 10000) return `${(value / 10000).toFixed(0)}万`;
  return value.toFixed(0);
}

function formatAxisAmount(value: number) {
  if (!value || Number.isNaN(value)) return '0';
  if (Math.abs(value) >= 100000000) return `${(value / 100000000).toFixed(value >= 1000000000 ? 0 : 1)}亿`;
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(0)}万`;
  return value.toFixed(0);
}

function pctColor(value?: number | null) {
  return (value ?? 0) >= 0 ? '#ff4f6d' : '#28e070';
}

function returnPct(items: IndexKlineItem[], period: number) {
  if (items.length <= period) return null;
  const latest = items[items.length - 1]?.close;
  const previous = items[items.length - 1 - period]?.close;
  if (!latest || !previous) return null;
  return ((latest - previous) / previous) * 100;
}

export function MarketIndexPanel({ indices = [], defaultIndexCode, tradeDate }: MarketIndexPanelProps) {
  const [period, setPeriod] = useState<ChartPeriod>('daily');
  const minutePeriod = isMinutePeriod(period);
  const [selectedCode, setSelectedCode] = useState(defaultIndexCode || indices[0]?.indexCode || '000001');
  const effectiveSelectedCode = indices.some((item) => item.indexCode === selectedCode)
    ? selectedCode
    : defaultIndexCode || indices[0]?.indexCode || '000001';
  const selectedIndex = indices.find((item) => item.indexCode === effectiveSelectedCode) || indices[0];

  const klineQuery = useQuery({
    queryKey: ['marketIndexKline', effectiveSelectedCode, tradeDate || 'latest', period],
    queryFn: () => marketApi.getMarketIndexKline(effectiveSelectedCode, 250, tradeDate, period),
    enabled: Boolean(effectiveSelectedCode),
    staleTime: minutePeriod ? 120000 : 1000 * 60 * 30,
    gcTime: 300000,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const klineItems: IndexKlineItem[] = useMemo(() => klineQuery.data?.items || [], [klineQuery.data?.items]);
  const latestKline = klineItems[klineItems.length - 1];
  const displayClose = latestKline?.close ?? (minutePeriod ? undefined : selectedIndex?.close);
  const displayPctChange = latestKline?.pctChange ?? (minutePeriod ? undefined : selectedIndex?.pctChange);
  const displayAmount = latestKline?.amount ?? (minutePeriod ? undefined : selectedIndex?.amount);
  const displayReturn20d = minutePeriod ? selectedIndex?.return20d
    : returnPct(klineItems, 20) ?? selectedIndex?.return20d;
  const displayReturn50d = minutePeriod ? selectedIndex?.return50d
    : returnPct(klineItems, 50) ?? selectedIndex?.return50d;
  const latestMa20 = latestKline?.ma20 ?? (minutePeriod ? undefined : selectedIndex?.ma20);
  const latestMa50 = latestKline?.ma50 ?? (minutePeriod ? undefined : selectedIndex?.ma50);
  const latestMa120 = latestKline?.ma120 ?? (minutePeriod ? undefined : selectedIndex?.ma120);
  const latestClose = displayClose ?? 0;
  const latestAboveMa20 = latestMa20 !== null && latestMa20 !== undefined && latestClose >= latestMa20;
  const latestAboveMa50 = latestMa50 !== null && latestMa50 !== undefined && latestClose >= latestMa50;
  const latestAboveMa120 = latestMa120 !== null && latestMa120 !== undefined && latestClose >= latestMa120;

  const option = useMemo(() => {
    const dates = klineItems.map((item) => item.tradeDate);
    const candleData = klineItems.map((item) => [item.open, item.close, item.low, item.high]);
    const amounts = klineItems.map((item) => item.amount || 0);

    return {
      animation: false,
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        borderColor: '#30363d',
        backgroundColor: '#0d1117',
        textStyle: { color: '#c9d1d9', fontSize: 12 },
      },
      legend: {
        top: 0,
        right: 8,
        itemWidth: 10,
        itemHeight: 2,
        textStyle: { color: '#8a8d91', fontSize: 10 },
        data: ['K线', 'MA20', 'MA50', 'MA120'],
      },
      grid: [
        { left: 42, right: 16, top: 26, height: 196 },
        { left: 42, right: 16, top: 238, height: 58 },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: '#30363d' } },
          axisLabel: { color: '#8a8d91', fontSize: 10,
            formatter: (value: string) => formatChartAxisTime(value, period) },
          min: 'dataMin',
          max: 'dataMax',
        },
        {
          type: 'category',
          gridIndex: 1,
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: '#30363d' } },
          axisLabel: { show: false },
          axisTick: { show: false },
          min: 'dataMin',
          max: 'dataMax',
        },
      ],
      yAxis: [
        {
          scale: true,
          splitLine: { lineStyle: { color: 'rgba(48,54,61,0.55)' } },
          axisLabel: { color: '#8a8d91', fontSize: 10 },
        },
        {
          scale: true,
          gridIndex: 1,
          splitLine: { show: false },
          splitNumber: 3,
          axisLabel: {
            color: '#8a8d91',
            fontSize: 9,
            margin: 4,
            formatter: formatAxisAmount,
            hideOverlap: true,
          },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 42, end: 100 },
      ],
      series: [
        {
          name: 'K线',
          type: 'candlestick',
          data: candleData,
          itemStyle: {
            color: '#ff4f6d',
            color0: '#28e070',
            borderColor: '#ff4f6d',
            borderColor0: '#28e070',
          },
        },
        {
          name: 'MA20',
          type: 'line',
          data: klineItems.map((item) => item.ma20),
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.4, color: '#f59e0b' },
        },
        {
          name: 'MA50',
          type: 'line',
          data: klineItems.map((item) => item.ma50),
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.4, color: '#60a5fa' },
        },
        {
          name: 'MA120',
          type: 'line',
          data: klineItems.map((item) => item.ma120),
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.4, color: '#a78bfa' },
        },
        {
          name: '成交额',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: amounts,
          itemStyle: { color: 'rgba(99,102,241,0.55)' },
        },
      ],
    };
  }, [klineItems, period]);

  const aboveCount = [latestAboveMa20, latestAboveMa50, latestAboveMa120].filter(Boolean).length;
  const latestTrendState = aboveCount >= 2 ? 'strong' : aboveCount === 1 ? 'neutral' : 'weak';
  const trend = trendConfig[minutePeriod ? selectedIndex?.trendState ?? 'weak' : latestTrendState];

  return (
    <section className="dashboard-panel min-h-[420px]">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>02</span>
          <div>
            <h2>指数趋势</h2>
            <p>关键均线与成交额</p>
          </div>
        </div>
      </div>

      <div className="dashboard-panel-body gap-3">
        <div className="dashboard-tabs overflow-x-auto">
          {indices.map((item) => (
            <button
              key={item.indexCode}
              type="button"
              onClick={() => setSelectedCode(item.indexCode)}
              className={item.indexCode === effectiveSelectedCode ? 'is-active' : ''}
            >
              {item.indexName}
            </button>
          ))}
        </div>

        <div className="dashboard-tabs overflow-x-auto" aria-label="指数K线周期">
          {CHART_PERIODS.filter((item) => item.key === 'daily' || isMinutePeriod(item.key))
            .map((item) => <button key={item.key} type="button"
              aria-pressed={period === item.key} onClick={() => setPeriod(item.key)}
              className={period === item.key ? 'is-active' : ''}>{item.label}</button>)}
        </div>
        {minutePeriod && <p className="text-[10px] text-text-tertiary">
          {klineQuery.data?.source_label || klineQuery.data?.source || '通达信指数分钟行情'} · 未复权
          {' · 点位、涨跌幅与成交额为当前分钟柱；趋势及20/50日收益保留日线摘要；MA以分钟根数计算'}
        </p>}

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
          <div>
            <span className="dashboard-eyebrow">点位</span>
            <strong className="block text-lg font-black text-text-primary font-mono">
              {formatNumber(displayClose)}
            </strong>
          </div>
          <div>
            <span className="dashboard-eyebrow">{minutePeriod ? '分钟柱涨跌幅' : '涨跌幅'}</span>
            <strong className="block text-lg font-black font-mono" style={{ color: pctColor(displayPctChange) }}>
              {(displayPctChange ?? 0) >= 0 ? '+' : ''}{formatNumber(displayPctChange)}%
            </strong>
          </div>
          <div>
            <span className="dashboard-eyebrow">{minutePeriod ? '日线趋势' : '趋势'}</span>
            <strong className="block text-lg font-black" style={{ color: trend.color }}>{trend.label}</strong>
          </div>
          <div>
            <span className="dashboard-eyebrow">20日</span>
            <strong className="block text-lg font-black font-mono" style={{ color: pctColor(displayReturn20d) }}>
              {formatNumber(displayReturn20d)}%
            </strong>
          </div>
          <div>
            <span className="dashboard-eyebrow">50日</span>
            <strong className="block text-lg font-black font-mono" style={{ color: pctColor(displayReturn50d) }}>
              {formatNumber(displayReturn50d)}%
            </strong>
          </div>
          <div>
            <span className="dashboard-eyebrow">成交额</span>
            <strong className="block text-lg font-black text-text-primary font-mono">
              {formatAmount(displayAmount)}
            </strong>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          {[
            ['MA20', latestAboveMa20],
            ['MA50', latestAboveMa50],
            ['MA120', latestAboveMa120],
          ].map(([label, above]) => (
            <span
              key={label as string}
              className={`dashboard-pill ${above ? 'is-green' : 'is-red'}`}
            >
              {label}{minutePeriod ? '根' : ''} {minutePeriod && !latestKline ? '暂无' : above ? '站上' : '未站上'}
            </span>
          ))}
        </div>

        <div className="min-h-[310px]">
          {klineQuery.isLoading ? (
            <div className="dashboard-empty h-[300px]">指数K线加载中</div>
          ) : klineQuery.isError ? (
            <div className="dashboard-empty flex h-[300px] flex-col items-center justify-center gap-2">
              <span>{chartPeriodLabel(period)}指数K线读取失败，请切换周期或重试</span>
              <button type="button" disabled={klineQuery.isFetching}
                onClick={() => void klineQuery.refetch()}
                className="rounded border border-border-primary px-3 py-1 disabled:opacity-40">
                {klineQuery.isFetching ? '重试中…' : '重试'}
              </button>
            </div>
          ) : klineItems.length ? (
            <ReactECharts option={option} style={{ height: 310, width: '100%' }} notMerge lazyUpdate />
          ) : (
            <div className="dashboard-empty flex h-[300px] flex-col items-center justify-center gap-2">
              <span>{klineQuery.data?.message || `暂无${chartPeriodLabel(period)}指数K线，请切换其它周期`}</span>
              <button type="button" disabled={klineQuery.isFetching}
                onClick={() => void klineQuery.refetch()}
                className="rounded border border-border-primary px-3 py-1 disabled:opacity-40">
                {klineQuery.isFetching ? '重试中…' : '重试'}
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
