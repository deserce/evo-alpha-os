import type { ChartPeriod } from '../../lib/chartPeriods.ts';

/** Single-line ticks leave full timestamps to the crosshair and tooltip. */
export function formatStockTimeTick(value: unknown, period: ChartPeriod): string {
  const text = typeof value === 'string' ? value : '';
  if (period !== '30min' && period !== '120min') return text;
  const match = /^\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(text);
  return match ? `${match[1]} ${match[2]}` : text;
}

export function buildStockTimeAxisLayout(containerHeight: number) {
  const height = Number.isFinite(containerHeight) && containerHeight > 0
    ? containerHeight : 600;
  return {
    bottomPercent: 56 / height * 100,
    axisLabel: {
      interval: 'auto' as const,
      hideOverlap: true,
      showMinLabel: false,
      showMaxLabel: false,
      margin: 10,
      fontSize: 10,
      fontFamily: 'monospace',
      color: '#8ea0bd',
    },
    slider: {
      bottom: 6,
      height: 12,
      left: 44,
      right: 46,
      showDetail: false,
      showDataShadow: false,
    },
  };
}
