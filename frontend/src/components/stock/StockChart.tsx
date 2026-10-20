'use client';

import ReactECharts from 'echarts-for-react';
import { buildStructureHintPriceContext } from './strategyHintPriceContext';
import { StockHintAxisPrices } from './StockHintAxisPrices';
import { buildHintPriceOverlay, type HintPriceContext } from './stockChartHintPrices';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { stockApi, watchApi } from '@/lib/api';
import {
  CHART_PERIODS, chartPeriodLabel, formatChartAxisTime, isMinutePeriod, type ChartPeriod,
} from '@/lib/chartPeriods';
import { useEffect, useMemo, useRef, useState } from 'react';
import { clsx } from 'clsx';
import { buildStockChartTooltip, resolveTooltipIndex } from './stockChartTooltip';
import {
  buildWatchChartPoints,
  type WatchMarkerRow,
} from './stockChartWatchMarkers';
import {
  buildStructurePoints, signalTooltip, STRUCTURE_COLORS, isSignalVisible, structureVersionLabel,
  DEFAULT_STRATEGY_VISIBILITY,
  type StrategyVisibility,
  type StructureCandidatePayload,
} from './structureCandidatePresentation';

import ChartSettingsDisclosure from './ChartSettingsDisclosure';
import { buildStockTimeAxisLayout, formatStockTimeTick } from './stockChartTimeAxis';
import StructureStrategyPanel from './StructureStrategyPanel';
import type { ChanHistory, ChanRevokedSignal } from './stockChartChanHistory';
import { buildChanOverlay, buildChanBuySellPoints, CHAN_OVERLAY_NOTICE, type ChanZone } from './stockChartChanOverlay';
import { chanPointLabel, chanPointExplanation, type ChanTextMode } from './stockChartChanPresentation';
import { buildChanZoneEdges } from './stockChartChanZoneGeometry';

type IndicatorKey = 'volume' | 'amount' | 'macd' | 'kdj' | 'rps' | 'fund' | 'north';
type PriceStyle = 'candle' | 'ohlc';
type NullableNumber = number | null;

interface StockChartProps {
  symbol: string;
  endDate?: string;
  limit?: number;
  hintContext?: HintPriceContext;
}

interface ChartRow {
  trade_date?: string;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close?: number | null;
  volume?: number | null;
  amount?: number | null;
  pct_chg?: number | null;
  turnover_rate?: number | null;
  rps_5?: number | null;
  rps_10?: number | null;
  rps_20?: number | null;
  rps_50?: number | null;
  rps_120?: number | null;
  rps_250?: number | null;
  fund_report_date?: string | null;
  fund_count?: number | null;
  fund_hold_ratio?: number | null;
  fund_hold_value?: number | null;
  northbound_hold_ratio?: number | null;
}

interface ChartData {
  source_label?: string;
  source?: string;
  adjustment?: string;
  status?: string;
  message?: string;
  dates?: string[];
  kline?: NullableNumber[][];
  volume?: NullableNumber[];
  amount?: NullableNumber[];
  rows?: ChartRow[];
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
  funds?: {
    fund?: NullableNumber[];
    north?: NullableNumber[];
    fund_count?: NullableNumber[];
  };
}

interface PeriodSignalPayload {
  period: ChartPeriod;
  rows?: WatchMarkerRow[];
  structure?: StructureCandidatePayload;
  rule_version?: number;
  rules_source?: string;
  blocked?: Array<{ trade_date: string; context_date?: string | null; reason: string }>;
  context_date?: string | null;
  message?: string;
}

interface WatchMarkerPayload {
  scan_id?: string | null;
  symbol?: string;
  rows?: WatchMarkerRow[];
  scan_coverage?: { start_date?: string; end_date?: string; rule_version?: number } | null;
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
  { key: 'fund', label: '基金持股比例', short: '基金' },
  { key: 'north', label: '北向持股比例', short: '北向' },
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

export default function StockChart({ symbol, endDate, limit = 260, hintContext }: StockChartProps) {
  const chartRef = useRef<ReactECharts>(null);
  const [chartReady, setChartReady] = useState(0);
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const [chartHeight, setChartHeight] = useState(600);
  const zoomRangeRef = useRef({ start: 45, end: 100 });
  const chartEvents = useMemo(() => ({
    datazoom: (event: { start?: number; end?: number; batch?: Array<{ start?: number; end?: number }> }) => {
      const range = event.batch?.[0] ?? event;
      if (typeof range.start === 'number' && typeof range.end === 'number'
        && Number.isFinite(range.start) && Number.isFinite(range.end)
        && range.start >= 0 && range.end <= 100 && range.start <= range.end) {
        zoomRangeRef.current = { start: range.start, end: range.end };
      }
    },
  }), []);
  const [period, setPeriod] = useState<ChartPeriod>('daily');
  const minutePeriod = isMinutePeriod(period);
  const [priceStyle, setPriceStyle] = useState<PriceStyle>('ohlc');
  const [showMovingAverages, setShowMovingAverages] = useState(false);
  const [showChanOverlay, setShowChanOverlay] = useState(true);
  const [chanVariant, setChanVariant] = useState<'zhihu' | 'xueqiu'>('xueqiu');
  const [chanTextMode, setChanTextMode] = useState<ChanTextMode>('original');
  const [showChanRevocations, setShowChanRevocations] = useState(false);
  const [showChanHistory, setShowChanHistory] = useState(true);
  const [showChanPivotLabels, setShowChanPivotLabels] = useState(true);
  const [chanHistoryResultZones, setChanHistoryZones] = useState<ChanZone[]>([]);
  const [chanRevokedSignals, setChanRevokedSignals] = useState<ChanRevokedSignal[]>([]);
  const [chanHistoryResultKey, setChanHistoryResultKey] = useState('');
  const [chanHistoryData, setChanHistoryData] = useState<ChartData | undefined>();

  const [chanHistoryStatus, setChanHistoryStatus] = useState('');
  const [strategyVisibility, setStrategyVisibility] = useState<StrategyVisibility>(
    { ...DEFAULT_STRATEGY_VISIBILITY, C: false },
  );
  const showStructure = strategyVisibility.C;
  const [indicators, setIndicators] = useState<Record<IndicatorKey, boolean>>({
    volume: true,
    amount: false,
    macd: true,
    kdj: true,
    rps: true,
    fund: false,
    north: false,
  });

  const chartQuery = useQuery({
    queryKey: ['stockChart', symbol, period, endDate, limit],
    queryFn: () => stockApi.getChartData(symbol, period, endDate, limit),
    enabled: Boolean(symbol),
    staleTime: minutePeriod ? 120000 : 30000,
    gcTime: 300000,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const data = chartQuery.data as ChartData | undefined;
  const chanHistoryKey = `${symbol}:${period}:${chanVariant}:${showChanHistory}:${showChanPivotLabels}:${showChanRevocations}`;
  const chanHistoryZones = useMemo(() => chanHistoryData === data && chanHistoryResultKey === chanHistoryKey ? chanHistoryResultZones : [],
    [chanHistoryData, data, chanHistoryResultZones, chanHistoryResultKey, chanHistoryKey]);
  const revokedSignals = useMemo(() => showChanOverlay && showChanRevocations
    && (chanVariant === 'zhihu' || showChanPivotLabels)
    && chanHistoryData === data && chanHistoryResultKey === chanHistoryKey ? chanRevokedSignals : [],
    [showChanOverlay, showChanRevocations, chanVariant, showChanPivotLabels, chanHistoryData, data, chanHistoryResultKey, chanHistoryKey, chanRevokedSignals]);
  const displayedChanHistoryStatus = chanHistoryData === data && chanHistoryResultKey === chanHistoryKey ? chanHistoryStatus : '正在回放…';
  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container) return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const height = Math.round(container.getBoundingClientRect().height);
        if (height > 0) setChartHeight(height);
        chartRef.current?.getEchartsInstance().resize();
      });
    });
    observer.observe(container);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [data, period]);
  const dates = useMemo(() => asArray<string>(data?.dates), [data?.dates]);
  const candles = useMemo(() => asArray<NullableNumber[]>(data?.kline), [data?.kline]);
  const chanOverlay = useMemo(
    () => showChanOverlay ? buildChanOverlay(dates, candles, chanVariant) : null,
    [showChanOverlay, dates, candles, chanVariant],
  );
  const chanDisplayedPoints = useMemo(
    () => chanOverlay && chanVariant === 'xueqiu' && showChanPivotLabels
      ? buildChanBuySellPoints(dates, candles) : chanOverlay?.points ?? [],
    [chanOverlay, chanVariant, showChanPivotLabels, dates, candles],
  );
  useEffect(() => {
    const includeZones = chanVariant === 'xueqiu' && showChanHistory;
    const auditSignals = showChanRevocations && (chanVariant === 'zhihu' || showChanPivotLabels);
    if (!showChanOverlay || (!includeZones && !auditSignals)
      || !chanOverlay?.barCount || dates.length !== candles.length) return;
    let worker: Worker | undefined;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let active = true;
    const stop = () => { if (timeout) clearTimeout(timeout); worker?.terminate(); };
    queueMicrotask(() => {
      if (!active) return;
      setChanHistoryData(data);
      setChanHistoryResultKey(chanHistoryKey);
      setChanRevokedSignals([]);
      setChanHistoryZones([]);
      setChanHistoryStatus('正在回放…');
    });
    try {
      worker = new Worker(new URL('./stockChartChanHistory.worker.ts', import.meta.url));
      worker.onmessage = (event: MessageEvent<{
        id: number; status: 'success' | 'error'; result?: ChanHistory; message?: string;
      }>) => {
        if (!active || event.data.id !== 1) return;
        stop();
        if (event.data.status === 'success' && event.data.result) {
          setChanRevokedSignals(event.data.result.revokedSignals ?? []);
          setChanHistoryZones(event.data.result.zones.filter((zone) => zone.endIndex !== dates.length - 1));
          setChanHistoryStatus('');
        } else {
          setChanHistoryZones([]);
          setChanHistoryStatus(event.data.message || '历史回放失败，请关闭后重新开启');
        }
      };
      worker.onerror = () => {
        if (!active) return;
        stop();
        setChanHistoryZones([]);
        setChanHistoryStatus('历史回放失败，请关闭后重新开启');
      };
      timeout = setTimeout(() => {
        if (!active) return;
        stop();
        setChanHistoryZones([]);
        setChanHistoryStatus('历史回放超时，请关闭后重新开启');
      }, 10000);
      worker.postMessage({ id: 1, dates, candles, variant: chanVariant, auditSignals, includeZones });
    } catch {
      stop();
      queueMicrotask(() => { if (active) setChanHistoryStatus('此环境无法启动历史回放，请关闭后重新开启'); });
    }
    return () => { active = false; stop(); };
  }, [showChanOverlay, chanVariant, showChanHistory, showChanRevocations, showChanPivotLabels, chanOverlay, dates, candles, symbol, period, data, chanHistoryKey]);
  const visibleKeys = INDICATORS.filter((item) => indicators[item.key]
    && (!minutePeriod || !['rps', 'fund', 'north'].includes(item.key))).map((item) => item.key);
  const periodSignalQuery = useQuery({
    queryKey: ['stockPeriodSignals', symbol, period, endDate, limit, dates[0], dates.at(-1)],
    queryFn: ({ signal }) => watchApi.getChartSignals({ symbol, period, endDate, limit }, signal),
    enabled: Boolean(period !== 'daily' && dates.length && symbol
      && (strategyVisibility.A || strategyVisibility.B || showStructure)),
    staleTime: 120_000,
    gcTime: 300_000,
    retry: false,
  });
  const periodPayload = periodSignalQuery.data as PeriodSignalPayload | undefined;
  const currentPeriodPayload = periodPayload?.period === period ? periodPayload : undefined;
  const markerQuery = useQuery({
    queryKey: ['watchChartMarkers', symbol, dates[0], dates[dates.length - 1]],
    queryFn: () => watchApi.getMarkers({
      symbol,
      startDate: dates[0],
      endDate: dates[dates.length - 1],
    }),
    enabled: Boolean(
      (strategyVisibility.A || strategyVisibility.B) && symbol && period === 'daily' && dates.length,
    ),
    staleTime: 60_000,
  });
  const markerPayload = (period === 'daily' ? markerQuery.data : currentPeriodPayload) as WatchMarkerPayload | undefined;
  const allWatchPoints = useMemo(
    () => buildWatchChartPoints(asArray<WatchMarkerRow>(markerPayload?.rows), dates, candles),
    [markerPayload?.rows, dates, candles],
  );
  const structureQuery = useQuery({
    queryKey: ['structureCandidate', symbol, dates[0], dates[dates.length - 1]],
    queryFn: () => watchApi.getStructureCandidate({
      symbol, startDate: dates[0], endDate: dates[dates.length - 1],
    }),
    enabled: Boolean(showStructure && period === 'daily' && symbol && dates.length),
    staleTime: 300_000,
    gcTime: 600_000,
    retry: false,
  });
  const structurePayload = (period === 'daily' ? structureQuery.data : currentPeriodPayload?.structure) as StructureCandidatePayload | undefined;
  const allStructurePoints = useMemo(
    () => showStructure
      ? buildStructurePoints(structurePayload?.markers ?? [], dates, candles) : [],
    [structurePayload?.markers, dates, candles, showStructure],
  );
  const watchPoints = allWatchPoints.filter((point) => isSignalVisible(strategyVisibility, point.pathCode, point.markerType));
  const structurePoints = allStructurePoints.filter((point) => isSignalVisible(strategyVisibility, 'C', point.marker_type));
  const zonePointCount = watchPoints.filter((point) => point.markerType === 'IN_ZONE').length;
  const triggerPointCount = watchPoints.filter((point) => point.markerType === 'TRIGGERED').length;
  const pathCounts = {
    A: {
      zone: watchPoints.filter((point) => point.pathCode === 'A' && point.markerType === 'IN_ZONE').length,
      trigger: watchPoints.filter((point) => point.pathCode === 'A' && point.markerType === 'TRIGGERED').length,
    },
    B: {
      zone: watchPoints.filter((point) => point.pathCode === 'B' && point.markerType === 'IN_ZONE').length,
      trigger: watchPoints.filter((point) => point.pathCode === 'B' && point.markerType === 'TRIGGERED').length,
    },
  };

  const hintPrices = useMemo(() => buildHintPriceOverlay(
    hintContext ?? (showStructure ? buildStructureHintPriceContext(structurePayload, symbol, endDate) : undefined),
    symbol, endDate, period, dates),
    [hintContext, showStructure, structurePayload, symbol, endDate, period, dates]);

  const option = useMemo<EChartsOption>(() => {
    const activeCount = visibleKeys.length;
    const grid: Array<Record<string, unknown>> = [];
    const xAxis: Array<Record<string, unknown>> = [];
    const yAxis: Array<Record<string, unknown>> = [];
    const chartTitles: Array<Record<string, unknown>> = [];
    const chartSeries: Array<Record<string, unknown>> = [];
    const xAxisIndexes: number[] = [];
    const chartTop = 2;
    const timeAxisLayout = buildStockTimeAxisLayout(chartHeight);
    const chartBottom = timeAxisLayout.bottomPercent;
    const gap = 1.4;
    const mainHeight = activeCount >= 5 ? 40 : activeCount >= 3 ? 50 : 60;
    const subHeight = activeCount
      ? Math.max(7, (100 - chartTop - chartBottom - mainHeight - gap * activeCount) / activeCount)
      : 0;
    let top = chartTop;
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
          formatter: (value: string) => formatChartAxisTime(value, period),
        },
        axisPointer: { label: { show: false, backgroundColor: '#111827' } },
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
      chartTitles.push({
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

    const mainGrid = addGrid('K线', mainHeight, {
      max: (value: { min: number; max: number }) =>
        value.max + Math.max((value.max - value.min) * 0.15, Math.abs(value.max) * 0.015),
      axisLabel: {
        color: '#8ea0bd',
        fontSize: 9,
        fontFamily: 'monospace',
        formatter: (value: number) => formatPrice(value),
      },
    });

    if (priceStyle === 'ohlc') {
      chartSeries.push({
        name: '美国线',
        type: 'custom',
        data: ohlcData(candles),
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
        data: candles,
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

    if (hintPrices.lines.length || hintPrices.points.length) chartSeries.push({
      name: '当日提示价位', type: 'line', data: [], symbol: 'none',
      xAxisIndex: mainGrid, yAxisIndex: mainGrid, z: 12,
      markLine: {
        silent: false, symbol: ['none', 'none'],
        lineStyle: { type: 'dashed', width: 1, opacity: 0.45 },
        label: { show: false },
        tooltip: { trigger: 'item', renderMode: 'richText', formatter: '{b}' },
        data: hintPrices.lines.map(line => ({
          name: `${line.label} ${line.price.toFixed(2)}`, yAxis: line.price,
          lineStyle: { color: line.color }, label: { color: line.color },
        })),
      },
      markPoint: {
        silent: false, symbol: 'circle', symbolSize: 6,
        label: { show: false },
        tooltip: { trigger: 'item', renderMode: 'richText', formatter: '{b}' },
        data: hintPrices.points.map(point => ({
          name: `${point.label} ${point.price.toFixed(2)}`,
          coord: [point.date, point.price], itemStyle: { color: point.color },
          label: { color: point.color },
        })),
      },
    });

    if (showMovingAverages) chartSeries.push(
      {
        name: 'MA5',
        type: 'line',
        data: calculateMA(5, candles),
        smooth: true,
        symbol: 'none',
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        lineStyle: { width: 1, color: '#f5f7fb' },
      },
      ...(chanOverlay && chanVariant === 'zhihu' ? [] : [{
        name: 'MA20',
        type: 'line',
        data: calculateMA(20, candles),
        smooth: true,
        symbol: 'none',
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        lineStyle: { width: 1, color: '#ffb020' },
      }]),
      {
        name: 'MA50',
        type: 'line',
        data: calculateMA(50, candles),
        smooth: true,
        symbol: 'none',
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        lineStyle: { width: 1, color: '#56d8ff' },
      },
    );

    if (chanOverlay) {
      chanOverlay.lines.filter((line) => showMovingAverages || !line.name.includes('MA20')).forEach((line) => chartSeries.push({
        name: line.name.startsWith('缠论') ? line.name : `缠论 · ${line.name}`,
        type: 'line', data: line.data,
        xAxisIndex: mainGrid, yAxisIndex: mainGrid, symbol: 'none',
        connectNulls: false, silent: true, z: 8,
        lineStyle: { width: line.width, color: line.color, type: line.lineType },
      }));
      const zoneGroups = [
        { name: '缠论当前参考区间', zones: chanOverlay.zones, historical: false },
        { name: '缠论历史中枢回放（扩展）', zones: chanVariant === 'xueqiu' && showChanHistory ? chanHistoryZones : [], historical: true },
      ];
      zoneGroups.forEach(({ name, zones, historical }) => {
      if (zones.length) {
        chartSeries.push({
          name, type: 'custom', silent: true, z: historical ? 10 : 11,
          xAxisIndex: mainGrid, yAxisIndex: mainGrid,
          data: zones.map((zone) => [
            zone.startIndex, zone.endIndex, zone.low, zone.high,
          ]),
          encode: { x: [0, 1], y: [2, 3] },
          renderItem: (
            params: { dataIndex: number; coordSys?: { x: number; y: number; width: number; height: number } },
            api: { value: (dimension: number) => number; coord: (data: [number, number]) => number[] },
          ) => {
            const zone = zones[params.dataIndex];
            const bounds = params.coordSys;
            if (!zone || !bounds) return null;
            const start = api.coord([api.value(0), api.value(3)]);
            const end = api.coord([api.value(1), api.value(2)]);
            const left = Math.max(bounds.x, start[0]);
            const right = Math.min(bounds.x + bounds.width, end[0]);
            const top = Math.max(bounds.y, start[1]);
            const bottom = Math.min(bounds.y + bounds.height, end[1]);
            const edges = buildChanZoneEdges([start[0], start[1]], [end[0], end[1]], bounds,
              zone.dashedTop, zone.dashedBottom);
            if (!edges.length) return null;
            return {
              type: 'group', children: [
                ...(chanVariant === 'xueqiu' && !historical && right > left && bottom > top
                  ? [{ type: 'rect', shape: { x: left, y: top, width: right - left, height: bottom - top },
                    style: { fill: zone.color, opacity: 0.04 } }] : []),
                ...edges.map((edge) => ({ type: 'line',
                  shape: { x1: edge.x1, y1: edge.y1, x2: edge.x2, y2: edge.y2 },
                  style: { stroke: zone.color, lineWidth: chanVariant === 'zhihu' || historical ? 1 : 1.6,
                    lineDash: edge.dotted ? chanVariant === 'zhihu' ? [1, 2] : [4, 3] : [] } })),
              ],
            };
          },
        });
      }
      });
      const chanPivots = chanDisplayedPoints.filter((point) => point.kind === 'pivot');
      if (chanPivots.length) chartSeries.push({
        name: '缠论极点', type: 'scatter', silent: true, z: 12,
        xAxisIndex: mainGrid, yAxisIndex: mainGrid,
        data: chanPivots.map((point) => ({
          value: [dates[point.index], point.price],
          name: chanPointLabel(point, chanTextMode) || (point.direction === 'low' ? '低点' : '高点'),
          label: { position: point.direction === 'low' ? 'bottom' : 'top' },
        })),
        symbol: 'emptyCircle', symbolSize: 5,
        itemStyle: { color: '#ff80ff' },
        label: { show: chanVariant === 'zhihu' || showChanPivotLabels, formatter: '{b}',
          color: chanVariant === 'xueqiu' ? '#ff80ff' : '#ffffff', fontSize: 8, distance: 3 },
      });
      const chanSignals = chanDisplayedPoints.filter((point) => point.kind !== 'pivot');
      if (chanSignals.length) chartSeries.push({
        name: '缠论回绘买卖点', type: 'scatter', silent: true, z: 24,
        xAxisIndex: mainGrid, yAxisIndex: mainGrid,
        data: chanSignals.map((point) => ({
          value: [dates[point.index], point.price], name: chanPointLabel(point, chanTextMode),
          symbol: chanVariant === 'zhihu' ? 'circle' : 'triangle', symbolRotate: point.kind === 'sell' ? 180 : 0,
          itemStyle: { color: chanVariant === 'zhihu' ? 'transparent' : point.kind === 'buy' ? '#fbbf24' : '#22d3ee' },
          label: { position: point.kind === 'buy' ? 'bottom' : 'top',
            fontWeight: chanVariant === 'zhihu' && /\[([12] 卖|2 买)\]/.test(point.label) ? 'bold' : 'normal' },
        })),
        symbolSize: chanVariant === 'zhihu' ? 0 : 10,
        label: { show: true, formatter: '{b}', color: chanVariant === 'zhihu' ? '#ffffff' : '#fde68a',
          fontSize: chanTextMode === 'repaint' ? 9 : 8, distance: chanVariant === 'zhihu' ? 0 : 4 },
      });
    }

    if (revokedSignals.length) chartSeries.push({
      name: '缠论撤销记录', type: 'scatter', silent: false, z: 25, animation: false,
      xAxisIndex: mainGrid, yAxisIndex: mainGrid,
      symbol: 'circle', symbolSize: 11,
      itemStyle: { color: 'transparent', borderColor: '#94a3b8', borderWidth: 1.5 },
      label: { show: false },
      data: revokedSignals.map((point) => ({ value: [dates[point.index], point.price], name: point.label })),
      tooltip: { trigger: 'item', formatter: (params: { dataIndex: number }) => {
        const point = revokedSignals[params.dataIndex];
        return point ? signalTooltip([{ label: `⭕ 原式撤销 ${point.label}`,
          reason: `原标记位置：${dates[point.index]}；回放首次可见：${point.firstSeen}；回放撤销：${point.revokedAt}；原标记价位：${formatPrice(point.price)}。原因：后续K线加入后，原式条件不再输出该标记。非确认后止损失效。`,
          source: `${chanVariant === 'zhihu' ? '知乎版' : '雪球版·知乎文字扩展'} · 当前加载区间回放` }]) : '';
      } },
    });

    const zonePoints = watchPoints.filter((point) => point.markerType === 'IN_ZONE');
    const triggerPoints = watchPoints.filter((point) => point.markerType === 'TRIGGERED');
    if (zonePoints.length) {
      chartSeries.push({
        name: '进入回调区',
        type: 'scatter',
        data: zonePoints.map((point) => ({
          value: point.value,
          name: point.label,
          itemStyle: { color: point.pathCode === 'A' ? '#56d8ff' : '#ffb020' },
        })),
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        symbol: 'diamond',
        symbolSize: 13,
        silent: true,
        z: 20,
        itemStyle: { borderColor: '#080a0d', borderWidth: 1 },
        label: {
          show: true,
          position: 'top',
          formatter: (params: { name?: string }) => params.name || '',
          color: '#dff8ff',
          fontSize: 8,
          fontWeight: 700,
          distance: 3,
        },
      });
    }
    if (triggerPoints.length) {
      chartSeries.push({
        name: '确认触发',
        type: 'scatter',
        data: triggerPoints.map((point) => ({
          value: point.value,
          name: point.label,
          itemStyle: { color: point.pathCode === 'A' ? '#60a5fa' : '#28e070' },
        })),
        xAxisIndex: mainGrid,
        yAxisIndex: mainGrid,
        symbol: 'pin',
        symbolSize: 27,
        silent: true,
        z: 21,
        itemStyle: { borderColor: '#07130d', borderWidth: 1 },
        label: {
          show: true,
          position: 'inside',
          formatter: (params: { name?: string }) => params.name || '',
          color: '#04150b',
          fontSize: 7,
          fontWeight: 900,
        },
      });
    }

    const invalidPoints = watchPoints.filter((point) => point.markerType === 'INVALIDATED');
    if (invalidPoints.length) {
      chartSeries.push({
        name: '低吸失效', type: 'scatter',
        data: invalidPoints.map((point) => ({ value: point.value, name: point.label })),
        xAxisIndex: mainGrid, yAxisIndex: mainGrid, symbol: 'triangle', symbolRotate: 180,
        symbolSize: 12, silent: true, z: 22, itemStyle: { color: '#fb7185' },
        label: { show: true, position: 'top', formatter: '{b}', color: '#fda4af', fontSize: 8 },
      });
    }
    if (structurePoints.length) {
      chartSeries.push({
        name: '结构候选（模拟）', type: 'scatter',
        data: structurePoints.map((point) => ({
          value: point.value, name: point.label,
          symbol: point.marker_type === 'BUY_CONFIRM' ? 'triangle' : 'diamond',
          symbolSize: point.marker_type === 'BUY_CONFIRM' || point.marker_type === 'EXIT' ? 13 : 8,
          itemStyle: { color: STRUCTURE_COLORS[point.marker_type] },
          label: { position: ['OBSERVE', 'BUY_CONFIRM'].includes(point.marker_type) ? 'bottom' : 'top' },
        })),
        xAxisIndex: mainGrid, yAxisIndex: mainGrid, silent: true, z: 23,
        label: { show: true, formatter: '{b}', color: '#ddd6fe', fontSize: 8, distance: 3 },
      });
    }

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
              color: (params: { dataIndex: number }) => candleColor(candles, params.dataIndex),
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

    if (indicators.kdj) {
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

    if (!minutePeriod && indicators.fund) {
      addSubChart(
        '基金持股',
        {
          min: 0,
          axisLabel: { formatter: axisPct },
        },
        [
          {
            name: '基金持股比例',
            type: 'line',
            data: series(data?.funds?.fund),
            symbol: 'none',
            step: 'end',
            connectNulls: true,
            lineStyle: { width: 2, color: '#34d399' },
            areaStyle: { color: 'rgba(52, 211, 153, 0.08)' },
          },
        ],
      );
    }

    if (!minutePeriod && indicators.north) {
      addSubChart(
        '北向持股',
        {
          min: 0,
          axisLabel: { formatter: axisPct },
        },
        [
          {
            name: '北向持股比例',
            type: 'line',
            data: series(data?.funds?.north),
            symbol: 'none',
            connectNulls: true,
            lineStyle: { width: 1.5, color: '#818cf8' },
            areaStyle: { color: 'rgba(129, 140, 248, 0.08)' },
          },
        ],
      );
    }

    if (xAxis.length) {
      const lastAxis = xAxis[xAxis.length - 1];
      lastAxis.axisLine = { show: true, lineStyle: { color: '#27303a' } };
      lastAxis.axisPointer = { label: { show: true, backgroundColor: '#111827', formatter: (params: { value: unknown }) => String(params.value ?? '') } };
      lastAxis.axisLabel = {
        show: true,
        ...timeAxisLayout.axisLabel,
        formatter: (value: string) => formatStockTimeTick(value, period),
      };
    }

    return {
      backgroundColor: '#080a0d',
      animation: false,
      title: chartTitles,
      legend: {
        show: false,
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] },
        confine: true,
        backgroundColor: 'rgba(8, 10, 13, 0.96)',
        borderColor: '#27303a',
        borderWidth: 1,
        textStyle: { color: '#d7dde8', fontSize: 11, fontFamily: 'monospace' },
        formatter: (params: unknown) => {
          const index = resolveTooltipIndex(params, dates);
          const base = buildStockChartTooltip(
          index,
          {
            dates,
            candles,
            showRps: !minutePeriod,
            pctChanges: asArray<ChartRow>(data?.rows).map((row) => row.pct_chg),
            rpsSeries: minutePeriod ? [] : [
              { label: 'RPS5', values: asArray<NullableNumber>(data?.rps_short?.r5) },
              { label: 'RPS20', values: asArray<NullableNumber>(data?.rps_short?.r20) },
              { label: 'RPS50', values: asArray<NullableNumber>(data?.rps_long?.r50) },
              { label: 'RPS250', values: asArray<NullableNumber>(data?.rps_long?.r250) },
            ],
          },
          );
          return base + signalTooltip([
            ...chanDisplayedPoints.filter((point) => point.index === index).map((point) => ({
              label: `缠论 ${chanPointLabel(point, chanTextMode) || '极点'}`,
              reason: chanPointExplanation(dates[index], dates.at(-1) ?? '-', dates[0] ?? '-'),
              source: `${chanVariant === 'zhihu' ? '知乎版' : showChanPivotLabels ? '知乎买卖扩展' : '雪球版'} · ${formatPrice(point.price)}`,
            })),
            ...revokedSignals.filter((point) => point.index === index).map((point) => ({
              label: `⭕ 原式撤销 ${point.label}`,
              reason: `回放首次可见：${point.firstSeen}；回放撤销：${point.revokedAt}；原标记价位：${formatPrice(point.price)}。后续K线加入后原式标记撤销，非确认后止损失效。`,
              source: '当前加载区间逐柱回放',
            })),
            ...watchPoints.filter((point) => point.date === dates[index]).map((point) => ({
              label: point.label, reason: point.reason,
              source: `${point.source === 'daily_snapshot' ? '正式日快照' : '历史扫描'}${point.ruleVersion ? ` v${point.ruleVersion}` : ''}`,
            })),
            ...structurePoints.filter((point) => point.trade_date === dates[index]).map((point) => ({
              label: point.label, reason: point.reason,
              source: `候选模拟${point.stop_price ? ` · 失效线 ${formatPrice(point.stop_price)}` : ''}`,
            })),
          ]);
        },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      dataZoom: [
        {
          type: 'inside',
          filterMode: 'weakFilter',
          xAxisIndex: xAxisIndexes,
          start: 45,
          end: 100,
          zoomOnMouseWheel: false,
          moveOnMouseWheel: false,
          moveOnMouseMove: true,
        },
        {
          type: 'slider',
          filterMode: 'weakFilter',
          xAxisIndex: xAxisIndexes,
          start: 45,
          end: 100,
          ...timeAxisLayout.slider,
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
  }, [hintPrices, candles, data, dates, indicators, priceStyle, visibleKeys, watchPoints, structurePoints,
    chanOverlay, chanDisplayedPoints, chanVariant, chanTextMode, revokedSignals, chanHistoryZones, showChanHistory, showChanPivotLabels, showMovingAverages, minutePeriod, period, chartHeight]);

  useEffect(() => {
    const chart = chartRef.current?.getEchartsInstance();
    if (!chart || !dates.length) return;
    chart.dispatchAction({ type: 'dataZoom', ...zoomRangeRef.current });
  }, [option, dates.length]);

  const toggleIndicator = (key: IndicatorKey) => {
    setIndicators((previous) => ({ ...previous, [key]: !previous[key] }));
  };

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border border-border-secondary bg-[#080a0d]">
      <div className="sr-only" data-hint-chart-symbol={symbol} data-hint-chart-date={endDate}>
        {hintPrices.lines.map(line => <span key={line.price} data-hint-chart-price={line.price}>
          {line.label} {line.price.toFixed(2)}
        </span>)}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-1 border-b border-[#1e2226] bg-[#0b0f14] px-1.5 py-1">
        <div className="flex items-center gap-1">
          {CHART_PERIODS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setPeriod(item.key)}
              className={clsx(
                'h-5 rounded border px-1.5 text-[10px] font-black transition-colors',
                period === item.key
                  ? 'border-brand-blue bg-brand-blue/15 text-brand-blue-light'
                  : 'border-border-primary bg-bg-secondary text-text-secondary hover:border-brand-blue/50 hover:text-white',
              )}
            >
              {item.label}
            </button>
          ))}
          <div className="ml-1 flex items-center gap-1 border-l border-border-secondary pl-1">
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
          {watchPoints.length > 0 && (
            <div className="ml-1 flex items-center gap-2 border-l border-border-secondary pl-2 font-mono text-[9px] font-bold">
              <span className="text-brand-blue-light">A强回 ◆{pathCounts.A.zone} ●{pathCounts.A.trigger}</span>
              <span className="text-warning">B修复 ◆{pathCounts.B.zone} <b className="text-success">●{pathCounts.B.trigger}</b></span>
              <span className="text-rose-300">失效 {watchPoints.filter((point) => point.markerType === 'INVALIDATED').length}</span>
              {(zonePointCount + triggerPointCount) > (
                pathCounts.A.zone + pathCounts.A.trigger + pathCounts.B.zone + pathCounts.B.trigger
              ) && <span className="text-text-tertiary">未分路径</span>}
            </div>
          )}
        </div>

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-1">
          <button type="button" aria-pressed={showMovingAverages}
            onClick={() => setShowMovingAverages((previous) => !previous)}
            title="显示或隐藏主图均线，包含知乎版MA20；不改变缠论计算"
            className={clsx('h-5 rounded border px-1.5 text-[10px] font-bold',
              showMovingAverages ? 'border-brand-blue bg-brand-blue/15 text-brand-blue-light' : 'border-border-primary text-text-secondary')}>
            均线
          </button>
          <button type="button" aria-pressed={showChanOverlay}
            onClick={() => setShowChanOverlay((previous) => !previous)}
            title="显示通达信缠论主图指标"
            className={clsx('h-5 rounded border px-1.5 text-[10px] font-bold',
              showChanOverlay ? 'border-amber-400 bg-amber-500/20 text-amber-200' : 'border-border-primary text-text-secondary')}>
            缠论
          </button>
          <ChartSettingsDisclosure label="缠论设置">
            <div className="flex flex-wrap items-center gap-2">
          {showChanOverlay && <select aria-label="缠论公式版本" value={chanVariant}
            onChange={(event) => setChanVariant(event.target.value === 'xueqiu' ? 'xueqiu' : 'zhihu')}
            className="h-5 rounded border border-amber-400/40 bg-[#0b0f14] px-1 text-[10px] text-amber-200">
            <option value="zhihu">知乎版</option><option value="xueqiu">雪球版</option>
          </select>}
          {showChanOverlay && <select aria-label="缠论文字显示" value={chanTextMode}
            onChange={(event) => setChanTextMode(event.target.value === 'original' ? 'original' : 'repaint')}
            className="h-5 rounded border border-amber-400/40 bg-[#0b0f14] px-1 text-[10px] text-amber-200">
            <option value="repaint">回绘提示</option><option value="original">原式文字</option>
          </select>}
          {showChanOverlay && <button type="button" aria-pressed={showChanRevocations}
            onClick={() => setShowChanRevocations((previous) => !previous)}
            title="逐柱回放原式撤销信号，空心圆悬停查看首次可见及撤销时间"
            className={clsx('h-5 rounded border px-1.5 text-[10px]', showChanRevocations ? 'border-slate-400 text-slate-200' : 'border-border-primary text-text-secondary')}>
            撤销圈 ⭕
          </button>}
          {showChanOverlay && chanVariant === 'xueqiu' && <>
            <button type="button" aria-pressed={showChanHistory}
              onClick={() => setShowChanHistory((previous) => !previous)}
              title="扩展：截至各柱回放历史中枢，非原式当前输出"
              className={clsx('h-5 rounded border px-1.5 text-[10px]', showChanHistory ? 'border-amber-400 text-amber-200' : 'border-border-primary text-text-secondary')}>
              历史中枢（扩展）
            </button>
            <button type="button" aria-pressed={showChanPivotLabels}
              onClick={() => setShowChanPivotLabels((previous) => !previous)}
              title="扩展：沿用知乎版的极点买卖文字和ZIG买卖条件，历史标记可能重绘"
              className={clsx('h-5 rounded border px-1.5 text-[10px]', showChanPivotLabels ? 'border-amber-400 text-amber-200' : 'border-border-primary text-text-secondary')}>
              买卖标记（知乎扩展）
            </button>
          </>}
            </div>
      {showChanOverlay && <details className="border-b border-[#1e2226] px-2 py-1 text-[10px] text-amber-200/80">
        <summary className="cursor-pointer text-text-tertiary">缠论说明 · 展开查看</summary>
        <div className="py-1" role="status">
        <strong className="text-amber-300">回绘参考 · 买卖文字可能移动或撤销，不能当作已确认信号。</strong> {CHAN_OVERLAY_NOTICE} · 当前加载区间 {dates[0] || '-'} 至 {dates[dates.length - 1] || '-'}
        {chanVariant === 'xueqiu' && <span> · 当前中枢 {chanOverlay?.zones.length ?? 0} 个{!chanOverlay?.zones.length && '（原式条件未满足）'} · 买卖文字为知乎公式扩展{showChanHistory && ` · 历史回放 ${chanHistoryZones.length} 个（截至各柱，非原式当前输出）${displayedChanHistoryStatus ? ` · ${displayedChanHistoryStatus}` : ''}`}</span>}
        {showChanRevocations && (chanVariant === 'zhihu' || showChanPivotLabels) && <span> · 撤销圈 {revokedSignals.length} 个（当前区间回放，悬停查看；非已确认信号）{displayedChanHistoryStatus && ` · ${displayedChanHistoryStatus}`}</span>}
        {chanVariant === 'zhihu' && showMovingAverages && <span> · MA20：紫色，下降时绿色</span>}
        {chanVariant === 'zhihu' && !showMovingAverages && <span> · 原式MA20已隐藏，可用“均线”开启</span>}
        {chanOverlay?.truncated && <span> · 仅计算末 {chanOverlay.barCount} 根</span>}
        </div>
      </details>}

          </ChartSettingsDisclosure>
          {([['A', 'A强回'], ['B', 'B修复'], ['C', 'C结构']] as const).map(([key, label]) => (
            <button key={key} type="button" aria-pressed={strategyVisibility[key]}
              onClick={() => setStrategyVisibility((previous) => ({ ...previous, [key]: !previous[key] }))}
              title="只控制图表显示，不关闭盘后监控；A/B失效与C卖出分别标注"
              className={clsx('h-5 rounded border px-1.5 text-[10px] font-bold disabled:opacity-40',
                strategyVisibility[key] ? 'border-violet-400 bg-violet-500/20 text-violet-200' : 'border-border-primary text-text-secondary')}>
              {label}
            </button>
          ))}
          <ChartSettingsDisclosure label="C结构设置" align="right">
            <p className="mb-2 text-text-tertiary">信号显示（A/B/C 共用）</p>
            <div className="mb-3 flex flex-wrap gap-2">
              {([['observe', '观察'], ['buy', '买点'], ['sell', '卖点/失效']] as const).map(([key, label]) => (
                <button key={key} type="button" aria-pressed={strategyVisibility[key]}
                  onClick={() => setStrategyVisibility((previous) => ({ ...previous, [key]: !previous[key] }))}
                  className={clsx('rounded border px-2 py-1 text-[10px]', strategyVisibility[key]
                    ? 'border-violet-400 text-violet-200' : 'border-border-primary text-text-secondary')}>
                  {label}
                </button>
              ))}
            </div>
            {showStructure && structurePayload ? <StructureStrategyPanel payload={structurePayload} />
              : <p className="text-text-tertiary">开启 C结构后可查看规则、触发依据和买卖过程。</p>}
          </ChartSettingsDisclosure>
          {INDICATORS.map((item) => (
            <button
              key={item.key}
              type="button"
              title={item.label}
              disabled={minutePeriod && ['rps', 'fund', 'north'].includes(item.key)}
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
        {data?.source_label || data?.source || '通达信分钟行情'} · 未复权 · {chartPeriodLabel(period)}
        {' · 均线、MACD/KDJ、缠论按分钟K线计算；日RPS与持股不叠加；A/B采用已完成日线资格＋本周期形态'}
        {' · VOL为源单位'}
      </div>}

      {period !== 'daily' && (strategyVisibility.A || strategyVisibility.B || showStructure) && (
        <div role="status" className="border-b border-[#1e2226] px-2 py-1 text-[10px] text-violet-200">
          {chartPeriodLabel(period)}策略回放 · 窗口和冷却按K线根数
          {periodSignalQuery.isFetching ? ' · 正在计算…' : periodSignalQuery.isError
            ? ' · 读取失败，请重试' : ` · A/B规则版本 ${currentPeriodPayload?.rule_version ?? '—'}`}
          {currentPeriodPayload?.context_date && ` · 日线背景至 ${currentPeriodPayload.context_date}`}
          {currentPeriodPayload?.message && ` · ${currentPeriodPayload.message}`}
          {Boolean(currentPeriodPayload?.blocked?.length) && ` · A/B背景缺失 ${currentPeriodPayload?.blocked?.length} 根（含暖机）`}
          {showStructure && ` · C买确认 ${structurePoints.filter((point) => point.marker_type === 'BUY_CONFIRM').length} / 卖出 ${structurePoints.filter((point) => point.marker_type === 'EXIT').length}`}
          {' · C为模拟买卖；悬停信号查看依据'}
          {periodSignalQuery.isError && <button type="button" onClick={() => void periodSignalQuery.refetch()}
            className="ml-2 rounded border border-border-primary px-2">重试</button>}
        </div>
      )}


      {period === 'daily' && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 border-b border-[#1e2226] px-2 py-1 text-[10px] text-text-tertiary">
          {(strategyVisibility.A || strategyVisibility.B) && <span>
            {markerQuery.isError ? 'A/B标记读取失败，请刷新重试' : markerQuery.isLoading ? '正在读取A/B标记…'
              : markerPayload?.scan_coverage?.end_date ? `A/B历史扫描至 ${markerPayload.scan_coverage.end_date} · 覆盖外补正式快照`
                : 'A/B正式日快照 · 仅标首次转折'}
          </span>}
          {showStructure && <span className="text-violet-300">
            {structureQuery.isFetching ? '结构候选计算中…' : structureQuery.isError ? '结构候选读取失败，请关闭后重试'
              : `C结构 ${structureVersionLabel(structurePayload?.version ?? "")} · ${structurePoints.filter((p) => p.marker_type === 'BUY_CONFIRM').length}次买入确认 / ${structurePoints.filter((p) => p.marker_type === 'EXIT').length}次卖出信号`}
          </span>}
          {showStructure && <span>开关仅控制显示；C买卖为策略信号；悬停K线查看依据</span>}
        </div>
      )}


      <div ref={chartContainerRef} className="relative min-h-0 flex-1">
        <StockHintAxisPrices chartRef={chartRef} lines={hintPrices.lines} revision={option} ready={chartReady} />
        {chartQuery.isLoading ? <div className="flex h-full items-center justify-center text-xs text-text-secondary">
          正在读取{chartPeriodLabel(period)}数据…
        </div> : chartQuery.isError ? <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-warning">
          <span>{chartPeriodLabel(period)}读取失败，请切换周期或重试</span>
          <button type="button" disabled={chartQuery.isFetching}
            onClick={() => void chartQuery.refetch()}
            className="rounded border border-border-primary px-3 py-1 disabled:opacity-40">
            {chartQuery.isFetching ? '重试中…' : '重试'}
          </button>
        </div> : !dates.length || !candles.length ? <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-text-secondary">
          <span>{data?.message || `暂无${chartPeriodLabel(period)}K线数据，请切换其它周期`}</span>
          <button type="button" disabled={chartQuery.isFetching}
            onClick={() => void chartQuery.refetch()}
            className="rounded border border-border-primary px-3 py-1 disabled:opacity-40">
            {chartQuery.isFetching ? '重试中…' : '重试'}
          </button>
        </div> : <ReactECharts
          ref={chartRef}
          option={option}
          style={{ height: '100%', width: '100%', position: 'absolute' }}
          notMerge
          lazyUpdate
          onEvents={chartEvents}
          onChartReady={() => setChartReady(value => value + 1)}
          opts={{ renderer: 'canvas' }}
        />}
      </div>
    </div>
  );
}
