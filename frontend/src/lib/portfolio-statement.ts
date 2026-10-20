export interface StatementTransaction {
  id: number;
  business_type: string;
  trade_date: string;
  symbol: string | null;
  name?: string | null;
  quantity: number | null;
  price_micros: number | null;
  gross_amount_cents: number | null;
  cash_delta_cents: number;
  fee_cents: number;
  tax_cents: number;
  reversal_of: number | null;
  is_reversed?: boolean;
  note: string | null;
}
export interface StatementTotals {
  buyQuantity: number; sellQuantity: number;
  buyAmount: number; sellAmount: number; fees: number; netCash: number; trades: number;
}
export interface StockStatement extends StatementTotals {
  symbol: string; name: string | null; latestDate: string;
}
const emptyTotals = (): StatementTotals => ({ buyQuantity: 0, sellQuantity: 0, buyAmount: 0, sellAmount: 0, fees: 0, netCash: 0, trades: 0 });
const numeric = (value: number | null): number => Number.isFinite(value) ? value ?? 0 : 0;
export function buildStatement(transactions: StatementTransaction[], year = '', month = '') {
  const bounded = transactions.slice(0, 10000);
  const reversedIds = new Set(bounded.filter(row => row.business_type === 'REVERSAL').map(row => row.reversal_of));
  const rows = bounded.map(row => ({ ...row, is_reversed: Boolean(row.is_reversed || reversedIds.has(row.id)) }))
    .sort((a, b) => b.trade_date.localeCompare(a.trade_date) || b.id - a.id);
  const years = [...new Set(rows.map(row => row.trade_date.slice(0, 4)))].sort().reverse();
  const filtered = rows.filter(row => (!year || row.trade_date.slice(0, 4) === year) && (!month || row.trade_date.slice(5, 7) === month));
  const months = new Map<string, StatementTotals>();
  const stocks = new Map<string, StockStatement>();
  for (const row of filtered) {
    const key = row.trade_date.slice(0, 7);
    if (!months.has(key)) months.set(key, emptyTotals());
    if (row.is_reversed || !['BUY', 'SELL'].includes(row.business_type)) continue;
    const monthly = months.get(key)!;
    let stock = row.symbol ? stocks.get(row.symbol) : undefined;
    if (!stock && row.symbol) {
      stock = { ...emptyTotals(), symbol: row.symbol, name: row.name ?? null, latestDate: row.trade_date };
      stocks.set(row.symbol, stock);
    }
    for (const target of stock ? [monthly, stock] : [monthly]) {
      if (row.business_type === 'BUY') {
        target.buyQuantity += numeric(row.quantity); target.buyAmount += numeric(row.gross_amount_cents);
      } else {
        target.sellQuantity += numeric(row.quantity); target.sellAmount += numeric(row.gross_amount_cents);
      }
      target.fees += numeric(row.fee_cents) + numeric(row.tax_cents);
      target.netCash += numeric(row.cash_delta_cents); target.trades += 1;
    }
  }
  return { rows: filtered, years, months: [...months].map(([month, totals]) => ({ month, ...totals })), stocks: [...stocks.values()], truncated: transactions.length > 10000 };
}
