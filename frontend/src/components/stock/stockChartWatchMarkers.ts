export type WatchChartMarkerType = 'IN_ZONE' | 'TRIGGERED' | 'INVALIDATED';
export type WatchChartPathCode = 'A' | 'B' | '-';

export interface WatchMarkerRow {
  trade_date?: string | null;
  marker_type?: string | null;
  current_price?: number | null;
  reason?: string | null;
  factors?: Record<string, unknown> | null;
  source?: string | null;
  rule_version?: number | null;
}

export interface WatchChartPoint {
  key: string;
  date: string;
  markerType: WatchChartMarkerType;
  pathCode: WatchChartPathCode;
  label: string;
  value: [string, number];
  reason: string;
  source: string;
  ruleVersion: number | null;
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * 将 Watch 历史扫描的状态转折点转为 ECharts 散点。
 * 后端已过滤冷却日，这里再去重以防重复渲染。
 */
export function buildWatchChartPoints(
  markers: WatchMarkerRow[],
  dates: string[],
  candles: Array<Array<number | null>>,
): WatchChartPoint[] {
  const dateIndexes = new Map(dates.map((tradeDate, index) => [tradeDate, index]));
  const seen = new Set<string>();
  const points: WatchChartPoint[] = [];

  markers.forEach((marker) => {
    const rawDate = String(marker.trade_date ?? '');
    const tradeDate = dateIndexes.has(rawDate) ? rawDate
      : dates.every((date) => date.length === 10) ? rawDate.slice(0, 10) : rawDate;
    const markerType = String(marker.marker_type ?? '').toUpperCase();
    if (!['IN_ZONE', 'TRIGGERED', 'INVALIDATED'].includes(markerType)) return;
    const index = dateIndexes.get(tradeDate);
    if (index === undefined) return;
    const key = `${tradeDate}:${markerType}`;
    if (seen.has(key)) return;

    const candleHigh = finiteNumber(candles[index]?.[3]);
    const currentPrice = finiteNumber(marker.current_price);
    const anchorPrice = candleHigh ?? currentPrice;
    if (anchorPrice === null || anchorPrice <= 0) return;

    seen.add(key);
    const typedMarker = markerType as WatchChartMarkerType;
    const setupPath = String(marker.factors?.setup_path ?? '');
    const pathCode: WatchChartPathCode = setupPath === 'TREND_PULLBACK'
      ? 'A'
      : setupPath === 'REPAIR_REVERSAL'
        ? 'B'
        : '-';
    const stateLabel = typedMarker === 'TRIGGERED' ? '确认'
      : typedMarker === 'INVALIDATED' ? '失效' : '观察';
    const multiplier = typedMarker === 'TRIGGERED' ? 1.06
      : typedMarker === 'INVALIDATED' ? 1.09 : 1.025;
    points.push({
      key,
      date: tradeDate,
      markerType: typedMarker,
      pathCode,
      label: pathCode === '-' ? stateLabel : `${pathCode}${stateLabel}`,
      value: [tradeDate, Number((anchorPrice * multiplier).toFixed(4))],
      reason: String(marker.reason ?? ''),
      source: String(marker.source ?? 'historical_scan'),
      ruleVersion: finiteNumber(marker.rule_version),
    });
  });

  return points.sort((left, right) => (
    (dateIndexes.get(left.date) ?? 0) - (dateIndexes.get(right.date) ?? 0)
  ));
}
