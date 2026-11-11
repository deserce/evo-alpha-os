export type StructureMonitorPayload = {
  trade_date: string | null;
  version: string;
  run: { status: string; updated_at: string } | null;
  freshness?: {
    status: 'CURRENT' | 'STALE';
    reason: 'quality_changed' | 'price_data_changed' | null;
  };
  counts: Record<string, number>;
  rows: Array<{
    symbol: string; name: string; status: string; reason: string;
    current_price: number | null;
    state: { stop_price?: number | null; waiting_for?: string[] };
  }>;
  events: Array<{
    event_id: string; symbol: string; name: string; event_type: string;
    payload: { reason?: string; price?: number; stop_price?: number };
  }>;
};

const qualityMarketNames = new Map([
  ['SH', '沪市'],
  ['SZ', '深市'],
  ['BSE', '北交所'],
  ['BJ', '北交所'],
  ['ALL', '全市场'],
]);

export function isStructureMonitorStale(
  data: StructureMonitorPayload | null | undefined,
): boolean {
  return data?.freshness?.status === 'STALE';
}

export function describeStructureReason(reason?: string): string {
  const fallback = '暂无可展示的原因说明';
  if (typeof reason !== 'string' || !reason.trim()) return fallback;
  const normalized = reason.trim();
  const [code, market] = normalized.split(':', 2);
  switch (code) {
    case 'stock_daily_quality_missing':
      return `${qualityMarketNames.get(market) || ''}日K质量检查结果缺失`;
    case 'stock_daily_quality_failed':
      return `${qualityMarketNames.get(market) || ''}日K质量检查未通过`;
    case 'target_daily_bar_missing':
      return '所选交易日缺少日K行情';
    case 'insufficient_history':
      return '历史日K数据不足';
    case 'history_bar_limit_exceeded':
      return '历史日K数量超出本次校验上限';
    case 'monitor_time_budget_exceeded':
      return '本次校验耗时超出上限，请稍后重试';
    case 'invalid_ohlcv':
      return '日K价格或成交量数据异常';
    case 'symbol_removed_from_all_pools':
      return '该股票已移出所有股票池';
  }
  // 机器码可能附带中文异常详情，不能因此原样展示。
  const machinePrefix = /^[a-z][a-z0-9_]*:|^[a-z][a-z0-9]*_/i.test(normalized);
  return !machinePrefix && /\p{Script=Han}/u.test(normalized) ? reason : fallback;
}

export function monitorSummary(data: StructureMonitorPayload | undefined, loading: boolean, failed: boolean): string {
  if (failed) return '读取失败';
  if (loading) return '读取中…';
  if (isStructureMonitorStale(data)) return '行情数据已更新，C结果待重新校验';
  if (!data?.run) return '所选日期未运行';
  const buys = data.events.filter((event) => event.event_type === 'BUY_CONFIRM').length;
  const sells = data.events.filter((event) => event.event_type === 'EXIT').length;
  return `观察 ${data.counts.OBSERVING || 0} · 模拟持有 ${data.counts.HOLDING || 0} · 买入 ${buys} · 卖出 ${sells} · 阻断 ${data.counts.BLOCKED || 0}`;
}
