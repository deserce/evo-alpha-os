export type StructureMonitorPayload = {
  trade_date: string | null;
  version: string;
  run: { status: string; updated_at: string } | null;
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


export function monitorSummary(data: StructureMonitorPayload | undefined, loading: boolean, failed: boolean): string {
  if (failed) return '读取失败';
  if (loading) return '读取中…';
  if (!data?.run) return '所选日期未运行';
  const buys = data.events.filter((event) => event.event_type === 'BUY_CONFIRM').length;
  const sells = data.events.filter((event) => event.event_type === 'EXIT').length;
  return `观察 ${data.counts.OBSERVING || 0} · 模拟持有 ${data.counts.HOLDING || 0} · 买入 ${buys} · 卖出 ${sells} · 阻断 ${data.counts.BLOCKED || 0}`;
}
