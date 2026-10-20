'use client';

import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { clsx } from 'clsx';

export type Dict = Record<string, any>;

export function asList<T = Dict>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

export function num(value: unknown, digits = 2): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '-';
  return parsed.toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function int(value: unknown): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '-';
  return Math.round(parsed).toLocaleString('zh-CN');
}

export function pct(value: unknown, digits = 2): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '-';
  return `${parsed >= 0 ? '+' : ''}${parsed.toFixed(digits)}%`;
}

export function ratioPct(value: unknown, digits = 1): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '-';
  return `${(parsed * 100).toFixed(digits)}%`;
}

export function money(value: unknown): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '-';
  if (Math.abs(parsed) >= 100000000) return `${(parsed / 100000000).toFixed(0)}亿`;
  if (Math.abs(parsed) >= 10000) return `${(parsed / 10000).toFixed(0)}万`;
  return parsed.toFixed(0);
}

export function badgeClass(status?: string) {
  if (!status) return 'border-border-primary bg-bg-tertiary text-text-secondary';
  if (['ok', 'success', 'active', 'normal', 'profitable', 'healthy', 'M3', 'M4', 'I3', 'I4', 'S4', 'S5'].includes(status)) {
    return 'border-success/30 bg-success/10 text-success';
  }
  if (['stale', 'reduce', 'tracked', 'time_stop_10d', 'market_risk', 'industry_risk', 'M1', 'M2', 'I1', 'I2', 'S3'].includes(status)) {
    return 'border-warning/40 bg-warning/10 text-warning';
  }
  if (['failed', 'error', 'missing', 'duplicate', 'empty', 'exit_pending', 'stop_triggered', 'time_stop_20d', 'M0', 'I0', 'S1'].includes(status)) {
    return 'border-danger/40 bg-danger/10 text-danger';
  }
  return 'border-border-primary bg-bg-tertiary text-text-secondary';
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="w-full min-h-full">
      <div className="mx-auto max-w-[1840px] px-2 sm:px-3 pb-3">
        {children}
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  eyebrow,
  icon: Icon,
  actions,
  children,
}: {
  title: string;
  eyebrow?: string;
  icon: LucideIcon;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="py-2 flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
      <div className="min-w-0">
        <div className="flex items-start gap-2">
          <span className="h-7 w-7 rounded-lg border border-brand-blue/35 bg-brand-blue/15 inline-flex items-center justify-center shadow-[0_0_24px_rgba(31,111,255,0.25)]">
            <Icon size={15} className="text-brand-blue" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-lg sm:text-xl font-bold text-white tracking-normal leading-tight">{title}</h1>
              {eyebrow && <span className="rounded border border-border-primary bg-bg-secondary/70 px-2 py-1 text-[10px] font-mono text-text-tertiary">{eyebrow}</span>}
            </div>
            {children && <div className="text-[11px] text-text-secondary mt-1">{children}</div>}
          </div>
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Panel({
  title,
  icon: Icon,
  extra,
  children,
  className,
}: {
  title: string;
  icon: LucideIcon;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx('cockpit-card min-w-0 overflow-hidden', className)}>
      <div className="h-11 px-3 border-b border-border-primary/80 flex items-center justify-between gap-3 bg-bg-secondary/60">
        <div className="flex items-center gap-2 min-w-0">
          <Icon size={14} className="text-brand-blue shrink-0" />
          <h2 className="text-xs font-bold text-text-primary truncate tracking-normal">{title}</h2>
        </div>
        {extra}
      </div>
      {children}
    </section>
  );
}

export function Metric({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: 'neutral' | 'up' | 'down' | 'warn';
}) {
  return (
    <div className="cockpit-card p-3 min-h-[88px]">
      <div className="text-[11px] text-text-secondary mb-2">{label}</div>
      <div className={clsx(
        'text-xl font-bold font-mono',
        tone === 'up' && 'text-up',
        tone === 'down' && 'text-down',
        tone === 'warn' && 'text-warning',
        tone === 'neutral' && 'text-text-primary',
      )}>
        {value}
      </div>
      {sub && <div className="text-[11px] text-text-tertiary mt-1 truncate">{sub}</div>}
    </div>
  );
}

export function Badge({ children, status }: { children: ReactNode; status?: string }) {
  return (
    <span className={clsx('inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-bold', badgeClass(status))}>
      {children}
    </span>
  );
}

export function DataTable({
  columns,
  rows,
  empty = '暂无数据',
}: {
  columns: Array<{ key: string; label: string; align?: 'left' | 'right'; render?: (row: Dict) => ReactNode }>;
  rows: Dict[];
  empty?: string;
}) {
  return (
    <div className="overflow-x-auto custom-scrollbar">
      <table className="w-full min-w-[760px] border-collapse text-left">
        <thead className="bg-[#0e1726]/80 text-text-secondary">
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                className={clsx('px-3 py-2 text-[10px] font-bold whitespace-nowrap', column.align === 'right' && 'text-right')}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border-secondary">
          {rows.length === 0 ? (
            <tr>
              <td className="px-3 py-8 text-center text-text-secondary" colSpan={columns.length}>
                {empty}
              </td>
            </tr>
          ) : rows.map((row, index) => (
            <tr key={`${row.symbol ?? row.table_name ?? row.signal_id ?? row.run_id ?? row.name ?? index}-${index}`} className="hover:bg-bg-tertiary/70">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={clsx('px-3 py-2 text-[11px] text-text-primary whitespace-nowrap', column.align === 'right' && 'text-right')}
                >
                  {column.render ? column.render(row) : row[column.key] ?? '-'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DateSelect({
  value,
  dates,
  onChange,
}: {
  value?: string;
  dates: string[];
  onChange: (value: string) => void;
}) {
  return (
    <select
      value={value || ''}
      onChange={(event) => onChange(event.target.value)}
      className="h-9 min-w-[138px] rounded-lg border border-border-primary bg-[#0b1220] px-3 text-xs text-text-primary font-mono outline-none focus:border-brand-blue/70"
    >
      {dates.map((date) => (
        <option key={date} value={date} className="bg-bg-secondary">
          {date}
        </option>
      ))}
    </select>
  );
}

export function ToolbarButton({
  children,
  onClick,
  active,
}: {
  children: ReactNode;
  onClick?: () => void;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'h-8 px-3 rounded-lg border text-xs font-bold transition-colors',
        active
          ? 'border-brand-blue bg-brand-blue/15 text-brand-blue'
          : 'border-border-primary bg-bg-secondary text-text-secondary hover:text-text-primary',
      )}
    >
      {children}
    </button>
  );
}
