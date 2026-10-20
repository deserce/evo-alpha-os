'use client';

import type { CSSProperties, ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { clsx } from 'clsx';
import { RefreshCw } from 'lucide-react';
import { AppShell, DateSelect, asList } from './DashboardPrimitives';

export type Row = Record<string, unknown>;
export type Tone = 'good' | 'warn' | 'bad' | 'blue' | 'neutral';

export const stageLabels = ['S0 观察', 'S1 启动', 'S2 加速', 'S3 准备', 'S4 临界', 'S5 触发'];

export const signalNames: Record<string, string> = {
  pocket_pivot: '口袋买点',
  breakout: '突破买点',
  supply_exhaustion: '抛压枯竭',
  earnings_gap: '业绩跳空',
};

export function rows<T = Row>(value: unknown): T[] {
  return asList<T>(value);
}

export function toNumber(value: unknown, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function clamp(value: number, min = 0, max = 100) {
  return Math.min(max, Math.max(min, value));
}

export function text(value: unknown, fallback = '-') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

export function num(value: unknown, digits = 2) {
  const parsed = toNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) return '-';
  return parsed.toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function int(value: unknown) {
  const parsed = toNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) return '-';
  return Math.round(parsed).toLocaleString('zh-CN');
}

export function pct(value: unknown, digits = 2) {
  const parsed = toNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) return '-';
  return `${parsed >= 0 ? '+' : ''}${parsed.toFixed(digits)}%`;
}

export function ratioPct(value: unknown, digits = 1) {
  const parsed = toNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) return '-';
  return `${(parsed * 100).toFixed(digits)}%`;
}

export function money(value: unknown) {
  const parsed = toNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) return '-';
  if (Math.abs(parsed) >= 100000000) return `${(parsed / 100000000).toFixed(0)}亿`;
  if (Math.abs(parsed) >= 10000) return `${(parsed / 10000).toFixed(0)}万`;
  return parsed.toFixed(0);
}

export function toneFromState(value?: unknown): Tone {
  const state = String(value || '');
  if (['M3', 'M4', 'I3', 'I4', 'S4', 'S5', 'ok', 'success', 'active', 'normal', 'profitable', 'healthy'].includes(state)) return 'good';
  if (['M1', 'M2', 'I1', 'I2', 'S3', 'tracked', 'stale', 'queued', 'running'].includes(state)) return 'warn';
  if (['M0', 'I0', 'S0', 'S1', 'failed', 'error', 'missing', 'empty', 'exit_pending', 'stop_triggered'].includes(state)) return 'bad';
  return 'neutral';
}

export function toneTextClass(tone: Tone) {
  return {
    good: 'text-success',
    warn: 'text-warning',
    bad: 'text-danger',
    blue: 'text-brand-blue-light',
    neutral: 'text-text-primary',
  }[tone];
}

export function WorkbenchPage({
  index,
  title,
  subtitle,
  date,
  dates,
  onDateChange,
  onRefresh,
  children,
}: {
  index: string;
  title: string;
  subtitle?: ReactNode;
  date?: string;
  dates?: string[];
  onDateChange?: (value: string) => void;
  onRefresh?: () => void;
  children: ReactNode;
}) {
  return (
    <AppShell>
      <div className="evo-command-space">
        <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <h1 className="text-center text-[18px] font-black tracking-normal text-white lg:text-left">
              <span className="font-mono">{index}</span> {title}
            </h1>
            {subtitle && <div className="mt-1 text-center text-[11px] text-text-secondary lg:text-left">{subtitle}</div>}
          </div>
          <div className="flex flex-wrap items-center justify-center gap-2 lg:justify-end">
            {dates && onDateChange && (
              <DateSelect value={date} dates={dates} onChange={onDateChange} />
            )}
            {onRefresh && (
              <button
                type="button"
                onClick={onRefresh}
                className="h-9 w-9 rounded-lg border border-border-primary bg-bg-secondary text-text-primary transition-colors hover:border-brand-blue/60 hover:text-brand-blue-light"
                title="刷新"
              >
                <RefreshCw size={14} className="mx-auto" />
              </button>
            )}
          </div>
        </div>
        {children}
      </div>
    </AppShell>
  );
}

export function CockpitPanel({
  title,
  icon: Icon,
  children,
  className,
  extra,
}: {
  title: string;
  icon: LucideIcon;
  children: ReactNode;
  className?: string;
  extra?: ReactNode;
}) {
  return (
    <section className={clsx('evo-panel min-w-0 overflow-hidden', className)}>
      <header className="evo-panel-header">
        <div className="flex min-w-0 items-center gap-2">
          <Icon size={14} className="shrink-0 text-brand-blue-light" />
          <h2 className="truncate text-[12px] font-black text-white">{title}</h2>
        </div>
        {extra}
      </header>
      <div className="p-3">{children}</div>
    </section>
  );
}

export function StatCard({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className="evo-stat">
      <div className="truncate text-[10px] text-text-tertiary">{label}</div>
      <div className={clsx('mt-1 truncate font-mono text-[18px] font-black leading-none', toneTextClass(tone))}>
        {value}
      </div>
      {sub && <div className="mt-1 truncate text-[10px] text-text-secondary">{sub}</div>}
    </div>
  );
}

export function StageBadge({ value }: { value?: unknown }) {
  const state = text(value);
  const tone = toneFromState(state);
  return (
    <span className={clsx(
      'inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-black',
      tone === 'good' && 'border-success/30 bg-success/10 text-success',
      tone === 'warn' && 'border-warning/40 bg-warning/10 text-warning',
      tone === 'bad' && 'border-danger/40 bg-danger/10 text-danger',
      tone === 'neutral' && 'border-border-primary bg-bg-tertiary text-text-secondary',
    )}>
      {state}
    </span>
  );
}

export function MiniSparkline({
  values,
  tone = 'good',
  height = 42,
}: {
  values: number[];
  tone?: Tone;
  height?: number;
}) {
  const width = 120;
  const points = values.map((value, index) => {
    const x = (index / Math.max(1, values.length - 1)) * width;
    const y = height - (clamp(value) / 100) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const color = {
    good: '#28e070',
    warn: '#ffb020',
    bad: '#ff4f6d',
    blue: '#56d8ff',
    neutral: '#8ea0bd',
  }[tone];

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={`${points} ${width},${height} 0,${height}`} fill={color} opacity="0.12" stroke="none" />
      <polyline points={points} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function MiniBars({
  values,
  tone = 'blue',
}: {
  values: number[];
  tone?: Tone;
}) {
  const color = {
    good: 'bg-success',
    warn: 'bg-warning',
    bad: 'bg-danger',
    blue: 'bg-brand-blue',
    neutral: 'bg-text-tertiary',
  }[tone];

  return (
    <div className="flex h-full items-end gap-1">
      {values.map((value, index) => (
        <span
          key={`${value}-${index}`}
          className={clsx('min-w-[3px] flex-1 rounded-t opacity-90', color)}
          style={{ height: `${clamp(value, 8, 100)}%` }}
        />
      ))}
    </div>
  );
}

export function RingGauge({
  value,
  label,
  tone = 'good',
}: {
  value: number;
  label: string;
  tone?: Tone;
}) {
  const normalized = clamp(value);
  const color = {
    good: '#28e070',
    warn: '#ffb020',
    bad: '#ff4f6d',
    blue: '#56d8ff',
    neutral: '#8ea0bd',
  }[tone];
  const style = {
    '--ring-color': color,
    '--ring-value': `${normalized * 3.6}deg`,
  } as CSSProperties;

  return (
    <div className="evo-ring" style={style}>
      <div className="evo-ring-core">
        <span className="font-mono text-lg font-black text-white">{normalized.toFixed(0)}</span>
        <span className="text-[9px] text-text-tertiary">{label}</span>
      </div>
    </div>
  );
}

export function ProgressLine({
  label,
  value,
  tone = 'good',
  right,
}: {
  label: string;
  value: number;
  tone?: Tone;
  right?: ReactNode;
}) {
  const color = {
    good: 'bg-success',
    warn: 'bg-warning',
    bad: 'bg-danger',
    blue: 'bg-brand-blue-light',
    neutral: 'bg-text-tertiary',
  }[tone];

  return (
    <div className="grid grid-cols-[72px_1fr_58px] items-center gap-2">
      <span className="truncate text-[10px] text-text-secondary">{label}</span>
      <span className="h-1.5 overflow-hidden rounded-full bg-bg-primary">
        <span className={clsx('block h-full rounded-full', color)} style={{ width: `${clamp(value, 3, 100)}%` }} />
      </span>
      <span className="truncate text-right font-mono text-[10px] text-text-primary">{right ?? `${value.toFixed(0)}%`}</span>
    </div>
  );
}

export function CompactTable({
  rows: tableRows,
  columns,
  rowKey,
  limit = 8,
  minWidth = 520,
  empty = '暂无数据',
  onRowClick,
  sortKey,
  sortDirection,
  onSort,
}: {
  rows: Row[];
  columns: Array<{
    key: string;
    label: string;
    align?: 'left' | 'right';
    sortable?: boolean;
    render?: (row: Row, index: number) => ReactNode;
  }>;
  rowKey: (row: Row, index: number) => string;
  limit?: number;
  minWidth?: number;
  empty?: string;
  onRowClick?: (row: Row, index: number) => void;
  sortKey?: string;
  sortDirection?: 'asc' | 'desc';
  onSort?: (key: string) => void;
}) {
  const visibleRows = tableRows.slice(0, limit);

  return (
    <div className="overflow-x-auto custom-scrollbar">
      <table className="w-full border-collapse text-left" style={{ minWidth }}>
        <thead>
          <tr className="border-y border-border-secondary bg-[#081422]/85">
            {columns.map((column) => (
              <th
                key={column.key}
                className={clsx('px-2 py-1.5 text-[10px] font-bold text-text-tertiary', column.align === 'right' && 'text-right')}
              >
                {column.sortable && onSort ? (
                  <button
                    type="button"
                    onClick={() => onSort(column.key)}
                    className={clsx(
                      'inline-flex w-full items-center gap-1 transition-colors hover:text-white',
                      column.align === 'right' ? 'justify-end' : 'justify-start',
                      sortKey === column.key && 'text-brand-blue-light',
                    )}
                    title={`${column.label}排序`}
                  >
                    <span>{column.label}</span>
                    {sortKey === column.key && (
                      <span className="font-mono text-[9px]">{sortDirection === 'asc' ? '↑' : '↓'}</span>
                    )}
                  </button>
                ) : (
                  column.label
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border-secondary/80">
          {visibleRows.length === 0 ? (
            <tr>
              <td className="px-2 py-6 text-center text-[11px] text-text-tertiary" colSpan={columns.length}>
                {empty}
              </td>
            </tr>
          ) : visibleRows.map((row, index) => (
            <tr
              key={rowKey(row, index)}
              onClick={onRowClick ? () => onRowClick(row, index) : undefined}
              className={onRowClick ? 'cursor-pointer hover:bg-brand-blue/10' : 'hover:bg-brand-blue/5'}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={clsx('px-2 py-1.5 text-[11px] text-text-primary', column.align === 'right' && 'text-right font-mono')}
                >
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
