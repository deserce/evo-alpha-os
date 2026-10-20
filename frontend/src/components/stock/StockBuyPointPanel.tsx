'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Crosshair, History, ShieldCheck, Zap } from 'lucide-react';
import { clsx } from 'clsx';
import { watchApi } from '@/lib/api';
import {
  CompactTable,
  Row,
  StageBadge,
  num,
  signalNames,
  text,
} from '@/components/workspace/CockpitPrimitives';
import {
  buildLowBuyStatusCards,
  lowBuyEventStatusLabel,
  type LowBuyDetailPayload,
  type LowBuyStatusCard,
} from '@/components/stock/stockLowBuyPresentation';
import { describeWatchFactors } from '@/components/stock/watchRulePresentation';

type BuyPointTab = 'low-buy' | 'strategy';

interface StockBuyPointPanelProps {
  symbol: string;
  selectedDate: string;
  signals: Row[];
  plans: Row[];
  latest?: Row;
  compact?: boolean;
}

function stateClass(state: string) {
  if (state === 'TRIGGERED') return 'border-success/45 bg-success/10 text-success';
  if (state === 'IN_ZONE') return 'border-warning/45 bg-warning/10 text-warning';
  if (state === 'INVALIDATED') return 'border-danger/45 bg-danger/10 text-danger';
  if (state === 'UNKNOWN') return 'border-brand-blue/45 bg-brand-blue/10 text-brand-blue-light';
  return 'border-border-primary bg-bg-primary text-text-secondary';
}

function sourceClass(source: LowBuyStatusCard['source']) {
  if (source === 'tracking') return 'text-brand-blue-light';
  if (source === 'scan') return 'text-warning';
  return 'text-success';
}

function priceRange(low: number | null, high: number | null) {
  if (low === null && high === null) return '-';
  return `${num(low, 2)} - ${num(high, 2)}`;
}

export default function StockBuyPointPanel({
  symbol,
  selectedDate,
  signals,
  plans,
  latest = {},
  compact = false,
}: StockBuyPointPanelProps) {
  const [activeTab, setActiveTab] = useState<BuyPointTab>('low-buy');
  const detailQuery = useQuery({
    queryKey: ['watchStockDetail', symbol, selectedDate || 'latest'],
    queryFn: () => watchApi.getStockDetail(symbol, selectedDate || undefined),
    enabled: Boolean(symbol),
    staleTime: 30_000,
  });
  const payload = (detailQuery.data ?? {}) as LowBuyDetailPayload;
  const cards = buildLowBuyStatusCards(payload);
  const latestEvent = payload.events?.[0];
  const strategyRows = plans.length ? plans : signals;
  const divergent = payload.consistency === 'DIVERGED';

  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-border-primary bg-bg-secondary/60">
      <div className="flex h-8 items-center justify-between gap-2 border-b border-border-secondary px-2">
        <div className="flex min-w-0 items-center gap-1 text-[10px] font-black text-text-tertiary">
          <Crosshair size={11} className="text-brand-blue-light" />
          <span>买点作战</span>
          {selectedDate ? <span className="font-mono font-normal">{selectedDate}</span> : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => setActiveTab('low-buy')}
            className={clsx(
              'inline-flex h-6 items-center gap-1 rounded border px-2 text-[10px] font-black transition-colors',
              activeTab === 'low-buy'
                ? 'border-brand-blue/60 bg-brand-blue/15 text-brand-blue-light'
                : 'border-border-primary bg-bg-primary/50 text-text-tertiary hover:text-white',
            )}
          >
            <Crosshair size={10} />
            低吸
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('strategy')}
            className={clsx(
              'inline-flex h-6 items-center gap-1 rounded border px-2 text-[10px] font-black transition-colors',
              activeTab === 'strategy'
                ? 'border-brand-blue/60 bg-brand-blue/15 text-brand-blue-light'
                : 'border-border-primary bg-bg-primary/50 text-text-tertiary hover:text-white',
            )}
          >
            <Zap size={10} />
            策略 {strategyRows.length || ''}
          </button>
        </div>
      </div>

      {activeTab === 'low-buy' ? (
        <div className={clsx('custom-scrollbar overflow-auto', compact ? 'max-h-[126px]' : 'max-h-[210px]')}>
          {detailQuery.isLoading ? (
            <div className="px-3 py-6 text-center text-[10px] text-text-tertiary">正在读取低吸状态...</div>
          ) : detailQuery.isError ? (
            <div className="flex items-center justify-center gap-1.5 px-3 py-6 text-[10px] text-danger">
              <AlertTriangle size={12} />
              低吸状态读取失败
            </div>
          ) : cards.length ? (
            <>
              {divergent ? (
                <div className="flex items-center gap-1.5 border-b border-warning/25 bg-warning/5 px-2.5 py-1.5 text-[9px] text-warning">
                  <AlertTriangle size={11} />
                  正式跟踪与规则回放状态不同
                </div>
              ) : null}
              <div className="divide-y divide-border-secondary/75">
                {cards.map((card) => {
                  const factorLabels = describeWatchFactors(card.factors).slice(1);
                  return (
                    <div key={card.key} className="px-2.5 py-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className={clsx('rounded border px-1.5 py-0.5 text-[9px] font-black', stateClass(card.state))}>
                          {card.stateLabel}
                        </span>
                        <span className={clsx('text-[9px] font-bold', sourceClass(card.source))}>{card.sourceLabel}</span>
                        <span className="text-[9px] text-text-secondary">{card.pathLabel}</span>
                        {card.ruleVersion !== null ? (
                          <span className="font-mono text-[8px] text-text-tertiary">v{card.ruleVersion}</span>
                        ) : null}
                        {card.source !== 'scan' && latestEvent ? (
                          <span className="ml-auto inline-flex items-center gap-1 text-[8px] text-text-tertiary">
                            <History size={9} />
                            {lowBuyEventStatusLabel(latestEvent.status)}
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-1.5 grid grid-cols-4 gap-1 text-[9px]">
                        <div><span className="text-text-tertiary">现价 </span><span className="font-mono text-white">{num(card.currentPrice, 2)}</span></div>
                        <div className="col-span-2"><span className="text-text-tertiary">回调区 </span><span className="font-mono text-white">{priceRange(card.zoneLow, card.zoneHigh)}</span></div>
                        <div><span className="text-text-tertiary">失效 </span><span className="font-mono text-danger">{num(card.invalidationPrice, 2)}</span></div>
                        <div><span className="text-text-tertiary">确认 </span><span className="font-mono text-success">{num(card.triggerPrice ?? card.intradayTriggerPrice, 2)}</span></div>
                        {factorLabels.slice(0, 3).map((label) => (
                          <span key={label} className="truncate text-[8px] text-text-secondary" title={label}>{label}</span>
                        ))}
                      </div>
                      {card.reason ? <div className="mt-1 truncate text-[9px] text-text-secondary" title={card.reason}>{card.reason}</div> : null}
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="px-3 py-6 text-center text-[10px] text-text-tertiary">当日未纳入低吸跟踪</div>
          )}
        </div>
      ) : (
        <div className={clsx('custom-scrollbar overflow-auto p-1.5', compact ? 'max-h-[126px]' : 'max-h-[210px]')}>
          {strategyRows.length ? (
            <CompactTable
              rows={strategyRows}
              limit={compact ? 3 : 5}
              minWidth={420}
              rowKey={(row, index) => `${text(row.signal_type)}-${index}`}
              columns={[
                { key: 'signal_type', label: '信号', render: (row) => signalNames[text(row.signal_type, '')] || text(row.signal_type) },
                { key: 'entry_price', label: '入场', align: 'right', render: (row) => num(row.entry_price, 2) },
                { key: 'current_stop_price', label: '止损', align: 'right', render: (row) => num(row.current_stop_price || row.stop_loss_price, 2) },
                { key: 'latest_close', label: '现价', align: 'right', render: (row) => num(row.latest_close || latest.close, 2) },
                { key: 'r_multiple', label: 'R', align: 'right', render: (row) => num(row.r_multiple) },
                { key: 'risk_status', label: '风险', render: (row) => <StageBadge value={row.risk_status || row.signal_status} /> },
              ]}
            />
          ) : (
            <div className="flex items-center justify-center gap-1.5 px-3 py-6 text-[10px] text-text-tertiary">
              <ShieldCheck size={11} />
              当日暂无策略买点或交易计划
            </div>
          )}
        </div>
      )}
    </section>
  );
}
