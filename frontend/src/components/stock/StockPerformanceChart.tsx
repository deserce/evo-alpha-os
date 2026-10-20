'use client';

import type { EChartsOption } from 'echarts';
import ReactECharts from 'echarts-for-react';
import { AlertTriangle, Database } from 'lucide-react';
import { clsx } from 'clsx';
import { useMemo } from 'react';
import { useGlobalStore } from '@/lib/store';

import type {
  StockFinancePerformance,
  StockFinanceResponse,
  StockFinancialMetricKey,
} from '@/lib/api';
import {
  STOCK_FINANCIAL_METRICS,
  buildFinancialChartPresentation,
  buildFinancialChartSeries,
  buildFinancialYearGroups,
  formatFinancialMetricValue,
  type FinancialChartPresentation,
  type FinancialPerformancePoint,
  type FinancialYearGroup,
} from './stockFinancialPerformance';

export interface StockPerformanceChartProps {
  data?: StockFinanceResponse | StockFinancePerformance | null;
  years?: number;
  initialMetric?: StockFinancialMetricKey;
  compact?: boolean;
  fitHeight?: boolean;
  className?: string;
  error?: string | null;
  feedback?: string | null;
}

interface TooltipParameter {
  dataIndex?: number;
  axisValue?: string | number;
}

const QUARTER_COLORS = ['#2563eb', '#3b82f6', '#60a5fa', '#93c5fd'] as const;

function unwrapPerformance(
  data: StockPerformanceChartProps['data'],
): StockFinancePerformance | null {
  if (!data) {
    return null;
  }
  return 'performance' in data ? data.performance : data;
}

function isMetricKey(value: unknown): value is StockFinancialMetricKey {
  return STOCK_FINANCIAL_METRICS.some((metric) => metric.key === value);
}

function hasMetricValue(groups: readonly FinancialYearGroup[], key: StockFinancialMetricKey) {
  return groups.some((group) => (
    group.summary?.values[key] !== null && group.summary?.values[key] !== undefined
  ) || group.quarters.some((quarter) => (
    quarter?.values[key] !== null && quarter?.values[key] !== undefined
  )));
}

function availableMetrics(
  performance: StockFinancePerformance | null,
  groups: readonly FinancialYearGroup[],
) {
  const advertised = (performance?.available_metrics ?? []).filter(isMetricKey);
  const candidates = advertised.length
    ? STOCK_FINANCIAL_METRICS.filter((metric) => advertised.includes(metric.key))
    : STOCK_FINANCIAL_METRICS.filter((metric) => hasMetricValue(groups, metric.key));
  return candidates;
}

function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function tooltipIndex(params: unknown, categories: readonly string[]): number {
  const values = Array.isArray(params) ? params : [params];
  for (const value of values) {
    if (!value || typeof value !== 'object') {
      continue;
    }
    const parameter = value as TooltipParameter;
    if (Number.isInteger(parameter.dataIndex)) {
      return parameter.dataIndex as number;
    }
    if (parameter.axisValue !== null && parameter.axisValue !== undefined) {
      const index = categories.indexOf(String(parameter.axisValue));
      if (index >= 0) {
        return index;
      }
    }
  }
  return -1;
}

function sourceText(point: FinancialPerformancePoint | null, fallback: string | null) {
  return point?.source || fallback || '未标注';
}

function percentText(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '—';
  }
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function tooltipHtml(
  params: unknown,
  presentation: FinancialChartPresentation,
  fallbackSource: string | null,
): string {
  const index = tooltipIndex(params, presentation.categories);
  const group = presentation.groups[index];
  if (!group) {
    return '';
  }

  const summary = group.summary;
  const summaryValue = summary?.values[presentation.metricKey];
  const summaryYoy = summary?.yoy[presentation.metricKey];
  const summaryName = summary?.label ?? '累计报告缺失';
  const summaryDate = summary?.reportDate || '—';
  const summaryDisplay = formatFinancialMetricValue(
    summaryValue,
    presentation.metricKey,
    presentation.scale,
  );
  const quarterRows = group.quarters.map((quarter, quarterIndex) => {
    const value = quarter?.values[presentation.metricKey];
    const display = formatFinancialMetricValue(
      value,
      presentation.metricKey,
      presentation.scale,
    );
    const reportDate = quarter?.reportDate || '—';
    const source = sourceText(quarter, fallbackSource);
    return `
      <div style="display:grid;grid-template-columns:30px 1fr auto;gap:10px;margin-top:5px;">
        <span style="color:${QUARTER_COLORS[quarterIndex]};font-weight:700;">
          Q${quarterIndex + 1}
        </span>
        <span style="color:#7f8da3;">${escapeHtml(reportDate)} 单季</span>
        <span style="color:#dbe6f4;font-weight:700;">${escapeHtml(display)}</span>
      </div>
      ${quarter ? `
        <div style="margin-left:40px;color:#66758a;font-size:10px;">
          同比 ${escapeHtml(percentText(quarter.yoy[presentation.metricKey]))}
          · 来源 ${escapeHtml(source)}
        </div>
      ` : ''}
    `;
  }).join('');

  return `
    <div style="min-width:260px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">
      <div style="color:#f5f8fc;font-weight:800;margin-bottom:7px;">
        ${group.year} · ${escapeHtml(presentation.metric.label)}
      </div>
      <div style="border:1px solid #253044;background:#101722;padding:7px 8px;">
        <div style="display:flex;justify-content:space-between;gap:16px;">
          <span style="color:#9dadc2;">
            ${escapeHtml(summaryName)} · ${escapeHtml(summaryDate)}
          </span>
          <span style="color:#e7edf7;font-weight:800;">${escapeHtml(summaryDisplay)}</span>
        </div>
        <div style="margin-top:4px;color:#f59e0b;">
          累计同比 ${escapeHtml(percentText(summaryYoy))}
        </div>
        <div style="margin-top:3px;color:#66758a;font-size:10px;">
          来源 ${escapeHtml(sourceText(summary, fallbackSource))}
        </div>
      </div>
      <div style="margin-top:7px;">${quarterRows}</div>
    </div>
  `;
}

function chartOption(
  presentation: FinancialChartPresentation,
  compact: boolean,
  fallbackSource: string | null,
): EChartsOption {
  const series = buildFinancialChartSeries(presentation);
  const zoomStart = compact && presentation.categories.length > 3
    ? Math.max(0, 100 - (3 / presentation.categories.length) * 100)
    : 0;

  return {
    backgroundColor: 'transparent',
    animation: false,
    aria: {
      enabled: true,
      decal: { show: false },
    },
    legend: {
      top: 2,
      left: compact ? 6 : 'center',
      itemWidth: 10,
      itemHeight: 7,
      itemGap: compact ? 7 : 12,
      textStyle: {
        color: '#8593a8',
        fontSize: compact ? 9 : 10,
        fontFamily: 'monospace',
      },
      data: series.map((item) => item.name),
    },
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'shadow' },
      backgroundColor: 'rgba(7, 11, 17, 0.98)',
      borderColor: '#263247',
      borderWidth: 1,
      padding: 10,
      textStyle: { color: '#dbe6f4', fontSize: 11 },
      formatter: (params: unknown) => tooltipHtml(params, presentation, fallbackSource),
    },
    grid: {
      left: compact ? 44 : 58,
      right: compact ? 42 : 52,
      top: compact ? 54 : 48,
      bottom: compact ? 54 : 38,
      containLabel: false,
    },
    xAxis: [
      {
        type: 'category',
        data: presentation.categories,
        axisLine: { lineStyle: { color: '#2a3443' } },
        axisTick: { show: false },
        axisLabel: {
          color: '#8796aa',
          fontSize: compact ? 9 : 10,
          fontFamily: 'monospace',
          lineHeight: 14,
          interval: 0,
        },
      },
      {
        type: 'category',
        data: presentation.categories,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value',
        name: presentation.scale.unit,
        nameTextStyle: { color: '#718096', fontSize: 9, align: 'right' },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: '#718096',
          fontSize: 9,
          fontFamily: 'monospace',
        },
        splitLine: {
          lineStyle: { color: '#202a38', type: 'dashed' },
        },
      },
      {
        type: 'value',
        name: '同比 %',
        nameTextStyle: { color: '#9a7d43', fontSize: 9, align: 'left' },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: '#9a7d43',
          fontSize: 9,
          formatter: '{value}%',
        },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: zoomStart,
        end: 100,
        zoomOnMouseWheel: false,
        moveOnMouseWheel: false,
        moveOnMouseMove: true,
      },
      {
        type: 'slider',
        show: compact && presentation.categories.length > 3,
        xAxisIndex: [0, 1],
        start: zoomStart,
        end: 100,
        bottom: 5,
        height: 12,
        borderColor: 'transparent',
        backgroundColor: '#101722',
        fillerColor: 'rgba(59, 130, 246, 0.15)',
        handleStyle: { color: '#3b82f6' },
        moveHandleStyle: { color: '#3b82f6' },
        textStyle: { color: '#718096', fontSize: 9 },
      },
    ],
    series: [
      {
        name: series[0].name,
        type: 'bar',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: series[0].data,
        barWidth: compact ? '86%' : '80%',
        z: 1,
        itemStyle: {
          color: 'rgba(76, 111, 174, 0.18)',
          borderColor: 'rgba(111, 148, 214, 0.40)',
          borderWidth: 1,
        },
        emphasis: {
          itemStyle: { color: 'rgba(76, 111, 174, 0.28)' },
        },
      },
      ...series.slice(1, 5).map((item, index) => ({
        name: item.name,
        type: 'bar' as const,
        xAxisIndex: 1,
        yAxisIndex: 0,
        data: item.data,
        barMaxWidth: compact ? 9 : 13,
        barGap: '18%',
        barCategoryGap: '32%',
        z: 3,
        itemStyle: {
          color: QUARTER_COLORS[index],
          borderRadius: [2, 2, 0, 0],
        },
      })),
      {
        name: series[5].name,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 1,
        data: series[5].data,
        connectNulls: false,
        symbol: 'circle',
        symbolSize: compact ? 5 : 6,
        z: 5,
        lineStyle: { color: '#f59e0b', width: 1.6 },
        itemStyle: { color: '#f59e0b' },
      },
    ],
  };
}

function StatusMessage({
  icon,
  title,
  description,
}: {
  icon: 'empty' | 'error';
  title: string;
  description: string;
}) {
  const Icon = icon === 'error' ? AlertTriangle : Database;
  return (
    <div className="flex min-h-[260px] flex-col items-center justify-center px-5 text-center">
      <Icon size={20} className={icon === 'error' ? 'text-warning' : 'text-text-tertiary'} />
      <div className="mt-2 text-[12px] font-black text-text-secondary">{title}</div>
      <div className="mt-1 max-w-md text-[10px] leading-4 text-text-tertiary">
        {description}
      </div>
    </div>
  );
}

export default function StockPerformanceChart({
  data,
  years = 5,
  initialMetric = 'net_profit',
  compact = false,
  fitHeight = false,
  className,
  error,
  feedback,
}: StockPerformanceChartProps) {
  const performance = useMemo(() => unwrapPerformance(data), [data]);
  const groups = useMemo(
    () => buildFinancialYearGroups(performance, years),
    [performance, years],
  );
  const metrics = useMemo(
    () => availableMetrics(performance, groups),
    [performance, groups],
  );
  const storedMetric = useGlobalStore((state) => state.stockSidebarUi.financialMetric);
  const setSidebarUi = useGlobalStore((state) => state.setStockSidebarUi);
  const selectedMetric = isMetricKey(storedMetric) ? storedMetric : initialMetric;
  const setSelectedMetric = (financialMetric: StockFinancialMetricKey) => setSidebarUi({ financialMetric });
  const activeMetric = metrics.some((metric) => metric.key === selectedMetric)
    ? selectedMetric
    : metrics[0]?.key ?? initialMetric;
  const presentation = useMemo(
    () => buildFinancialChartPresentation(groups, activeMetric),
    [activeMetric, groups],
  );
  const option = useMemo(
    () => chartOption(presentation, compact, performance?.source ?? null),
    [compact, performance?.source, presentation],
  );
  const hasData = groups.length > 0 && metrics.length > 0;
  const status = performance?.data_status ?? 'EMPTY';
  const statusLabel = status === 'OK'
    ? '数据完整'
    : status === 'PARTIAL' ? '存在缺口' : '暂无数据';
  const emptyDescription = error
    || '使用资料面板右上角「补齐业绩」，按当前股票补齐 F10 累计报告期与真实单季数据。';

  return (
    <section
      style={fitHeight ? { minHeight: 350 } : undefined}
      className={clsx(
        'overflow-hidden rounded-lg border border-border-secondary bg-[#080b10]',
        fitHeight && 'flex flex-col',
        className,
      )}
    >
      <div className="shrink-0 border-b border-[#202936] bg-[#0c1119] px-2.5 py-1">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="shrink-0 text-[12px] font-black text-text-primary">业绩表现</h3>
          <span
            className={clsx(
              'shrink-0 rounded border px-1 py-0.5 text-[9px] font-bold',
              status === 'OK'
                ? 'border-success/30 bg-success/10 text-success'
                : status === 'PARTIAL'
                  ? 'border-warning/30 bg-warning/10 text-warning'
                  : 'border-border-secondary text-text-tertiary',
            )}
          >
            {statusLabel}
          </span>
          <span className="ml-auto truncate font-mono text-[10px] text-text-tertiary"
            title="最新报告期">
            {performance?.latest_report_date || '累计 + 单季'}
          </span>
        </div>


      </div>

      {feedback && (
        <div
          className={clsx(
            'border-b border-success/20 bg-success/5 px-2.5 py-1.5',
            'text-[10px] text-success',
          )}
        >
          {feedback}
        </div>
      )}
      {error && hasData && (
        <div
          className={clsx(
            'flex items-center gap-1 border-b border-warning/20 bg-warning/5',
            'px-2.5 py-1.5 text-[10px] text-warning',
          )}
        >
          <AlertTriangle size={11} />
          <span>{error}</span>
        </div>
      )}

      {!hasData ? (
        <StatusMessage
          icon={error ? 'error' : 'empty'}
          title={error ? '业绩数据更新失败' : '暂无详细业绩数据'}
          description={emptyDescription}
        />
      ) : (
        <div
          className={clsx(
            'w-full',
            fitHeight
              ? 'min-h-[240px] flex-1'
              : compact
              ? 'h-[clamp(270px,30vh,310px)]'
              : 'h-[clamp(290px,32vh,340px)]',
          )}
        >
          <ReactECharts
            option={option}
            style={{ height: '100%', width: '100%' }}
            notMerge
            lazyUpdate
            opts={{ renderer: 'canvas' }}
          />
        </div>
      )}
        {hasData && (
          <div className="grid shrink-0 grid-cols-4 gap-1 border-t border-[#202936] bg-[#0c1119] px-2.5 py-1.5">
            {metrics.map((metric) => (
              <button
                key={metric.key}
                type="button"
                aria-pressed={activeMetric === metric.key}
                title={metric.label}
                onClick={() => setSelectedMetric(metric.key)}
                className={clsx(
                  'min-w-0 whitespace-nowrap rounded border px-1 py-1 text-[11px] font-bold transition-colors',
                  activeMetric === metric.key
                    ? 'border-brand-blue/60 bg-brand-blue/15 text-brand-blue-light'
                    : clsx(
                      'border-border-secondary bg-bg-primary/50 text-text-tertiary',
                      'hover:text-text-primary',
                    ),
                )}
              >
                {metric.key === 'net_profit' ? '归母净利'
                  : metric.key === 'deduct_net_profit' ? '扣非净利'
                  : metric.key === 'operating_cashflow_per_share' ? '每股现金流'
                  : metric.shortLabel}
              </button>
            ))}
          </div>
        )}
    </section>
  );
}
