import type { DailyHintSignal } from '../../lib/dailyHints';
import type { HintPriceContext } from './stockChartHintPrices';
import type { StructureCandidatePayload } from './structureCandidatePresentation';

/** Use only this chart's exact-date C response; never inherit Watchlist context. */
export function buildStructureHintPriceContext(
  payload: StructureCandidatePayload | undefined, symbol: string, date: string | undefined,
): HintPriceContext | undefined {
  if (!payload || !date || !/^\d{6}$/.test(symbol)
    || !/^\d{4}-\d{2}-\d{2}$/.test(date)
    || payload.state?.trade_date !== date || payload.history_end !== date
    || (payload.period && payload.period !== 'daily')
    || (payload.summary?.status && payload.summary.status !== 'ok')) return undefined;
  const signals: DailyHintSignal[] = [];
  const validPrice = (value: unknown): value is number => typeof value === 'number'
    && Number.isFinite(value) && value > 0;
  for (const marker of payload.markers.slice(-32)) {
    if (marker.trade_date !== date || !validPrice(marker.price)
      || !['BUY_CONFIRM', 'EXIT', 'RISK_WARNING', 'INVALIDATED'].includes(marker.marker_type)) continue;
    signals.push({ symbol, name: symbol, source: 'C', family: 'C',
      label: marker.marker_type, direction: marker.marker_type === 'BUY_CONFIRM' ? 'buy'
        : marker.marker_type === 'EXIT' ? 'sell' : 'risk',
      confirmed: marker.marker_type === 'BUY_CONFIRM',
      confirmedDate: marker.marker_type === 'BUY_CONFIRM' ? date : undefined,
      triggerPrice: marker.price, stopPrice: validPrice(marker.stop_price) ? marker.stop_price : undefined,
      period: 'daily' });
  }
  const state = payload.state;
  if (state.status === 'HOLDING' && validPrice(state.entry_price) && state.entry_date
    && /^\d{4}-\d{2}-\d{2}$/.test(state.entry_date)
    && state.entry_date <= date && !signals.some(signal => signal.direction === 'buy')) {
    signals.push({ symbol, name: symbol, source: 'C', family: 'C', label: '持有背景',
      direction: 'wait', confirmed: true, confirmedDate: state.entry_date,
      triggerPrice: state.entry_price, stopPrice: validPrice(state.stop_price) ? state.stop_price : undefined,
      period: 'daily' });
  }
  return signals.length ? { date, items: [{ symbol, signals: signals.slice(0, 8) }] } : undefined;
}
