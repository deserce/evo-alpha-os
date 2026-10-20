'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { clsx } from 'clsx';
import { quantApi } from '@/lib/api';
import {
  CHART_PERIODS, chartPeriodLabel, formatChartAxisTime, isMinutePeriod, type ChartPeriod,
} from '@/lib/chartPeriods';

type IndicatorKey = 'volume' | 'amount' | 'macd' | 'kdj' | 'rps';
type PriceStyle = 'candle' | 'ohlc';
type DataMode = 'local' | 'composite' | 'vendor';
type NullableNumber = number | null;

interface SectorChartProps {
  sectorName: string;
  tradeDate?: string;
  limit?: number;
  mode?: DataMode;
}

interface SectorChartData {
  source_label?: string;
  adjustment?: string;
  mode?: DataMode;
  price_kind?: 'net_value' | 'ohlc';
  source?: string;
  method_version?: string | null;
  ready?: boolean;
  status?: string;
  message?: string;
  dates?: string[];
  net_value?: NullableNumber[];
  kline?: NullableNumber[][];
  volume?: NullableNumber[];
  amount?: NullableNumber[];
  pct_chg?: NullableNumber[];
  macd?: {
    diff?: NullableNumber[];
    dea?: NullableNumber[];
    hist?: NullableNumber[];
  };
  kdj?: {
    k?: NullableNumber[];
    d?: NullableNumber[];
    j?: NullableNumber[];
  };
  rps_short?: {
    r5?: NullableNumber[];
    r10?: NullableNumber[];
    r20?: NullableNumber[];
  };
  rps_long?: {
    r50?: NullableNumber[];
    r120?: NullableNumber[];
    r250?: NullableNumber[];
  };
  quality_summary?: {
    rows?: number;
    synthetic_rows?: number;
    real_rows?: number;
    sources?: Record<string, number>;
    quality_flags?: Record<string, number>;
  };
  rows?: Array<Record<string, unknown>>;
}

const PRICE_STYLES: Array<{ key: PriceStyle; label: string }> = [
  { key: 'candle', label: '蜡烛' },
  { key: 'ohlc', label: '美国' },
];

const INDICATORS: Array<{ key: IndicatorKey; label: string; short: string }> = [
  { key: 'volume', label: '成交量', short: 'VOL' },
  { key: 'amount', label: '成交额', short: 'AMT' },
  { key: 'macd', label: 'MACD', short: 'MACD' },
  { key: 'kdj', label: 'KDJ', short: 'KDJ' },
  { key: 'rps', label: 'RPS', short: 'RPS' },
];

function toNumber(value: unknown): NullableNumber {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function series(values?: NullableNumber[]) {
  return asArray<NullableNumber>(values).map((value) => toNumber(value));
}

function calculateMA(dayCount: number, candles: NullableNumber[][]) {
  return candles.map((_, index) => {
    if (index < dayCount - 1) return null;
    const closes = candles
      .slice(index - dayCount + 1, index + 1)
      .map((item) => toNumber(item[1]));
    const validCloses = closes.filter((value): value is number => value !== null);
    if (validCloses.length !== dayCount) return null;
    const total = validCloses.reduce((sum, value) => sum + value, 0);
    return Number((total / dayCount).toFixed(3));
  });
}

function calculateLineMA(dayCount: number, values: NullableNumber[]) {
  return values.map((_, index) => {
    if (index < dayCount - 1) return null;
    const window = values.slice(index - dayCount + 1, index + 1);
    const validValues = window
      .map((value) => toNumber(value))
      .filter((value): value is number => value !== null);
    if (validValues.length !== dayCount) return null;
    const total = validValues.reduce((sum, value) => sum + value, 0);
    return Number((total / dayCount).toFixed(3));
  });
}

function changeColor(changes: NullableNumber[], index: number) {
  const value = toNumber(changes[index]);
  if (value === null) return '#8ea0bd';
  return value >= 0 ? '#ff4f6d' : '#28e070';
}

function formatPrice(value: unknown, digits = 2) {
  const parsed = toNumber(value);
  if (parsed === null) return '-';
  return parsed.toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function formatCompact(value: unknown) {
  const parsed = toNumber(value);
  if (parsed === null) return '-';
  const abs = Math.abs(parsed);
  if (abs >= 100000000) return `${(parsed / 100000000).toFixed(abs >= 1000000000 ? 0 : 1)}亿`;
  if (abs >= 10000) return `${(parsed / 10000).toFixed(abs >= 1000000 ? 0 : 1)}万`;
  return parsed.toFixed(0);
}

function formatAxisNumber(value: unknown) {
  const parsed = toNumber(value);
  if (parsed === null) return '-';
  const abs = Math.abs(parsed);
  if (abs >= 1000) return parsed.toFixed(0);
  if (abs >= 100) return parsed.toFixed(0);
  if (abs >= 10) return parsed.toFixed(1);
  return parsed.toFixed(2);
}

function axisPct(value: unknown) {
  const parsed = toNumber(value);
  return parsed === null ? '-' : `${parsed.toFixed(0)}%`;
}

function candleColor(candles: NullableNumber[][], index: number) {
  const candle = candles[index] || [];
  const open = toNumber(candle[0]);
  const close = toNumber(candle[1]);
  if (open === null || close === null) return '#8ea0bd';
  return close >= open ? '#ff4f6d' : '#28e070';
}

function ohlcData(candles: NullableNumber[][]) {
  return candles.map((item, index) => [
    index,
    toNumber(item[0]),
    toNumber(item[1]),
    toNumber(item[2]),
    toNumber(item[3]),
  ]);
}

function kdjAxisMin(value: { min: number }) {
  return Math.min(0, Math.floor((value.min - 5) / 10) * 10);
}

function kdjAxisMax(value: { max: number }) {
  return Math.max(100, Math.ceil((value.max + 5) / 10) * 10);
}

export default function SectorChart({ sectorName, tradeDate, limit = 260, mode }: SectorChartProps) {
  const [preferredDataMode, setDataMode] = useState<DataMode>(mode ?? 'local');
  const [period, setPeriod] = useState<ChartPeriod>('daily');
  const minutePeriod = isMinutePeriod(period);
  const dataMode = minutePeriod ? 'vendor' : preferredDataMode;
  const [priceStyle, setPriceStyle] = useState<PriceStyle>('ohlc');
  const [indicators, setIndicators] = useState<Record<IndicatorKey, boolean>>({
    volume: true,
    amount: true,
    macd: true,
    kdj: true,
    rps: true,
  });

  const chartQuery = useQuery({
    queryKey: ['sectorChart', sectorName, tradeDate || 'latest', limit, dataMode, period],
    queryFn: () => quantApi.getSectorChart({ sectorName, tradeDate, limit, mode: dataMode, period }),
    enabled: Boolean(sectorName),
    staleTime: minutePeriod ? 120000 : 30000,
    gcTime: 300000,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const data = chartQuery.data as SectorChartData | undefined;
  const dates = asArray<string>(data?.dates);
  const candles = asArray<NullableNumber[]>(data?.kline);
  const netValues = series(data?.net_value);
  const visibleIndicators = INDICATORS.filter(
    (item) => indicators[item.key] && (dataMode !== 'composite' || item.key !== 'kdj')
      && (!minutePeriod || item.key !== 'rps'),
  );

  const option = useMemo<EChartsOption>(() => {
    const activeCount = visibleIndicators.length;
    const grid: Array<Record<string, unknown>> = [];
    const xAxis: Array<Record<string, unknown>> = [];
    const yAxis: Array<Record<string, unknown>> = [];
    const titles: Array<Record<string, unknown>> = [];
    const chartSeries: Array<Record<string, unknown>> = [];
    const xAxisIndexes: number[] = [];
    const topStart = 2;
    const bottom = 5;
    const gap = 1.4;
    const mainHeight = activeCount >= 5 ? 40 : activeCount >= 3 ? 50 : 60;
    const subHeight = activeCount
      ? Math.max(7, (100 - topStart - bottom - mainHeight - gap * activeCount) / activeCount)
      : 0;
    let top = topStart;
    let gridIndex = 0;

    const addGrid = (
      title: string,
      height: number,
      yAxisConfig: Record<string, unknown>,
      showXAxisLabel = false,
    ) => {
      const axisConfig = { ...yAxisConfig };
      const axisLabelConfig = (axisConfig.axisLabel ?? {}) as Record<string, unknown>;
      delete axisConfig.axisLabel;

      grid.push({
        left: 44,
        right: 46,
        top: `${top}%`,
        height: `${height}%`,
        containLabel: false,
      });
      xAxis.push({
        type: 'category',
        data: dates,
        gridIndex,
        boundaryGap: true,
        axisLine: { show: showXAxisLabel, lineStyle: { color: '#27303a' } },
        axisTick: { show: false },
        axisLabel: {
          show: showXAxisLabel,
          color: '#8ea0bd',
          fontSize: 9,
          fontFamily: 'monospace',
          margin: 8,
        },
        axisPointer: { label: { backgroundColor: '#111827' } },
      });
      yAxis.push({
        type: 'value',
        scale: true,
        position: 'right',
        gridIndex,
        splitNumber: title === 'K线' ? 4 : 2,
        splitLine: { lineStyle: { color: '#1d2630', type: 'dashed' } },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: '#8ea0bd',
          fontSize: 9,
          fontFamily: 'monospace',
          margin: 3,
          hideOverlap: true,
          showMinLabel: false,
          formatter: formatAxisNumber,
          ...axisLabelConfig,
        },
        ...axisConfig,
      });
      titles.push({
        text: title,
        left: 10,
        top: `${top + 0.2}%`,
        textStyle: {
          color: '#8ea0bd',
          fontSize: 9,
          fontFamily: 'monospace',
          fontWeight: 700,
        },
      });
      xAxisIndexes.push(gridIndex);
      const current = gridIndex;
      gridIndex += 1;
      top += height + gap;
      return current;
    };

    const chartCandles = dataMode === 'composite'
      ? netValues.map((value) => [null, value, null, null])
      : candles;
    const mainGrid = addGrid(dataMode === 'composite' ? '等权净值' : 'K线', mainHeight, {
      axisLabel: {
        color: '#8ea0bd',
        fontSize: 9,
        fontFamily: 'monospace',
        formatter: (value: number) => formatPrice(value),
      },
    });

    if (dataMode === 'composite') {
      chartSeries.push({
        name: 'EvoAlpha 等权净值',
        type: 'line',
        data: netValues,
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        symbol: 'none',
        smooth: false,
        lineStyle: { width: 1.6, color: '#56d8ff' },
        areaStyle: { color: 'rgba(86, 216, 255, 0.08)' },
      });
    } else if (priceStyle === 'ohlc') {
      chartSeries.push({
        name: '美国线',
        type: 'custom',
        data: ohlcData(chartCandles),
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        encode: { x: 0, y: [1, 2, 3, 4] },
        renderItem: (
          _params: { dataIndex: number },
          api: {
            value: (dimension: number) => number;
            coord: (data: [number, number]) => number[];
            size: (data: [number, number]) => number[];
          },
        ) => {
          const xValue = toNumber(api.value(0));
          const open = toNumber(api.value(1));
          const close = toNumber(api.value(2));
          const low = toNumber(api.value(3));
          const high = toNumber(api.value(4));
          if (xValue === null || open === null || close === null || low === null || high === null) return null;
          const x = api.coord([xValue, open])[0];
          const yOpen = api.coord([xValue, open])[1];
          const yClose = api.coord([xValue, close])[1];
          const yLow = api.coord([xValue, low])[1];
          const yHigh = api.coord([xValue, high])[1];
          const halfWidth = Math.max(3, Math.min(8, api.size([1, 0])[0] * 0.36));
          const color = close >= open ? '#ff4f6d' : '#28e070';
          const lineStyle = { stroke: color, lineWidth: 1.25 };
          return {
            type: 'group',
            children: [
              { type: 'line', shape: { x1: x, y1: yHigh, x2: x, y2: yLow }, style: lineStyle },
              { type: 'line', shape: { x1: x - halfWidth, y1: yOpen, x2: x, y2: yOpen }, style: lineStyle },
              { type: 'line', shape: { x1: x, y1: yClose, x2: x + halfWidth, y2: yClose }, style: lineStyle },
            ],
          };
        },
      });
    } else {
      chartSeries.push({
        name: 'K线',
        type: 'candlestick',
        data: chartCandles,
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        itemStyle: {
          color: '#ff4f6d',
          color0: '#28e070',
          borderColor: '#ff4f6d',
          borderColor0: '#28e070',
        },
      });
    }

    chartSeries.push(
      {
        name: 'MA5',
        type: 'line',
        data: dataMode === 'composite' ? calculateLineMA(5, netValues) : calculateMA(5, chartCandles),
        smooth: true,
        symbol: 'none',
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        lineStyle: { width: 1, color: '#f5f7fb' },
      },
      {
        name: 'MA20',
        type: 'line',
        data: dataMode === 'composite' ? calculateLineMA(20, netValues) : calculateMA(20, chartCandles),
        smooth: true,
        symbol: 'none',
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        lineStyle: { width: 1, color: '#ffb020' },
      },
      {
        name: 'MA50',
        type: 'line',
        data: dataMode === 'composite' ? calculateLineMA(50, netValues) : calculateMA(50, chartCandles),
        smooth: true,
        symbol: 'none',
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        lineStyle: { width: 1, color: '#56d8ff' },
      },
    );

    const addSubChart = (
      title: string,
      yAxisConfig: Record<string, unknown>,
      subSeries: Array<Record<string, unknown>>,
    ) => {
      const current = addGrid(title, subHeight, yAxisConfig);
      subSeries.forEach((item) => {
        chartSeries.push({ ...item, xAxisIndex: current, yAxisIndex: current });
      });
    };

    if (indicators.volume) {
      addSubChart(
        '成交量',
        { axisLabel: { formatter: formatCompact } },
        [
          {
            name: '成交量',
            type: 'bar',
            data: series(data?.volume),
            barMaxWidth: 10,
            itemStyle: {
              color: (params: { dataIndex: number }) => dataMode === 'composite'
                ? changeColor(asArray<NullableNumber>(data?.pct_chg), params.dataIndex)
                : candleColor(chartCandles, params.dataIndex),
              opacity: 0.72,
            },
          },
        ],
      );
    }

    if (indicators.amount) {
      addSubChart(
        '成交额',
        { axisLabel: { formatter: formatCompact } },
        [
          {
            name: '成交额',
            type: 'bar',
            data: series(data?.amount),
            barMaxWidth: 10,
            itemStyle: {
              color: '#56d8ff',
              opacity: 0.58,
            },
          },
        ],
      );
    }

    if (indicators.macd) {
      addSubChart(
        'MACD',
        {},
        [
          {
            name: 'MACD',
            type: 'bar',
            data: series(data?.macd?.hist),
            barMaxWidth: 8,
            itemStyle: {
              color: (params: { value: number }) => params.value >= 0 ? '#ff4f6d' : '#28e070',
              opacity: 0.72,
            },
          },
          {
            name: 'DIF',
            type: 'line',
            data: series(data?.macd?.diff),
            symbol: 'none',
            lineStyle: { width: 1, color: '#f5f7fb' },
          },
          {
            name: 'DEA',
            type: 'line',
            data: series(data?.macd?.dea),
            symbol: 'none',
            lineStyle: { width: 1, color: '#ffb020' },
          },
        ],
      );
    }

    if (dataMode !== 'composite' && indicators.kdj) {
      addSubChart(
        'KDJ',
        {
          min: kdjAxisMin,
          max: kdjAxisMax,
          boundaryGap: ['8%', '8%'],
        },
        [
          {
            name: 'K',
            type: 'line',
            data: series(data?.kdj?.k),
            symbol: 'none',
            lineStyle: { width: 1, color: '#f5f7fb' },
          },
          {
            name: 'D',
            type: 'line',
            data: series(data?.kdj?.d),
            symbol: 'none',
            lineStyle: { width: 1, color: '#ffb020' },
          },
          {
            name: 'J',
            type: 'line',
            data: series(data?.kdj?.j),
            symbol: 'none',
            lineStyle: { width: 1, color: '#ff4f6d' },
            markLine: {
              silent: true,
              symbol: 'none',
              data: [{ yAxis: 20 }, { yAxis: 80 }],
              lineStyle: { color: '#65738a', type: 'dashed', width: 1, opacity: 0.5 },
              label: { show: false },
            },
          },
        ],
      );
    }

    if (!minutePeriod && indicators.rps) {
      addSubChart(
        'RPS',
        {
          min: 0,
          max: 100,
          axisLabel: { formatter: axisPct },
        },
        [
          {
            name: 'RPS5',
            type: 'line',
            data: series(data?.rps_short?.r5),
            symbol: 'none',
            lineStyle: { width: 1, color: '#a78bfa' },
          },
          {
            name: 'RPS10',
            type: 'line',
            data: series(data?.rps_short?.r10),
            symbol: 'none',
            lineStyle: { width: 1, color: '#c4b5fd' },
          },
          {
            name: 'RPS20',
            type: 'line',
            data: series(data?.rps_short?.r20),
            symbol: 'none',
            lineStyle: { width: 2, color: '#ffb020' },
          },
          {
            name: 'RPS50',
            type: 'line',
            data: series(data?.rps_long?.r50),
            symbol: 'none',
            lineStyle: { width: 1, color: '#56d8ff' },
          },
          {
            name: 'RPS120',
            type: 'line',
            data: series(data?.rps_long?.r120),
            symbol: 'none',
            lineStyle: { width: 1, color: '#34d399' },
          },
          {
            name: 'RPS250',
            type: 'line',
            data: series(data?.rps_long?.r250),
            symbol: 'none',
            lineStyle: { width: 1, color: '#f472b6' },
            markLine: {
              silent: true,
              symbol: 'none',
              data: [{ yAxis: 90 }],
              lineStyle: { color: '#ff4f6d', type: 'dashed', width: 1 },
              label: { color: '#ff4f6d', formatter: '90' },
            },
          },
        ],
      );
    }

    if (xAxis.length) {
      const lastAxis = xAxis[xAxis.length - 1];
      lastAxis.axisLine = { show: true, lineStyle: { color: '#27303a' } };
      lastAxis.axisLabel = {
        show: true,
        color: '#8ea0bd',
        fontSize: 10,
        fontFamily: 'monospace',
        margin: 5,
        formatter: (value: string) => formatChartAxisTime(value, period),
      };
    }

    return {
      backgroundColor: '#080a0d',
      animation: false,
      title: titles,
      legend: { show: false },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] },
        confine: true,
        backgroundColor: 'rgba(8, 10, 13, 0.96)',
        borderColor: '#27303a',
        borderWidth: 1,
        textStyle: { color: '#d7dde8', fontSize: 11, fontFamily: 'monospace' },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: xAxisIndexes,
          start: 45,
          end: 100,
          zoomOnMouseWheel: false,
          moveOnMouseWheel: false,
          moveOnMouseMove: true,
        },
        {
          type: 'slider',
          xAxisIndex: xAxisIndexes,
          bottom: 0,
          height: 10,
          borderColor: 'transparent',
          backgroundColor: '#101820',
          fillerColor: 'rgba(86, 216, 255, 0.12)',
          handleStyle: { color: '#56d8ff' },
          moveHandleStyle: { color: '#56d8ff' },
          textStyle: { color: '#8ea0bd', fontSize: 9 },
        },
      ],
      grid,
      xAxis,
      yAxis,
      series: chartSeries,
    };
  }, [candles, data, dataMode, dates, indicators, netValues, priceStyle, visibleIndicators,
    minutePeriod, period]);

  const toggleIndicator = (key: IndicatorKey) => {
    setIndicators((previous) => ({ ...previous, [key]: !previous[key] }));
  };

  const quality = data?.quality_summary;
  const syntheticRows = toNumber(quality?.synthetic_rows);
  const qualityRows = toNumber(quality?.rows);
  const hasPriceData = dataMode === 'composite' ? netValues.length > 0 : candles.length > 0;
  const latestRow = data?.rows?.length ? data.rows[data.rows.length - 1] : undefined;
  const latestCoverage = toNumber(latestRow?.coverage_ratio);
  const dataModeLabel = dataMode === 'local'
    ? '本地等权K线'
    : dataMode === 'composite'
      ? '等权净值'
      : '东财 BK';

  const periodControls = (
    <div className="flex shrink-0 items-center gap-1">
      {CHART_PERIODS.filter((item) => item.key === 'daily' || isMinutePeriod(item.key))
        .map((item) => <button key={item.key} type="button"
        aria-pressed={period === item.key} onClick={() => setPeriod(item.key)}
        className={clsx('h-5 rounded border px-1.5 text-[10px] font-bold',
          period === item.key ? 'border-brand-blue bg-brand-blue/15 text-brand-blue-light'
            : 'border-border-primary text-text-secondary')}>
        {item.label}
      </button>)}
    </div>
  );

  const modeControls = (
    <div className="flex shrink-0 items-center gap-1 border-l border-border-secondary pl-1">
      {[
        { key: 'local' as DataMode, label: '本地等权K线' },
        { key: 'vendor' as DataMode, label: '东财BK' },
      ].map((item) => (
        <button
          key={item.key}
          type="button"
          disabled={minutePeriod}
          title={minutePeriod ? '分钟板块使用东财 BK 原始行情，非本地等权合成' : item.label}
          onClick={() => setDataMode(item.key)}
          className={clsx(
            'h-5 rounded border px-1.5 text-[10px] font-black transition-colors disabled:opacity-40',
            dataMode === item.key
              ? 'border-brand-blue bg-brand-blue/15 text-brand-blue-light'
              : 'border-border-primary bg-bg-secondary text-text-secondary hover:border-brand-blue/50 hover:text-white',
          )}
        >
          {item.label}
        </button>
      ))}
    </div>
  );

  const priceStyleControls = dataMode !== 'composite' ? (
    <div className="flex shrink-0 items-center gap-1">
      {PRICE_STYLES.map((item) => (
        <button
          key={item.key}
          type="button"
          onClick={() => setPriceStyle(item.key)}
          className={clsx(
            'h-5 rounded border px-1.5 text-[10px] font-black transition-colors',
            priceStyle === item.key
              ? 'border-brand-blue bg-brand-blue/15 text-brand-blue-light'
              : 'border-border-primary bg-bg-secondary text-text-secondary hover:border-brand-blue/50 hover:text-white',
          )}
        >
          {item.label}
        </button>
      ))}
    </div>
  ) : null;

  if (chartQuery.isLoading || chartQuery.isError || !dates.length || !hasPriceData) {
    return (
      <div className="flex h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border border-border-secondary bg-[#080a0d]">
        <div className="flex flex-wrap items-center gap-1 border-b border-[#1e2226] bg-[#0b0f14] px-1.5 py-1">
          <div className="min-w-0 truncate px-1 text-[10px] font-black text-text-secondary">
            {sectorName} · {dataModeLabel}
          </div>
          {periodControls}
          {modeControls}
          {priceStyleControls}
        </div>
        {minutePeriod && <div className="px-2 py-1 text-[10px] text-text-tertiary">
          东财 BK 原始分钟K线（非本地等权合成） · 未复权 · 不提供日RPS
        </div>}
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-4 text-center text-xs text-text-secondary">
          <span>{chartQuery.isLoading ? `正在读取${chartPeriodLabel(period)}板块数据…`
            : chartQuery.isError ? `${chartPeriodLabel(period)}读取失败，请切换周期或重试`
            : data?.message || (
            dataMode === 'vendor'
              ? '东财 BK 当前不可用，未使用旧数据拼接'
              : dataMode === 'local'
                ? '暂无本地等权板块K线数据'
                : '暂无等权板块净值数据'
          )}</span>
          {!chartQuery.isLoading && <button type="button" disabled={chartQuery.isFetching}
            onClick={() => void chartQuery.refetch()}
            className="rounded border border-border-primary px-3 py-1 disabled:opacity-40">
            {chartQuery.isFetching ? '重试中…' : '重试'}
          </button>}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border border-border-secondary bg-[#080a0d]">
      <div className="flex flex-wrap items-center justify-between gap-1 border-b border-[#1e2226] bg-[#0b0f14] px-1.5 py-1">
        <div className="flex min-w-0 items-center gap-1">
          <div className="min-w-0 truncate px-1 text-[10px] font-black text-text-secondary">
            {sectorName} · {dataModeLabel}
          </div>
          <span
            className="shrink-0 rounded border border-border-secondary px-1.5 py-0.5 text-[9px] font-mono text-text-tertiary"
            title={dataMode === 'composite'
              ? '成交量统一为股数，成交额统一为元；兼容净值模式不提供OHLC'
              : dataMode === 'vendor' ? '东财 BK 原始行情，成交量为股数，成交额为元'
                : '成交量统一为股数，成交额统一为元；开高低收来自同一成分集的等权合成'}
          >
            VOL:股
          </span>
          <span className="shrink-0 rounded border border-border-secondary px-1.5 py-0.5 text-[9px] font-mono text-text-tertiary">
            {dataMode === 'local'
              ? 'EvoAlpha 等权K线'
              : dataMode === 'composite'
                ? 'EvoAlpha 等权净值'
                : '东财 BK'}
          </span>
          {data?.method_version ? (
            <span className="shrink-0 rounded border border-border-secondary px-1.5 py-0.5 text-[9px] font-mono text-text-tertiary">
              {data.method_version}
            </span>
          ) : null}
          {latestCoverage !== null ? (
            <span className="shrink-0 rounded border border-border-secondary px-1.5 py-0.5 text-[9px] font-mono text-text-tertiary">
              覆盖 {(latestCoverage * 100).toFixed(0)}%
            </span>
          ) : null}
          {dataMode === 'vendor' && syntheticRows !== null && syntheticRows > 0 ? (
            <span
              className="shrink-0 rounded border border-warning/45 bg-warning/10 px-1.5 py-0.5 text-[9px] font-mono text-warning"
              title="供应商轨出现非真实行，当前数据不应进入发布"
            >
              异常 {syntheticRows}/{qualityRows ?? '-'}
            </span>
          ) : null}
          {periodControls}
          {modeControls}
          {priceStyleControls}
        </div>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-1">
          {INDICATORS.map((item) => (
            <button
              key={item.key}
              type="button"
              disabled={minutePeriod && item.key === 'rps'}
              title={item.label}
              onClick={() => toggleIndicator(item.key)}
              className={clsx(
                'h-5 rounded border px-1.5 text-[10px] font-black transition-colors disabled:opacity-40',
                indicators[item.key]
                  ? 'border-brand-blue bg-brand-blue/15 text-brand-blue-light'
                  : 'border-border-primary bg-bg-secondary text-text-tertiary hover:border-text-tertiary hover:text-text-primary',
              )}
            >
              {item.short}
            </button>
          ))}
        </div>
      </div>

      {minutePeriod && <div className="border-b border-[#1e2226] px-2 py-1 text-[10px] text-text-tertiary">
        {data?.source_label || '东财 BK 原始分钟K线（非本地等权合成）'} · 未复权
        {' · 均线、MACD/KDJ按分钟K线计算；不提供日RPS'}
      </div>}

      <div className="relative min-h-0 flex-1">
        <ReactECharts
          option={option}
          style={{ height: '100%', width: '100%', position: 'absolute' }}
          notMerge
          lazyUpdate
          opts={{ renderer: 'canvas' }}
        />
      </div>
    </div>
  );
}
