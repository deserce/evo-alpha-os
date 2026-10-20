import type { DailyHintSignal } from '../../lib/dailyHints';

export type HintPriceContext = {
  date: string;
  items: { symbol: string; signals: DailyHintSignal[] }[];
};
export type HintPriceLine = { price: number; label: string; color: string };
export type HintPricePoint = HintPriceLine & { date: string };

/** Read-only, bounded overlays for the exact stock/date; no inferred confirmations. */
export function buildHintPriceOverlay(
  context: HintPriceContext | undefined, symbol: string, date: string | undefined,
  period: string, dates: string[],
): { lines: HintPriceLine[]; points: HintPricePoint[] } {
  const empty = { lines: [], points: [] };
  if (!context || period !== 'daily' || !date || context.date !== date
    || !/^\d{6}$/.test(symbol) || !dates.includes(date)) return empty;
  const item = context.items.find(row => row.symbol === symbol);
  if (!item || item.signals.some(signal => signal.direction === 'blocked')) return empty;
  const lines = new Map<string, HintPriceLine>();
  const points = new Map<string, HintPricePoint>();
  const valid = (price: unknown): price is number => typeof price === 'number'
    && Number.isFinite(price) && price > 0;
  const merge = (target: HintPriceLine, label: string) => {
    if (!target.label.split(' / ').includes(label)) target.label += ` / ${label}`;
  };
  for (const signal of item.signals.slice(0, 8)) {
    if (signal.symbol !== symbol || signal.direction === 'blocked'
      || (signal.confirmedDate && signal.confirmedDate > date)
      || (signal.period && !['日K', '日线', 'daily'].includes(signal.period))) continue;
    const source = (signal.family === 'chan' ? '缠论候选' : signal.source).slice(0, 20);
    if (valid(signal.triggerPrice)) {
      const label = `${source} ${signal.family === 'chan' ? '候选拐点' : signal.family === 'watch' ? '触发价' : '信号价'}`;
      const key = signal.triggerPrice.toFixed(2);
      const existing = lines.get(key);
      if (existing) merge(existing, label);
      else if (lines.size < 12) lines.set(key, { price: signal.triggerPrice, label, color: '#56d8ff' });
      if (signal.confirmed && signal.family !== 'chan' && signal.confirmedDate
        && signal.confirmedDate <= date && dates.includes(signal.confirmedDate)) {
        const pointKey = `${signal.confirmedDate}:${key}`;
        const pointLabel = `${source} 确认 ${signal.confirmedDate}`;
        const existingPoint = points.get(pointKey);
        if (existingPoint) merge(existingPoint, pointLabel);
        else points.set(pointKey, { date: signal.confirmedDate, price: signal.triggerPrice,
          label: pointLabel, color: '#34d399' });
      }
    }
    if (valid(signal.stopPrice)) {
      const key = signal.stopPrice.toFixed(2);
      const label = `${source} 失效线`;
      const existing = lines.get(key);
      if (existing) { merge(existing, label); existing.color = '#fb7185'; }
      else if (lines.size < 12) lines.set(key, { price: signal.stopPrice, label, color: '#fb7185' });
    }
  }
  return { lines: [...lines.values()], points: [...points.values()] };
}
