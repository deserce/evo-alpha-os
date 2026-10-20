export type DailyHintDirection = 'buy' | 'sell' | 'risk' | 'wait' | 'blocked';

export type DailyHintSignal = {
  symbol: string;
  name: string;
  source: string;
  label: string;
  direction: DailyHintDirection;
  reason?: string;
  family?: 'C' | 'chan' | 'watch' | 'other';
  confirmed?: boolean;
  candidateDirection?: 'buy' | 'sell';
  confirmedDate?: string;
  period?: string;
  currentPrice?: number;
  triggerPrice?: number;
  zoneLow?: number;
  zoneHigh?: number;
  stopPrice?: number;
  eventId?: string;
};

export type DailyHintItem = {
  symbol: string;
  name: string;
  sources: string[];
  signals: DailyHintSignal[];
  direction: DailyHintDirection;
  conflict: boolean;
  resonance: boolean;
  summary: string;
};

export type DailyHintsResult = {
  items: DailyHintItem[];
  counts: Record<DailyHintDirection | 'total', number>;
  summary: string;
  invalidCount: number;
  truncated: boolean;
};

export const DAILY_HINT_LIMITS = { input: 4096, stocks: 300, signalsPerStock: 32 };
const directions = new Set<DailyHintDirection>(['buy', 'sell', 'risk', 'wait', 'blocked']);

function boundedText(value: unknown, length: number): string {
  return typeof value === 'string' ? value.trim().slice(0, length) : '';
}

function normalizeSignal(value: unknown): DailyHintSignal | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  const symbol = boundedText(row.symbol, 20);
  const source = boundedText(row.source, 60);
  const label = boundedText(row.label, 100);
  if (!/^\d{6}$/.test(symbol) || !source || !label
    || !directions.has(row.direction as DailyHintDirection)) return null;
  const signal: DailyHintSignal = {
    symbol, source, label, direction: row.direction as DailyHintDirection,
    name: boundedText(row.name, 60) || symbol,
  };
  const reason = boundedText(row.reason, 500);
  const eventId = boundedText(row.eventId, 128);
  if (reason) signal.reason = reason;
  if (eventId) signal.eventId = eventId;
  if (typeof row.stopPrice === 'number' && Number.isFinite(row.stopPrice) && row.stopPrice > 0) {
    signal.stopPrice = row.stopPrice;
  }
  if (['C', 'chan', 'watch', 'other'].includes(String(row.family))) {
    signal.family = row.family as DailyHintSignal['family'];
  }
  signal.confirmed = row.confirmed === true;
  if (row.candidateDirection === 'buy' || row.candidateDirection === 'sell') signal.candidateDirection = row.candidateDirection;
  const confirmedDate = boundedText(row.confirmedDate, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(confirmedDate)
    && !Number.isNaN(Date.parse(confirmedDate))) signal.confirmedDate = confirmedDate;
  const period = boundedText(row.period, 30);
  if (period) signal.period = period;
  for (const key of ['currentPrice', 'triggerPrice', 'zoneLow', 'zoneHigh'] as const) {
    const price = row[key];
    if (typeof price === 'number' && Number.isFinite(price) && price > 0) signal[key] = price;
  }
  return signal;
}

/** Explicit source priority, not a trading probability or quality score. */
export function dailyHintPriority(item: DailyHintItem): number {
  const direction = item.direction === 'sell' ? 'sell' : 'buy';
  const c = item.signals.some(s => s.family === 'C' && s.direction === direction && s.confirmed);
  const chan = item.signals.some(s => s.family === 'chan'
    && (s.direction === direction || s.candidateDirection === direction));
  return item.resonance ? 6 : c && chan ? 5 : c ? 4 : chan ? 3
    : item.signals.some(s => s.family === 'watch') ? 1 : 0;
}

/** Local, bounded aggregation. Callers must select one date and pool before passing signals. */
export function aggregateDailyHints(input: unknown): DailyHintsResult {
  const counts: DailyHintsResult['counts'] = {
    buy: 0, sell: 0, risk: 0, wait: 0, blocked: 0, total: 0,
  };
  let invalidCount = Array.isArray(input) ? 0 : 1;
  let truncated = Array.isArray(input) && input.length > DAILY_HINT_LIMITS.input;
  const rows: unknown[] = Array.isArray(input) ? input : [];
  const grouped = new Map<string, {
    item: DailyHintItem; directions: Set<DailyHintDirection>; sources: Set<string>;
  }>();
  for (let index = 0; index < Math.min(rows.length, DAILY_HINT_LIMITS.input); index += 1) {
    const signal = normalizeSignal(rows[index]);
    if (!signal) { invalidCount += 1; continue; }
    let group = grouped.get(signal.symbol);
    if (!group) {
      if (grouped.size >= DAILY_HINT_LIMITS.stocks) { truncated = true; continue; }
      group = {
        item: { symbol: signal.symbol, name: signal.name, sources: [], signals: [],
          direction: 'wait', conflict: false, resonance: false, summary: '' },
        directions: new Set(), sources: new Set(),
      };
      grouped.set(signal.symbol, group);
    }
    group.directions.add(signal.direction);
    if (group.item.name === signal.symbol) group.item.name = signal.name;
    if (group.sources.size < DAILY_HINT_LIMITS.signalsPerStock) {
      group.sources.add(signal.source);
    } else if (!group.sources.has(signal.source)) truncated = true;
    if (group.item.signals.length < DAILY_HINT_LIMITS.signalsPerStock) {
      group.item.signals.push(signal);
    } else truncated = true;
  }
  const items = [...grouped.values()].map(({ item, directions: flags, sources }) => {
    item.conflict = flags.has('buy') && flags.has('sell');
    item.sources = [...sources];
    item.direction = flags.has('blocked') ? 'blocked'
      : item.conflict || flags.has('risk') ? 'risk'
        : flags.has('sell') ? 'sell' : flags.has('buy') ? 'buy' : 'wait';
    item.summary = item.direction === 'blocked' ? '数据待校验，暂不据此行动'
      : item.conflict ? '买卖信号冲突，先核对各策略条件'
        : item.direction === 'risk' ? '存在风险或失效提示，优先核对风险'
          : item.direction === 'sell' ? '出现卖出关注，结合真实持仓和可卖数量核对'
            : item.direction === 'buy' ? '出现买入关注，核对确认条件与失效线'
              : '进入观察或回调区，等待确认';
    // Only explicit confirmed C and Chan signals on the same date qualify.
    if (item.direction === 'buy' || item.direction === 'sell') {
      const confirmed = item.signals.filter((signal) => signal.confirmed
        && signal.confirmedDate && signal.direction === item.direction);
      item.resonance = confirmed.some((signal) => signal.family === 'C'
        && confirmed.some((other) => other.family === 'chan'
          && other.confirmedDate === signal.confirmedDate));
    }
    counts[item.direction] += 1;
    return item;
  });
  const priority: Record<DailyHintDirection, number> = { buy: 0, sell: 1, risk: 2, wait: 3, blocked: 4 };
  items.sort((a, b) => priority[a.direction] - priority[b.direction]
    || dailyHintPriority(b) - dailyHintPriority(a));
  counts.total = items.length;
  const summary = items.length
    ? `买入关注 ${counts.buy} · 卖出关注 ${counts.sell} · 风险 ${counts.risk} · 等待确认 ${counts.wait} · 待校验 ${counts.blocked}`
    : invalidCount ? '信号数据格式异常，暂无可用提示' : '当前范围暂无提示';
  return { items, counts, summary: truncated ? `${summary}（数量已达展示上限）` : summary,
    invalidCount, truncated };
}
