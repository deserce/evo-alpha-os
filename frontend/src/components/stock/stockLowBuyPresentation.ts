export type LowBuyConsistency =
  | 'MATCHED'
  | 'DIVERGED'
  | 'TRACKING_ONLY'
  | 'SCAN_ONLY'
  | 'NO_DATA';

export type LowBuyFactorMap = Record<string, unknown>;

export type LowBuySnapshot = Record<string, unknown> & {
  watch_state?: string | null;
  setup_id?: string | null;
  rule_version?: number | null;
  current_price?: number | null;
  zone_low?: number | null;
  zone_high?: number | null;
  trigger_price?: number | null;
  intraday_trigger_price?: number | null;
  invalidation_price?: number | null;
  distance_to_trigger_pct?: number | null;
  reason?: string | null;
  data_status?: string | null;
  factors?: LowBuyFactorMap | null;
};

export type LowBuyEvent = Record<string, unknown> & {
  event_id?: string | number | null;
  event_type?: string | null;
  status?: string | null;
  title?: string | null;
  message?: string | null;
  payload?: Record<string, unknown> | null;
};

export type LowBuyDetailPayload = Record<string, unknown> & {
  symbol?: string | null;
  trade_date?: string | null;
  consistency?: LowBuyConsistency | string | null;
  tracking_snapshot?: LowBuySnapshot | null;
  scan_snapshot?: LowBuySnapshot | null;
  scan?: Record<string, unknown> | null;
  events?: LowBuyEvent[] | null;
};

export type LowBuyCardSource = 'matched' | 'tracking' | 'scan';

export interface LowBuyStatusCard {
  key: string;
  source: LowBuyCardSource;
  sourceLabel: string;
  state: string;
  stateLabel: string;
  pathCode: 'A' | 'B' | '-';
  pathLabel: string;
  setupId: string;
  ruleVersion: number | null;
  currentPrice: number | null;
  zoneLow: number | null;
  zoneHigh: number | null;
  triggerPrice: number | null;
  intradayTriggerPrice: number | null;
  invalidationPrice: number | null;
  distanceToTriggerPct: number | null;
  reason: string;
  dataStatus: string;
  factors: LowBuyFactorMap;
}

function factorMap(value: unknown): LowBuyFactorMap {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as LowBuyFactorMap
    : {};
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    const parsed = finiteNumber(value);
    if (parsed !== null) return parsed;
  }
  return null;
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    const normalized = String(value ?? '').trim();
    if (normalized) return normalized;
  }
  return '';
}

export function lowBuyStateLabel(value: unknown) {
  const normalized = String(value ?? '').toUpperCase();
  if (normalized === 'WATCHING') return '持续观察';
  if (normalized === 'IN_ZONE') return '进入回调区';
  if (normalized === 'TRIGGERED') return '确认触发';
  if (normalized === 'INVALIDATED') return '已经失效';
  if (normalized === 'UNKNOWN') return '数据异常';
  return '未知状态';
}

export function lowBuyEventStatusLabel(value: unknown) {
  const normalized = String(value ?? '').toUpperCase();
  if (normalized === 'UNREAD') return '待处理';
  if (normalized === 'ACKNOWLEDGED') return '已确认';
  if (normalized === 'SNOOZED') return '已延后';
  if (normalized === 'RESOLVED') return '已解决';
  if (normalized === 'HISTORICAL') return '历史回看';
  return '无提醒';
}

function setupPath(value: unknown) {
  if (value === 'TREND_PULLBACK') {
    return { code: 'A' as const, label: '路径 A · 强势回调' };
  }
  if (value === 'REPAIR_REVERSAL') {
    return { code: 'B' as const, label: '路径 B · 领先修复' };
  }
  return { code: '-' as const, label: '未形成 setup' };
}

function statusCard(
  primary: LowBuySnapshot,
  secondary: LowBuySnapshot | null,
  source: LowBuyCardSource,
): LowBuyStatusCard {
  const primaryFactors = factorMap(primary.factors);
  const secondaryFactors = factorMap(secondary?.factors);
  const factors = { ...secondaryFactors, ...primaryFactors };
  const state = firstText(primary.watch_state, secondary?.watch_state).toUpperCase();
  const path = setupPath(factors.setup_path);
  const sourceLabel = source === 'matched'
    ? '正式跟踪 / 规则回放'
    : source === 'tracking'
      ? '正式跟踪'
      : '规则回放';

  return {
    key: `${source}:${firstText(primary.setup_id, secondary?.setup_id, state)}`,
    source,
    sourceLabel,
    state,
    stateLabel: lowBuyStateLabel(state),
    pathCode: path.code,
    pathLabel: path.label,
    setupId: firstText(primary.setup_id, secondary?.setup_id),
    ruleVersion: firstNumber(primary.rule_version, secondary?.rule_version),
    currentPrice: firstNumber(primary.current_price, secondary?.current_price),
    zoneLow: firstNumber(primary.zone_low, secondary?.zone_low),
    zoneHigh: firstNumber(primary.zone_high, secondary?.zone_high),
    triggerPrice: firstNumber(primary.trigger_price, secondary?.trigger_price),
    intradayTriggerPrice: firstNumber(
      primary.intraday_trigger_price,
      secondary?.intraday_trigger_price,
    ),
    invalidationPrice: firstNumber(
      primary.invalidation_price,
      secondary?.invalidation_price,
    ),
    distanceToTriggerPct: firstNumber(
      primary.distance_to_trigger_pct,
      secondary?.distance_to_trigger_pct,
    ),
    reason: firstText(primary.reason, secondary?.reason),
    dataStatus: firstText(primary.data_status, secondary?.data_status),
    factors,
  };
}

export function buildLowBuyStatusCards(payload: LowBuyDetailPayload | null | undefined) {
  const tracking = payload?.tracking_snapshot ?? null;
  const scan = payload?.scan_snapshot ?? null;
  if (!tracking && !scan) return [];

  const sameState = Boolean(
    tracking
    && scan
    && firstText(tracking.watch_state).toUpperCase()
      === firstText(scan.watch_state).toUpperCase(),
  );
  const trackingSetup = firstText(tracking?.setup_id);
  const scanSetup = firstText(scan?.setup_id);
  const sameSetup = trackingSetup === scanSetup || (!trackingSetup && !scanSetup);

  if (tracking && scan && sameState && sameSetup) {
    return [statusCard(tracking, scan, 'matched')];
  }

  const cards: LowBuyStatusCard[] = [];
  if (tracking) cards.push(statusCard(tracking, null, 'tracking'));
  if (scan) cards.push(statusCard(scan, null, 'scan'));
  return cards;
}
