'use client';

import { useEffect, useRef, useState } from 'react';
import { parseStockDetailDate } from '@/lib/mrgcReportSelection';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { quantApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import { AllWatchlistDailyHints, StockDetailPanel } from '@/components/stock/StockWorkspace';
import type { DailyHintsContext } from './DailyHintsPanel';
import { rows } from '@/components/workspace/CockpitPrimitives';

export default function StockDetailView({ symbol }: { symbol: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const source = searchParams.get('returnTo');
  const returnTo = source === '/a/strategy' ? '/a/strategy' : '/a/watchlist';
  const { globalDate, setGlobalDate, setSelectedStock } = useGlobalStore();
  useEffect(() => {
    setSelectedStock(symbol);
    try {
      window.localStorage.setItem('evoalpha.stock.symbol', symbol);
    } catch {
      // Global state still preserves navigation when browser storage is unavailable.
    }
  }, [symbol, setSelectedStock]);
  const appliedLink = useRef(false);
  useEffect(() => {
    if (appliedLink.current) return;
    appliedLink.current = true;
    const linkedDate = parseStockDetailDate(window.location.search);
    if (linkedDate) setGlobalDate(linkedDate);
  }, [setGlobalDate]);

  const datesQuery = useQuery({ queryKey: ['quantDates'], queryFn: () => quantApi.getDates(160) });
  const dates = rows<string>(datesQuery.data?.dates);
  const selectedDate = globalDate || datesQuery.data?.latest || '';
  const dateOptions = selectedDate && !dates.includes(selectedDate) ? [selectedDate, ...dates] : dates;

  const [unifiedHints, setUnifiedHints] = useState<DailyHintsContext>();
  const selectSymbol = (nextSymbol: string) => router.replace(`/a/stock/${nextSymbol}?returnTo=${encodeURIComponent(returnTo)}`);
  const dailyHintsPanel = <AllWatchlistDailyHints date={selectedDate}
    onSelectSymbol={selectSymbol} onContext={setUnifiedHints} />;

  return (
    <div className="flex h-[calc(100dvh-var(--evo-nav-height,64px))] min-h-0 flex-col">
      {/* 顶部工具栏 */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border-primary bg-bg-secondary/40 px-3 py-2">
        <button
          type="button"
          onClick={() => router.push(returnTo)}
          className="flex h-8 items-center gap-1 rounded-md border border-border-primary bg-bg-primary px-2 text-[11px] font-bold text-text-secondary transition-colors hover:border-brand-blue/60 hover:text-brand-blue-light"
          title="返回"
        >
          <ArrowLeft size={14} />
          返回
        </button>
        <div className="min-w-0">
          <h1 className="truncate text-[14px] font-black text-white">
            个股详情 <span className="font-mono text-text-tertiary">{symbol}</span>
          </h1>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {dates.length > 0 && (
            <select
              value={selectedDate}
              onChange={(event) => setGlobalDate(event.target.value)}
              className="h-8 rounded-md border border-border-primary bg-bg-primary px-2 text-[11px] font-mono text-text-primary outline-none"
            >
              {dateOptions.map((date) => (
                <option key={date} value={date}>{date}</option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* 主内容区 - 纯个股详情面板 */}
      <main className="min-h-0 flex-1 overflow-hidden p-2">
        <StockDetailPanel symbol={symbol} selectedDate={selectedDate} onSelectSymbol={selectSymbol} dailyHintsPanel={dailyHintsPanel} sidebarScopeKey="__all_watchlist__" unifiedHints={unifiedHints && unifiedHints.date === selectedDate && unifiedHints.poolName === 'Watchlist全部' ? unifiedHints : undefined} />
      </main>
    </div>
  );
}
