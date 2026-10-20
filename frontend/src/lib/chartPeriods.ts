export type ChartPeriod = 'daily' | 'weekly' | 'monthly' | '30min' | '120min';

export const CHART_PERIODS: ReadonlyArray<{ key: ChartPeriod; label: string }> = [
  { key: 'daily', label: '日K' },
  { key: 'weekly', label: '周K' },
  { key: 'monthly', label: '月K' },
  { key: '30min', label: '30分钟' },
  { key: '120min', label: '120分钟' },
];

export function isMinutePeriod(period: ChartPeriod): boolean {
  return period === '30min' || period === '120min';
}

export function chartPeriodLabel(period: ChartPeriod): string {
  return CHART_PERIODS.find((item) => item.key === period)?.label ?? '日K';
}

export function formatChartAxisTime(value: unknown, period: ChartPeriod): string {
  const text = typeof value === 'string' ? value : '';
  if (!isMinutePeriod(period)) return text;
  const match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(text);
  return match ? `${match[1].slice(5)}\n${match[2]}` : text;
}
