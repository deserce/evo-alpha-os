import type { ReportLinkedStock } from './store';

export type ReportFilter = 'all' | 'buy' | 'exit' | 'risk' | 'holding';
export type ReportGroup = 'all' | 'mrgc' | 'company' | 'watchlist' | 'overlap';
export type ReportOpportunity = { direction: 'buy' | 'sell'; level: 'resonance' | 'c_only' | 'chan_only' };
type SignalStock = { status: string; sector_name: string; events: { event_type: string }[];
  data_status?: string; group_ids?: string[]; opportunities?: ReportOpportunity[] };
type LinkStock = { symbol: string; name: string; sector_name: string; price: number | null;
  pct_chg: number | null; rps20: number | null; last_hit_date: string; hit_count: number; group_names?: string[] };

export function selectReportStocks<T extends SignalStock>(stocks: T[], filter: ReportFilter, sector = '', group: ReportGroup = 'all'): T[] {
  if (!['all', 'buy', 'exit', 'risk', 'holding'].includes(filter)) throw new Error('未知日报筛选');
  if (!['all', 'mrgc', 'company', 'watchlist', 'overlap'].includes(group)) throw new Error('未知日报分组');
  return stocks.filter((stock) => {
    const membership = new Set((stock.group_ids ?? ['mrgc']).filter((id) => ['mrgc', 'company', 'watchlist'].includes(id)));
    if (group === 'overlap' ? membership.size < 2 : group !== 'all' && !membership.has(group)) return false;
    if ((filter === 'buy' || filter === 'exit') && (stock.status === 'BLOCKED'
      || (stock.data_status !== undefined && stock.data_status !== 'OK'))) return false;
    if (sector && stock.sector_name !== sector) return false;
    const has = (type: string) => stock.events.some((event) => event.event_type === type);
    return filter === 'all' || (filter === 'holding' ? stock.status === 'HOLDING' && !has('BUY_CONFIRM')
      : filter === 'risk' ? has('RISK_WARNING') || has('INVALIDATED')
      : stock.opportunities !== undefined
        ? stock.opportunities.some((opportunity) => opportunity.direction === (filter === 'buy' ? 'buy' : 'sell'))
        : has(filter === 'buy' ? 'BUY_CONFIRM' : 'EXIT'));
  });
}

export function reportLinkedStocks(stocks: LinkStock[]): ReportLinkedStock[] {
  return stocks.map((stock) => ({ symbol: stock.symbol, name: stock.name, industry: stock.sector_name,
    close: stock.price, pct_chg: stock.pct_chg, rps_20: stock.rps20,
    note: [stock.group_names?.join(' / '), stock.last_hit_date
      ? `最近命中 ${stock.last_hit_date} · 20日命中 ${stock.hit_count} 次` : 'MRGC 未命中'].filter(Boolean).join(' · '),
  }));
}

export function parseReportLink(search: string): { date: string; symbol: string } | null {
  const query = new URLSearchParams(search);
  const date = query.get('trade_date') || '';
  const symbol = query.get('symbol') || '';
  if (query.get('pool_id') !== 'smart_mrgc_20d' || !/^\d{6}$/.test(symbol) || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const parsed = new Date(`${date}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date || date < '2023-01-01') return null;
  return { date, symbol };
}


export const REPORT_LINKED_POOL_ID = 'report_linked';

export function reportContextPool(context: { date: string; label: string; stocks: readonly unknown[] } | null, date: string) {
  return context?.date === date ? { pool_id: REPORT_LINKED_POOL_ID,
    name: `日报联动 · ${context.label}`, stock_count: context.stocks.length, is_report: true,
    monitor_enabled: 0 } : null;
}

export function parseStockDetailDate(search: string): string | null {
  const value = new URLSearchParams(search).get('trade_date') || '';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value < '2023-01-01') return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value ? value : null;
}
