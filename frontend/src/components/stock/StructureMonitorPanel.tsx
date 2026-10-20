'use client';

import { useQuery } from '@tanstack/react-query';
import { watchApi } from '@/lib/api';

import { monitorSummary } from '@/lib/structureMonitor';

const statusLabels: Record<string, string> = {
  WATCHING: '等待结构', OBSERVING: '观察中', HOLDING: '模拟持有',
  BLOCKED: '数据阻断', REMOVED: '已移出',
};
const eventLabels: Record<string, string> = {
  OBSERVE: '观察', BUY_CONFIRM: '买入确认', EXIT: '卖出确认',
  INVALIDATED: '观察失效', RISK_WARNING: '风险预警',
};
const price = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—';

export default function StructureMonitorPanel({ tradeDate, poolId, selectedSymbol, onSelectSymbol }: {
  tradeDate: string; poolId: string; selectedSymbol: string;
  onSelectSymbol: (symbol: string) => void;
}) {
  const query = useQuery({
    queryKey: ['structureMonitor', tradeDate, poolId],
    queryFn: () => watchApi.getStructureMonitor({ tradeDate, poolId }),
    enabled: Boolean(tradeDate && poolId), staleTime: 60_000,
    refetchOnWindowFocus: true, retry: 1,
  });
  const data = query.data;
  const summary = monitorSummary(data, query.isLoading, query.isError);
  const selectedRow = data?.rows.find((row) => row.symbol === selectedSymbol);
  const selectedEvent = data?.events.find((event) => event.symbol === selectedSymbol &&
    (event.event_type === 'BUY_CONFIRM' || event.event_type === 'EXIT'));
  return (
    <details className="shrink-0 border-b border-border-primary bg-bg-secondary/40 text-[11px]">
      <summary className="cursor-pointer px-3 py-1.5 text-text-secondary">
        <span className="font-semibold text-cyan-300">C盘后买卖监控</span>
        <span className="ml-3">{tradeDate} · {summary}</span>
        {selectedRow && <span className="ml-3 text-violet-200">
          当前 {selectedRow.name}：{selectedEvent ? eventLabels[selectedEvent.event_type] :
            statusLabels[selectedRow.status] || selectedRow.status}
          {selectedRow.state.stop_price != null ? ` · 失效线 ${price(selectedRow.state.stop_price)}` : ''}
        </span>}
      </summary>
      <div className="max-h-52 overflow-y-auto px-3 pb-2">
        <p className="mb-2 text-text-tertiary">当前分组 · 收盘日线判断 · 模拟持有，不代表真实账户仓位。图表开关只控制显示。未运行日期不会借用其他日期信号。</p>
        {query.isError && <button type="button" onClick={() => query.refetch()} className="text-cyan-300">重新读取C监控</button>}
        {data?.run && <p className="mb-2 text-text-tertiary">运行状态 {data.run.status} · 更新 {data.run.updated_at} · {data.counts.total || 0} 只</p>}
        {data?.events.map((event) => (
          <button key={event.event_id} type="button" onClick={() => onSelectSymbol(event.symbol)}
            className="mb-1 block w-full rounded border border-border-primary px-2 py-1 text-left text-text-secondary hover:border-cyan-400">
            <span className="mr-2 text-cyan-300">{eventLabels[event.event_type] || event.event_type}</span>
            {event.name} {event.symbol} · 信号价 {price(event.payload.price)} · {event.payload.reason}
          </button>
        ))}
        {data?.run && data.events.length === 0 && <p className="mb-2 text-text-tertiary">当日无新增买卖或观察转折。</p>}
        {data?.rows.map((row) => (
          <button key={row.symbol} type="button" onClick={() => onSelectSymbol(row.symbol)}
            className="block w-full border-b border-border-primary/40 py-1 text-left text-text-secondary hover:text-cyan-300">
            {row.name} {row.symbol} · {statusLabels[row.status] || row.status} · 收盘 {price(row.current_price)} · 失效线 {price(row.state.stop_price)}
            {row.reason && <span className="ml-2 text-amber-300">{row.reason}</span>}
            {!row.reason && row.state.waiting_for?.length ? <span className="ml-2 text-text-tertiary">{row.state.waiting_for.join('；')}</span> : null}
          </button>
        ))}
      </div>
    </details>
  );
}
