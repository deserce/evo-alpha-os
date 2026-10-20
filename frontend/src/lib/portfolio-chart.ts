import type { PortfolioPerformanceSnapshot } from './api';

export type EquityMetric = 'equity' | 'pnl' | 'return';
export type EquityRange = '1m' | '3m' | 'all';
export interface EquityPoint {
  date: string;
  equity: number | null;
  pnl: number | null;
  return: number | null;
  cash: number;
  market: number | null;
  dailyPnl: number | null;
  flow: number;
}
const valid = (value: number | null): value is number => value !== null && Number.isSafeInteger(value);

/** Daily PnL is already net of external cash flow; never subtract that flow twice. */
export function buildEquitySeries(snapshots: PortfolioPerformanceSnapshot[], range: EquityRange): EquityPoint[] {
  const sorted = [...snapshots].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date));
  if (!sorted.length) return [];
  const end = new Date(`${sorted.at(-1)!.snapshot_date}T12:00:00Z`);
  const cutoff = new Date(end);
  if (range !== 'all') {
    const day = cutoff.getUTCDate();
    cutoff.setUTCDate(1);
    cutoff.setUTCMonth(cutoff.getUTCMonth() - (range === '1m' ? 1 : 3));
    const monthEnd = new Date(Date.UTC(cutoff.getUTCFullYear(), cutoff.getUTCMonth() + 1, 0));
    cutoff.setUTCDate(Math.min(day, monthEnd.getUTCDate()));
  }
  const cutoffDate = cutoff.toISOString().slice(0, 10);
  const start = range === 'all' ? 0 : sorted.findIndex(row => row.snapshot_date >= cutoffDate);
  const visible = sorted.slice(Math.max(start, sorted.length - 400));
  const firstIndex = sorted.indexOf(visible[0]);
  let previous = firstIndex > 0 ? sorted[firstIndex - 1] : undefined;
  let chainValid = !!previous && previous.price_status === 'COMPLETE' && valid(previous.total_equity_cents);
  let cumulative = 0;
  let factor = 1;
  return visible.map((row, index) => {
    const equityValid = row.price_status === 'COMPLETE' && valid(row.total_equity_cents);
    if (index === 0 && row.performance_status === 'INITIAL' && equityValid) {
      chainValid = true;
    } else if (chainValid && row.performance_status === 'COMPLETE' && equityValid
      && valid(row.pnl_cents) && valid(row.return_ppb)
      && previous?.snapshot_date === row.return_base_date) {
      cumulative += row.pnl_cents;
      factor *= 1 + row.return_ppb / 1_000_000_000;
      if (!Number.isFinite(factor) || !Number.isSafeInteger(cumulative)) chainValid = false;
    } else {
      chainValid = false;
    }
    previous = row;
    return {
      date: row.snapshot_date,
      equity: equityValid ? row.total_equity_cents! / 100 : null,
      pnl: chainValid ? cumulative / 100 : null,
      return: chainValid ? (factor - 1) * 100 : null,
      cash: row.cash_cents / 100,
      market: valid(row.market_value_cents) ? row.market_value_cents / 100 : null,
      dailyPnl: valid(row.pnl_cents) ? row.pnl_cents / 100 : null,
      flow: row.net_external_flow_cents / 100,
    };
  });
}
