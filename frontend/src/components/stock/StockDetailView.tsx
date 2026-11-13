'use client';

import { TopbarTradeDate } from '@/components/layout/TopbarTradeDate';
import { useEffect, useRef, useState } from 'react';
import { parseStockDetailDate } from '@/lib/mrgcReportSelection';
import { stockDetailReturn } from '@/lib/stockDetailReturn';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { quantApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import { AllWatchlistDailyHints, StockDetailPanel } from '@/components/stock/StockWorkspace';
import type { DailyHintsContext } from './DailyHintsPanel';
import { rows } from '@/components/workspace/CockpitPrimitives';

export default function StockDetailView({ symbol, onClose, onSelectStock }: { symbol: string; onClose?: () => void; onSelectStock?: (symbol: string) => void }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const source = searchParams.get('returnTo');
  const returnTo = stockDetailReturn(source);
  const { globalDate, setGlobalDate, setSelectedStock } = useGlobalStore();
  useEffect(() => {
    if (onClose) return;
    setSelectedStock(symbol);
    try {
      window.localStorage.setItem('evoalpha.stock.symbol', symbol);
    } catch {
      // Global state still preserves navigation when browser storage is unavailable.
    }
  }, [symbol, setSelectedStock, onClose]);
  const appliedLink = useRef(false);
  useEffect(() => {
    if (onClose || appliedLink.current) return;
    appliedLink.current = true;
    const linkedDate = parseStockDetailDate(window.location.search);
    if (linkedDate) setGlobalDate(linkedDate);
  }, [setGlobalDate, onClose]);

  const datesQuery = useQuery({ queryKey: ['quantDates'], queryFn: () => quantApi.getDates(160) });
  const appliedLatestResearch = useRef(false);
  const latestTradeDate = datesQuery.data?.latest;
  useEffect(() => {
    if (onClose || appliedLatestResearch.current || !returnTo.startsWith('/a/research')
      || searchParams.get('trade_date') || !latestTradeDate) return;
    appliedLatestResearch.current = true;
    setGlobalDate(latestTradeDate);
  }, [returnTo, searchParams, latestTradeDate, setGlobalDate, onClose]);
  const dates = rows<string>(datesQuery.data?.dates);
  const linkedDate = onClose ? parseStockDetailDate(searchParams.toString()) : '';
  const selectedDate = linkedDate || (onClose && returnTo.startsWith('/a/research') ? latestTradeDate : globalDate) || latestTradeDate || '';
  const dateOptions = selectedDate && !dates.includes(selectedDate) ? [selectedDate, ...dates] : dates;

  const [unifiedHints, setUnifiedHints] = useState<DailyHintsContext>();
  const selectSymbol = onSelectStock ?? ((nextSymbol: string) => router.replace(`/a/stock/${nextSymbol}?returnTo=${encodeURIComponent(returnTo)}`));
  const dailyHintsPanel = <AllWatchlistDailyHints date={selectedDate}
    onSelectSymbol={selectSymbol} onContext={setUnifiedHints} />;

  return (
    <div className="flex h-[calc(100dvh-var(--evo-nav-height,64px))] min-h-0 flex-col">
      {/* 顶部工具栏 */}
      <div className="evo-page-header mx-3 mb-1.5 mt-2.5 flex shrink-0 flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onClose ?? (() => router.push(returnTo))}
          className="flex h-8 items-center gap-1 rounded-md border border-border-primary bg-bg-primary px-2 text-[11px] font-bold text-text-secondary transition-colors hover:border-brand-blue/60 hover:text-brand-blue-light"
          title="返回"
        >
          <ArrowLeft size={14} />
          {onClose ? '关闭' : '返回'}
        </button>
        <div className="min-w-0">
          <h1 className="evo-page-title truncate">
            个股详情 <span className="font-mono text-text-tertiary">{symbol}</span>
          </h1>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {!onClose && dates.length > 0 && (
            <TopbarTradeDate value={selectedDate} dates={dateOptions} onChange={setGlobalDate} />
          )}
        </div>
      </div>

      {/* 主内容区 - 纯个股详情面板 */}
      <main className="min-h-0 flex-1 overflow-hidden p-2">
        <StockDetailPanel symbol={symbol} selectedDate={selectedDate} forceResearchAsOf={returnTo.startsWith('/a/research') && Boolean(searchParams.get('trade_date'))} onSelectSymbol={selectSymbol} dailyHintsPanel={dailyHintsPanel} sidebarScopeKey="__all_watchlist__" unifiedHints={unifiedHints && unifiedHints.date === selectedDate && unifiedHints.poolName === 'Watchlist全部' ? unifiedHints : undefined} />
      </main>
    </div>
  );
}
