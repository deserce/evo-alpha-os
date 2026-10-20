export interface StockSidebarScope {
  poolId: number | string;
  poolName: string;
  members: { symbol: string; name?: string }[];
  date: string;
}

export interface StockSidebarUi {
  strategy?: { source: string; symbol: string; detailOpen: boolean };
  tab: 'info' | 'ai' | 'hints';
  hintsFilter: 'all' | 'buy' | 'sell' | 'risk' | 'wait' | 'blocked';
  assistantLink?: { date: string; symbol?: string; symbols?: string[]; filter: StockSidebarUi['hintsFilter']; previousFilter: StockSidebarUi['hintsFilter'] } | null;
  infoTab: string;
  infoHidden: boolean;
  financialMetric: string;
  scope: StockSidebarScope | null;
  scrollPositions: Record<string, number>;
  expanded: Record<string, boolean>;
}

export const DEFAULT_STOCK_SIDEBAR_UI: StockSidebarUi = {
  hintsFilter: 'all', tab: 'info', infoTab: 'finance', infoHidden: false, financialMetric: 'net_profit',
  scope: null, scrollPositions: {}, expanded: {},
};

/** Update the least recently saved entries without growing a session indefinitely. */
export function boundedSidebarRecord<T>(record: Record<string, T>, key: string, value: T): Record<string, T> {
  const entries = Object.entries(record).filter(([entry]) => entry !== key);
  entries.push([key, value]);
  return Object.fromEntries(entries.slice(-32));
}

export function patchStockSidebarUi(current: StockSidebarUi, patch: Partial<StockSidebarUi>): StockSidebarUi {
  const next = { ...current, ...patch };
  if (!['all', 'buy', 'sell', 'risk', 'wait', 'blocked'].includes(next.hintsFilter)) next.hintsFilter = current.hintsFilter;
  if (!['info', 'ai', 'hints'].includes(next.tab)) next.tab = current.tab;
  if (!['profile', 'finance', 'f10', 'announcements', 'funds', 'risk'].includes(next.infoTab)) next.infoTab = current.infoTab;
  if (!['total_revenue', 'net_profit', 'deduct_net_profit', 'eps', 'roe', 'net_margin', 'gross_margin', 'operating_cashflow_per_share'].includes(next.financialMetric)) next.financialMetric = current.financialMetric;
  if (patch.scope) {
    const seen = new Set<string>();
    next.scope = { ...patch.scope, members: patch.scope.members.filter((member) => {
      if (!/^\d{6}$/.test(member.symbol) || seen.has(member.symbol)) return false;
      seen.add(member.symbol);
      return true;
    }).slice(0, 300).map(({ symbol, name }) => ({ symbol, name })) };
  }
  next.scrollPositions = Object.fromEntries(Object.entries(next.scrollPositions)
    .filter(([, top]) => Number.isFinite(top)).slice(-32)
    .map(([key, top]) => [key, Math.min(100000, Math.max(0, top))]));
  next.expanded = Object.fromEntries(Object.entries(next.expanded).slice(-32));
  return next;
}
