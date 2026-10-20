export type StructureMarkerType = 'OBSERVE' | 'BUY_CONFIRM' | 'RISK_WARNING' | 'EXIT' | 'INVALIDATED';

export interface StructureMarker {
  trade_date: string;
  marker_type: StructureMarkerType;
  price: number;
  reason: string;
  stop_price?: number | null;
  setup_path?: string;
  factors?: Record<string, unknown> & {
    checks?: Array<{ dimension: string; label: string; value: string; passed: boolean | null }>;
  };
}

export interface StructureCandidatePayload {
  version: string;
  period?: string;
  experimental: boolean;
  markers: StructureMarker[];
  summary: Record<string, unknown>;
  history_start?: string | null;
  history_end?: string | null;
  config?: Record<string, number>;
  state?: {
    status: string; trade_date: string | null; stop_price: number | null;
    entry_date: string | null; entry_price: number | null; waiting_for: string[];
    factors: StructureMarker['factors'];
  };
  trades: Array<{
    entry_date: string; entry_price: number; exit_date: string | null;
    exit_price: number | null; status: 'OPEN' | 'CLOSED'; holding_bars: number;
    mfe_pct: number; mae_pct: number; return_pct: number | null;
    exit_reason?: string; mark_to_market_pct?: number;
  }>;
}

export const STRUCTURE_LABELS: Record<StructureMarkerType, string> = {
  OBSERVE: 'C观察', BUY_CONFIRM: 'C买确认', RISK_WARNING: 'C风险',
  EXIT: 'C卖出', INVALIDATED: 'C失效',
};

export interface StrategyVisibility {
  A: boolean; B: boolean; C: boolean; observe: boolean; buy: boolean; sell: boolean;
}

export const DEFAULT_STRATEGY_VISIBILITY: StrategyVisibility = {
  A: false, B: false, C: true, observe: true, buy: true, sell: true,
};

export function isSignalVisible(visibility: StrategyVisibility, path: 'A' | 'B' | 'C' | '-', type: string) {
  if (path === '-' ? !visibility.A && !visibility.B : !visibility[path]) return false;
  if (type === 'OBSERVE' || type === 'IN_ZONE') return visibility.observe;
  if (type === 'BUY_CONFIRM' || type === 'TRIGGERED') return visibility.buy;
  if (['EXIT', 'INVALIDATED', 'RISK_WARNING'].includes(type)) return visibility.sell;
  return false;
}

export function structureVersionLabel(version: string) {
  return version.match(/v\d+$/)?.[0] ?? '实验版';
}

export const STRUCTURE_COLORS: Record<StructureMarkerType, string> = {
  OBSERVE: '#c4b5fd', BUY_CONFIRM: '#a78bfa', RISK_WARNING: '#fbbf24',
  EXIT: '#fb7185', INVALIDATED: '#94a3b8',
};

export function buildStructurePoints(markers: StructureMarker[], dates: string[], candles: Array<Array<number | null>>) {
  const indexes = new Map(dates.map((date, index) => [date, index]));
  const seen = new Set<string>();
  return markers.flatMap((marker) => {
    const index = indexes.get(marker.trade_date);
    const key = `${marker.trade_date}:${marker.marker_type}`;
    if (index === undefined || seen.has(key) || !(marker.marker_type in STRUCTURE_LABELS)) return [];
    if (!Number.isFinite(marker.price) || marker.price <= 0) return [];
    const below = marker.marker_type === 'OBSERVE' || marker.marker_type === 'BUY_CONFIRM';
    const anchor = candles[index]?.[below ? 2 : 3];
    const price = typeof anchor === 'number' && Number.isFinite(anchor) && anchor > 0 ? anchor : marker.price;
    seen.add(key);
    return [{ ...marker, key, label: STRUCTURE_LABELS[marker.marker_type],
      value: [marker.trade_date, price * (below ? 0.94 : 1.14)] as [string, number] }];
  });
}

function escapeHtml(text: unknown) {
  return String(text ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}

export function signalTooltip(lines: Array<{ label: string; reason: string; source?: string }>) {
  return lines.map((line) => `<div style="margin-top:5px;max-width:320px;white-space:normal">` +
    `<b>${escapeHtml(line.label)}</b> ${escapeHtml(line.source ?? '')}<br/>${escapeHtml(line.reason)}</div>`).join('');
}
