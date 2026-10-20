'use client';

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bot, CandlestickChart, Crosshair, X } from 'lucide-react';
import { quantApi, stockApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import StockBuyPointPanel from '@/components/stock/StockBuyPointPanel';
import StockChart from '@/components/stock/StockChart';
import MrgcDailyReportPanel from '@/components/stock/MrgcDailyReportPanel';
import { StockEnrichmentPanel, StockPoolAddMenu } from '@/components/stock/StockWorkspace';
import {
  CockpitPanel,
  ProgressLine,
  Row,
  StageBadge,
  clamp,
  num,
  pct,
  rows,
  stageLabels,
  text,
  toNumber,
} from '@/components/workspace/CockpitPrimitives';

function compactPercent(value: unknown) {
  if (value === null || value === undefined || value === '') return '-';
  return pct(value, 2);
}

export default function StockDrawer() {
  const { drawerSymbol, drawerOpen, closeDrawer, openDrawer, globalDate,
    reportOpen, setReportOpen } = useGlobalStore();
  const [infoPanelHidden, setInfoPanelHidden] = useState(false);
  const [mobileTab, setMobileTab] = useState<'market' | 'analysis'>('market');

  const datesQuery = useQuery({
    queryKey: ['drawerDates'],
    queryFn: () => quantApi.getDates(80),
    staleTime: 60_000,
    enabled: drawerOpen && !globalDate,
  });
  const selectedDate = globalDate || datesQuery.data?.latest || '';

  const infoQuery = useQuery({
    queryKey: ['drawerStockInfo', drawerSymbol],
    queryFn: () => stockApi.getInfo(drawerSymbol),
    enabled: Boolean(drawerSymbol && drawerOpen),
  });
  const klineQuery = useQuery({
    queryKey: ['drawerStockChart', drawerSymbol, selectedDate],
    queryFn: () => stockApi.getChartData(drawerSymbol, 'daily', selectedDate || undefined, 260),
    enabled: Boolean(drawerSymbol && drawerOpen && selectedDate),
  });
  const lifecycleQuery = useQuery({
    queryKey: ['drawerLifecycle', selectedDate],
    queryFn: () => quantApi.getLifecycle({ tradeDate: selectedDate, limit: 1000 }),
    enabled: Boolean(selectedDate && drawerOpen),
  });
  const signalsQuery = useQuery({
    queryKey: ['drawerSignals', selectedDate],
    queryFn: () => quantApi.getSignals({ tradeDate: selectedDate, limit: 1000 }),
    enabled: Boolean(selectedDate && drawerOpen),
  });
  const plansQuery = useQuery({
    queryKey: ['drawerPlans', selectedDate],
    queryFn: () => quantApi.getTradePlans({ tradeDate: selectedDate, limit: 1000 }),
    enabled: Boolean(selectedDate && drawerOpen),
  });

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeDrawer();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawerOpen, closeDrawer]);

  const info = infoQuery.data ?? {};
  const kline = rows<Row>(klineQuery.data?.rows);
  const lifecycle = rows<Row>(lifecycleQuery.data?.rows).find((row) => row.symbol === drawerSymbol) ?? {};
  const signals = rows<Row>(signalsQuery.data?.rows).filter((row) => row.symbol === drawerSymbol);
  const plans = rows<Row>(plansQuery.data?.rows).filter((row) => row.symbol === drawerSymbol);
  const latest = kline[kline.length - 1] ?? {};
  const prev = kline[kline.length - 2] ?? {};
  const displayName = text(info.name || lifecycle.name || drawerSymbol, drawerSymbol);
  const price = latest.close ?? plans[0]?.latest_close ?? signals[0]?.entry_price;
  const pctChg = latest.pct_chg;

  if (!drawerOpen) return null;

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        onClick={closeDrawer}
      />

      {/* Drawer */}
      <aside
        className="fixed right-0 top-0 z-50 flex h-screen w-full max-w-[920px] flex-col overflow-hidden border-l border-border-primary bg-bg-secondary shadow-2xl"
        style={{ animation: 'drawerSlideIn 0.25s ease-out' }}
      >
        <style>{`
          @keyframes drawerSlideIn {
            from { transform: translateX(100%); }
            to { transform: translateX(0); }
          }
        `}</style>

        {/* Header */}
        <div className="relative z-30 flex shrink-0 items-center justify-between gap-3 overflow-visible border-b border-border-primary bg-bg-tertiary/60 px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-black text-white">{displayName}</h2>
              <span className="font-mono text-xs text-text-secondary">{drawerSymbol}</span>
              <StageBadge value={lifecycle.lifecycle_state} />
            </div>
            <div className="mt-1 flex items-end gap-3">
              <span className={toNumber(pctChg) >= 0 ? 'font-mono text-xl font-black text-danger' : 'font-mono text-xl font-black text-success'}>
                {num(price, 2)}
              </span>
              <span className={toNumber(pctChg) >= 0 ? 'font-mono text-xs text-danger' : 'font-mono text-xs text-success'}>
                {compactPercent(pctChg)}
              </span>
              <span className="text-[10px] text-text-tertiary">前收 {num(prev.close, 2)}</span>
            </div>
          </div>
          <div className="flex shrink-0 items-start gap-2">
            <button type="button" aria-pressed={reportOpen}
              onClick={() => { setReportOpen(!reportOpen); setMobileTab(reportOpen ? 'market' : 'analysis'); }}
              className="flex h-8 items-center gap-1 rounded-lg border border-border-primary bg-bg-primary px-2 text-xs text-brand-blue-light">
              <Bot size={14} />{reportOpen ? '恢复资料' : 'AI 助手'}
            </button>
            <StockPoolAddMenu symbol={drawerSymbol} label="加入池" />
            <button
              type="button"
              onClick={closeDrawer}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border-primary bg-bg-primary text-text-secondary hover:text-text-primary"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="custom-scrollbar flex-1 space-y-3 overflow-y-auto p-3">
          {/* State badges */}
          <div className="flex flex-wrap gap-1.5">
            {[
              { label: '市场', value: lifecycle.market_state },
              { label: '行业', value: lifecycle.industry_state },
              { label: '阶段', value: lifecycle.lifecycle_state },
              { label: '结构', value: lifecycle.structure_state },
            ].map((item) => (
              <div key={item.label} className="flex items-center gap-1.5 rounded-md border border-border-primary bg-bg-primary/60 px-2.5 py-1.5">
                <span className="text-[10px] text-text-tertiary">{item.label}</span>
                <StageBadge value={item.value || '?'} />
              </div>
            ))}
          </div>

          {/* K-line chart */}
          <CockpitPanel title="K线" icon={CandlestickChart}>
            {reportOpen && <div role="tablist" aria-label="个股内容" className="mb-2 flex gap-2 md:hidden">
              {([{ id: 'market', label: '行情' }, { id: 'analysis', label: 'AI分析' }] as const).map((tab) => (
                <button key={tab.id} type="button" role="tab" aria-selected={mobileTab === tab.id}
                  onClick={() => setMobileTab(tab.id)}
                  className={`rounded border px-3 py-1 text-xs ${mobileTab === tab.id ? 'border-brand-blue text-brand-blue-light' : 'border-border-primary text-text-secondary'}`}>
                  {tab.label}
                </button>
              ))}
            </div>}
            <div className={reportOpen ? 'grid min-w-0 gap-2 md:grid-cols-[minmax(0,1fr)_360px]'
              : infoPanelHidden ? 'grid gap-2 md:grid-cols-[minmax(0,1fr)_44px]' : 'grid gap-2 md:grid-cols-[minmax(0,1fr)_320px]'}>
              <div className={`h-[420px] min-h-0 min-w-0 ${reportOpen && mobileTab === 'analysis' ? 'hidden md:block' : ''}`}>
                <StockChart symbol={drawerSymbol} endDate={selectedDate} limit={180} />
              </div>
              {reportOpen ? <div className={`h-[420px] min-h-0 min-w-0 ${mobileTab === 'market' ? 'hidden md:block' : ''}`}>
                <MrgcDailyReportPanel date={selectedDate} currentSymbol={drawerSymbol}
                  onSelectSymbol={openDrawer} allowGroupFilter={false} />
              </div> : <StockEnrichmentPanel
                symbol={drawerSymbol}
                selectedDate={selectedDate}
                hidden={infoPanelHidden}
                onHiddenChange={setInfoPanelHidden}
                className="min-h-[420px]"
              />}
            </div>
          </CockpitPanel>

          <StockBuyPointPanel
            key={`${drawerSymbol}-${selectedDate}`}
            symbol={drawerSymbol}
            selectedDate={selectedDate}
            signals={signals}
            plans={plans}
            latest={latest}
          />

          {/* Lifecycle stages */}
          <CockpitPanel title="生命周期开关" icon={Crosshair}>
            <div className="grid grid-cols-3 gap-1.5">
              {stageLabels.map((label) => {
                const code = label.split(' ')[0];
                const active = lifecycle.lifecycle_state === code;
                return (
                  <div
                    key={label}
                    className={active ? 'rounded-md border border-brand-blue/60 bg-brand-blue/15 p-2' : 'rounded-md border border-border-primary bg-bg-primary/60 p-2'}
                  >
                    <div className={active ? 'font-mono text-xs font-black text-brand-blue-light' : 'font-mono text-xs font-black text-text-tertiary'}>{code}</div>
                    <div className="text-[9px] text-text-secondary">{label.split(' ')[1]}</div>
                  </div>
                );
              })}
            </div>
          </CockpitPanel>

          {/* Structure score progress */}
          {toNumber(lifecycle.structure_score) > 0 && (
            <CockpitPanel title="结构评估" icon={Crosshair}>
              <div className="space-y-2">
                <ProgressLine label="结构分" value={clamp(toNumber(lifecycle.structure_score))} tone="blue" right={num(lifecycle.structure_score, 0)} />
                <ProgressLine label="信号分" value={clamp(toNumber(lifecycle.signal_score))} tone="good" right={num(lifecycle.signal_score, 0)} />
              </div>
            </CockpitPanel>
          )}
        </div>
      </aside>
    </>
  );
}
