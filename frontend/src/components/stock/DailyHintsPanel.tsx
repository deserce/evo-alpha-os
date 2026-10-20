'use client';

import { useEffect, useMemo, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { watchApi } from '@/lib/api';
import { aggregateDailyHints, type DailyHintSignal } from '@/lib/dailyHints';
import { loadReport } from './MrgcDailyReportPanel';
import { useGlobalStore } from '@/lib/store';

export type DailyHintsContext = {
  date: string; poolName: string; summary: string;
  items: ReturnType<typeof aggregateDailyHints>['items'];
};
const eventLabels: Record<string, string> = {
  BUY_CONFIRM: 'C 买入确认', EXIT: 'C 模拟卖出确认', OBSERVE: 'C 观察',
  INVALIDATED: 'C 结构失效', RISK_WARNING: 'C 风险预警',
};
const categoryStyles = {
  buy: { stripe: 'bg-emerald-400', label: 'bg-emerald-400/10 text-emerald-300' },
  sell: { stripe: 'bg-orange-400', label: 'bg-orange-400/10 text-orange-300' },
  risk: { stripe: 'bg-red-400', label: 'bg-red-400/10 text-red-300' },
  wait: { stripe: 'bg-slate-400', label: 'bg-slate-400/10 text-slate-300' },
  blocked: { stripe: 'bg-zinc-500', label: 'bg-zinc-500/10 text-zinc-300' },
};
const nextSteps = {
  buy: '核对确认条件和失效线，结合真实持仓决定下一步。',
  sell: '核对真实持仓、可卖数量与 T+1 状态；模拟卖点不等于账户卖出指令。',
  risk: '复核失效原因及已有持仓风险。',
  wait: '等待确认，进入回调区不等于买入确认。',
  blocked: '先补齐或核验数据，再判断买卖条件。',
};

export default function DailyHintsPanel({ date, poolId, poolName, members, watchSignals, legacyActions,
  onSelectSymbol, onContext, watchStatus = '' }: {
  date: string; poolId: string; poolName: string; members: { symbol?: unknown }[];
  watchSignals: DailyHintSignal[]; legacyActions: ReactNode;
  onSelectSymbol: (symbol: string) => void; onContext?: (context: DailyHintsContext) => void;
  watchStatus?: string;
}) {
  const setReportOpen = useGlobalStore((state) => state.setReportOpen);
  const setReportPanelUi = useGlobalStore((state) => state.setReportPanelUi);
  const selectedSymbol = useGlobalStore((state) => state.selectedStock);
  const assistantLink = useGlobalStore((state) => state.stockSidebarUi.assistantLink);
  const hintsTab = useGlobalStore((state) => state.stockSidebarUi.tab === 'hints');
  const filter = useGlobalStore((state) => state.stockSidebarUi.hintsFilter);
  const setSidebarUi = useGlobalStore((state) => state.setStockSidebarUi);
  const expanded = useGlobalStore((state) => state.stockSidebarUi.expanded);
  const setExpanded = useGlobalStore((state) => state.setSidebarExpanded);
  const expandKey = (part: string) => `hints-${part}:${poolId}:${date}`;
  const apiPoolId = ['__single__', '__all_watchlist__'].includes(poolId) ? undefined : poolId;
  const monitor = useQuery({ queryKey: ['structureMonitor', date, poolId],
    queryFn: () => watchApi.getStructureMonitor({ tradeDate: date, poolId: apiPoolId }),
    enabled: Boolean(date && poolId), staleTime: 60_000, gcTime: 600_000,
    retry: 1, refetchOnWindowFocus: false });
  const report = useQuery({ queryKey: ['mrgcDailyReport', date], queryFn: () => loadReport(date),
    enabled: Boolean(date), staleTime: 60_000, gcTime: 600_000,
    retry: false, refetchOnWindowFocus: false });
  const hints = useMemo(() => {
    const membership = new Set(members.map((row) => row.symbol).filter((s): s is string => typeof s === 'string'));
    const signals: DailyHintSignal[] = watchSignals.map((signal) => ({ ...signal, family: 'watch' }));
    const data = monitor.data;
    if (data?.trade_date === date && ['COMPLETED', 'BLOCKED'].includes(data.run?.status || '')) {
      for (const event of data.events.slice(0, 1200)) {
        const blocked = data.rows.slice(0, 300).some((row) => row.symbol === event.symbol && row.status === 'BLOCKED');
        const direction = blocked ? 'blocked' : event.event_type === 'BUY_CONFIRM' ? 'buy' : event.event_type === 'EXIT' ? 'sell'
          : ['INVALIDATED', 'RISK_WARNING'].includes(event.event_type) ? 'risk' : 'wait';
        signals.push({ symbol: event.symbol, name: event.name, source: 'C', direction,
          label: eventLabels[event.event_type] || event.event_type, reason: event.payload.reason,
          family: 'C', confirmedDate: date, confirmed: !blocked && ['BUY_CONFIRM', 'EXIT'].includes(event.event_type),
          currentPrice: data.rows.find((row) => row.symbol === event.symbol)?.current_price ?? undefined,
          triggerPrice: event.payload.price, stopPrice: event.payload.stop_price, eventId: event.event_id });
      }
      for (const row of data.rows.slice(0, 300).filter((row) => row.status === 'BLOCKED')) {
        signals.push({ symbol: row.symbol, name: row.name, source: 'C', direction: 'blocked',
          label: 'C 数据待校验', reason: row.reason, family: 'C', confirmed: false });
      }
    }
    const daily = report.data?.report;
    if (daily?.date === date) for (const stock of daily.stocks.slice(0, 300)) {
      if (stock.status === 'BLOCKED' || stock.data_status !== 'OK') {
        signals.push({ symbol: stock.symbol, name: stock.name, source: '日报',
          label: '日报数据待校验', direction: 'blocked', reason: stock.analysis });
      }
      for (const event of (stock.events || []).slice(0, 32)) {
        if (!['RISK_WARNING', 'INVALIDATED'].includes(event.event_type)) continue;
        if (signals.some((signal) => signal.symbol === stock.symbol && signal.source === 'C'
          && signal.label === eventLabels[event.event_type])) continue;
        signals.push({ symbol: stock.symbol, name: stock.name, source: 'C',
          label: eventLabels[event.event_type], direction: 'risk', reason: event.reason,
          stopPrice: stock.stop_price ?? undefined });
      }
      for (const event of (stock.events || []).slice(0, 32)) {
        if (!['BUY_CONFIRM', 'EXIT'].includes(event.event_type)) continue;
        const direction = event.event_type === 'BUY_CONFIRM' ? 'buy' : 'sell';
        if (signals.some((signal) => signal.symbol === stock.symbol && signal.family === 'C' && signal.direction === direction)) continue;
        const blocked = stock.status === 'BLOCKED' || stock.data_status !== 'OK';
        signals.push({ symbol: stock.symbol, name: stock.name, source: 'C', family: 'C',
          label: eventLabels[event.event_type], direction: blocked ? 'blocked' : direction,
          confirmedDate: date, confirmed: !blocked, currentPrice: stock.price ?? undefined,
          triggerPrice: event.price ?? undefined, stopPrice: stock.stop_price ?? undefined, reason: event.reason });
      }
      // These are first-visible ZIG candidates, not executable or non-repainting confirmations.
      for (const signal of (stock.chan_signals || []).slice(0, 32)) {
        if (signal.confirmation_date !== date || !['buy', 'sell'].includes(signal.direction)) continue;
        const blocked = stock.status === 'BLOCKED' || stock.data_status !== 'OK';
        signals.push({ symbol: stock.symbol, name: stock.name, source: '缠论', family: 'chan',
          label: signal.label, period: '日K', confirmedDate: signal.confirmation_date, confirmed: false,
          direction: blocked ? 'blocked' : 'wait', candidateDirection: signal.direction as 'buy' | 'sell',
          triggerPrice: signal.price, currentPrice: stock.price ?? undefined,
          stopPrice: stock.stop_price ?? undefined,
          reason: `首次可见候选；拐点日 ${signal.pivot_date}；ZIG 含未来函数，可能回绘，需核对图表。` });
      }
    }
    if (data?.trade_date === date && ['COMPLETED', 'BLOCKED'].includes(data.run?.status || '')) {
      for (const row of data.rows.slice(0, 300).filter((row) => ['HOLDING', 'OBSERVING'].includes(row.status))) {
        if (row.status === 'HOLDING' && !signals.some((signal) => signal.symbol === row.symbol && ['buy', 'sell', 'risk'].includes(signal.direction))) continue;
        signals.push({ symbol: row.symbol, name: row.name, source: 'C', family: 'C', direction: 'wait',
          label: row.status === 'HOLDING' ? 'C 模拟持有背景' : 'C 观察' , reason: row.reason,
          currentPrice: row.current_price ?? undefined, stopPrice: row.state.stop_price ?? undefined, confirmed: false });
      }
    }
    return aggregateDailyHints(signals.filter((signal) => membership.has(signal.symbol)));
  }, [members, watchSignals, monitor.data, report.data, date]);
  const reportMembers = new Set(report.data?.report?.stocks.map((stock) => stock.symbol) || []);
  const uncovered = report.data?.report ? members.filter((member) => typeof member.symbol === 'string'
    && !reportMembers.has(member.symbol)).length : 0;
  const availability = [watchStatus,
    uncovered ? `日报未覆盖当前分组 ${uncovered} 只，缠论机会尚不完整` : '',
    report.data?.job.status === 'STALE' ? '日报为旧版本，等待更新' : '',
    monitor.isError ? 'C 监控读取失败' : monitor.isLoading ? 'C 监控读取中'
      : !monitor.data?.run || monitor.data.trade_date !== date ? 'C 所选日期未运行'
      : monitor.data.run.status !== 'COMPLETED' ? `C 运行状态 ${monitor.data.run.status}` : '',
    report.isError ? '日报读取失败' : report.isLoading ? '日报读取中'
      : !report.data?.report ? '所选日期无日报存档'
      : report.data.report.status !== 'READY' ? '日报部分数据待校验' : '',
  ].filter(Boolean).join('；');
  const summary = [hints.summary, availability].filter(Boolean).join('；');
  const contextJson = JSON.stringify({ date, poolName, summary, items: hints.items });
  useEffect(() => { onContext?.(JSON.parse(contextJson) as DailyHintsContext); }, [contextJson, onContext]);
  const visible = hints.items.filter(item => (filter === 'all' || item.direction === filter) &&
    (assistantLink?.date !== date || !assistantLink.symbols || assistantLink.symbols.includes(item.symbol)));
  useEffect(() => {
    if (!hintsTab || assistantLink?.date !== date) return;
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        const panel = document.querySelector('[data-daily-hints]');
        const scroller = panel?.closest('[data-sidebar-scroll]');
        const target = assistantLink.symbol && /^\d{6}$/.test(assistantLink.symbol)
          ? panel?.querySelector(`[data-hint-symbol="${assistantLink.symbol}"]`) : panel;
        if (scroller instanceof HTMLElement && target instanceof HTMLElement) {
          scroller.scrollTop += target.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 40;
        }
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [hintsTab, assistantLink, date, visible.length]);
  const ordinals = new Map(visible.map((item, index) => [item.symbol, index + 1]));
  function renderCard(item: ReturnType<typeof aggregateDailyHints>['items'][number]) {
    const signals = [...item.signals].sort((a, b) =>
      Number(b.direction === item.direction) - Number(a.direction === item.direction)
      || ({ C: 3, chan: 2, watch: 1, other: 0 }[b.family || 'other']
        - { C: 3, chan: 2, watch: 1, other: 0 }[a.family || 'other']));
    const signal = signals[0];
    const labels = Array.from(new Set(signals.map(s => s.family === 'chan'
      ? `缠${s.candidateDirection === 'sell' ? '卖' : '买'}候选 · ${s.label}`
      : s.family === 'watch' ? `${s.source} · ${s.label}` : s.label))).slice(0, 3);
    const price = (value: number | undefined) => value === undefined ? '' : value.toFixed(2);
    const reason = signal?.reason || item.summary;
    const shortReason = reason.length > 56 ? `${reason.slice(0, 56)}…` : reason;
    return <article data-hint-symbol={item.symbol} data-hint-direction={item.direction} data-hint-ordinal={ordinals.get(item.symbol)} data-hint-selected={selectedSymbol === item.symbol} key={item.symbol}
      className={`relative min-w-0 rounded border p-2 pl-3.5 ${selectedSymbol === item.symbol ? 'border-cyan-300 bg-cyan-400/10 ring-1 ring-cyan-300/40' : 'border-border-primary bg-bg-primary/50'}`}>
      <span data-hint-stripe aria-hidden="true" className={`absolute bottom-2 left-1 top-2 w-0.5 rounded-full ${categoryStyles[item.direction].stripe}`} />
      <div data-hint-header className="flex min-w-0 items-center gap-2">
        <button type="button" aria-current={selectedSymbol === item.symbol ? 'true' : undefined} onClick={() => onSelectSymbol(item.symbol)} title={`${item.name} ${item.symbol}`} className="flex min-w-0 flex-1 items-center gap-1 font-bold text-text-primary hover:text-cyan-300">
          <span className="shrink-0 text-cyan-300">{ordinals.get(item.symbol)}.</span><span className="truncate">{item.name}</span><span className="shrink-0 font-mono">{item.symbol}</span>
        </button>
        <div className="flex shrink-0 gap-1 text-[10px]">
          <button type="button" onClick={() => onSelectSymbol(item.symbol)} className="rounded border border-border-primary px-1.5 py-0.5 text-cyan-300">看图</button>
          <button type="button" onClick={(event) => {
            const scroll = event.currentTarget.closest('[data-sidebar-scroll]');
            if (scroll instanceof HTMLElement) useGlobalStore.getState().setSidebarScroll(`hints:${poolId}:${date}`, scroll.scrollTop);
            onSelectSymbol(item.symbol); setReportPanelUi({ view: 'chat', answer: null }); setReportOpen(true);
          }} className="rounded border border-border-primary px-1.5 py-0.5 text-cyan-300">AI 解读</button>
        </div>
      </div>
      <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-cyan-300">
        <span data-hint-type className={`rounded px-1 ${categoryStyles[item.direction].label}`}>{({ buy: '买入关注', sell: '卖出关注', risk: '风险', wait: '等待确认', blocked: '待校验' })[item.direction]}</span>
        {labels.map(label => <span key={label} className="rounded border border-border-primary px-1">{label}</span>)}
        {item.resonance && <span className="text-success">确认共振</span>}
      </div>
      <p data-hint-prices className="mt-2 text-[11px] text-text-secondary">{[
        signal?.currentPrice !== undefined ? `现价 ${price(signal.currentPrice)}` : '',
        signal?.triggerPrice !== undefined ? `${signal.family === 'chan' ? '拐点价' : '信号价'} ${price(signal.triggerPrice)}` : '',
        signal?.stopPrice !== undefined ? `失效 ${price(signal.stopPrice)}` : '',
      ].filter(Boolean).join(' · ')}</p>
      <p className={`mt-1 text-[11px] ${item.direction === 'risk' || item.conflict ? 'text-warning' : 'text-text-secondary'}`}>{item.conflict ? item.summary : shortReason}</p>
      <details className="mt-2 text-[10px] text-text-tertiary" open={expanded[expandKey(`evidence-${item.symbol}`)] ?? false}
        onToggle={event => setExpanded(expandKey(`evidence-${item.symbol}`), event.currentTarget.open)}>
        <summary className="cursor-pointer">更多依据</summary>
        {signals.map((s, index) => <div key={index} className="mt-2">
          <strong>{s.source} · {s.label}{s.period ? ` · ${s.period}` : ''}</strong>
          {s.confirmedDate && <p>{s.family === 'chan' ? '首次可见计算日' : '确认日'} {s.confirmedDate}</p>}
          {s.reason && <p>{s.reason}</p>}
          <p>{[s.triggerPrice !== undefined ? `信号价 ${price(s.triggerPrice)}` : '', s.stopPrice !== undefined ? `失效线 ${price(s.stopPrice)}` : '', s.zoneLow !== undefined && s.zoneHigh !== undefined ? `回调区 ${price(s.zoneLow)}–${price(s.zoneHigh)}` : ''].filter(Boolean).join(' · ')}</p>
        </div>)}
        <p className="mt-2">下一步：{nextSteps[item.direction]}</p>
      </details>

    </article>;
  }
  return <section data-daily-hints className="min-w-0 break-words border-b border-border-primary bg-bg-secondary/40 px-3 py-0 text-xs" aria-label="当日提示">
    <div data-sidebar-heading className="flex flex-wrap items-center gap-2 border-b border-border-primary text-text-secondary">
      <strong className="text-cyan-300">当日提示</strong><span>{date} · {poolName}</span>
    </div>
    {assistantLink?.date === date && <div data-answer-return className="sticky top-0 z-10 flex items-center gap-2 border-b border-brand-blue/30 bg-bg-secondary py-2">
      <button type="button" onClick={() => { setSidebarUi({ hintsFilter: assistantLink.previousFilter, assistantLink: null }); setReportPanelUi({ view: 'chat' }); setReportOpen(true); }} className="rounded border border-brand-blue/40 px-2 py-1 text-brand-blue-light">← 返回问答</button><span className="text-text-tertiary">来自 AI 问答{assistantLink.symbols ? ` · ${visible.length} 只` : ''}</span>
    </div>}
    <div aria-label="提示分类" className="my-2 flex flex-wrap gap-1.5">
      {(['all', 'buy', 'sell', 'risk', 'wait', 'blocked'] as const).map((key, index) =>
        <button key={key} type="button" aria-pressed={filter === key} data-hint-filter={key}
          onClick={() => setSidebarUi({ hintsFilter: key, assistantLink: assistantLink ? { ...assistantLink, symbols: undefined, symbol: undefined } : null })}
          className={`rounded border px-2 py-1 font-medium ${filter === key
            ? 'border-cyan-300 bg-cyan-400/20 text-cyan-200 ring-1 ring-cyan-300'
            : 'border-border-primary text-text-secondary hover:border-cyan-400'}`}>
          {filter === key ? '✓ ' : ''}{['全部', '买入关注', '卖出关注', '风险', '等待确认', '待校验'][index]} {key === 'all' ? hints.counts.total : hints.counts[key]}
        </button>)}
    </div>
    <div data-hint-list className="mb-3 grid grid-cols-1 gap-2">{visible.map(renderCard)}</div>
    {(filter === 'all' ? hints.counts.total === 0 : hints.counts[filter] === 0) &&
      <p className="py-2 text-text-tertiary">当前分类暂无提示。数据未就绪时不代表没有机会。</p>}
    {hints.truncated && <p className="text-warning">仅展示前 300 只，请缩小范围。</p>}
    {availability && <p className="mt-1 text-[10px] text-warning">{availability} · 数量仅含已读取来源</p>}
    <details open={expanded[expandKey('history')] ?? false} onToggle={(event) => setExpanded(expandKey('history'), event.currentTarget.open)} aria-label="历史提醒与处理" className="mt-1 text-text-tertiary"><summary className="cursor-pointer">历史提醒与处理 · 已读 / 延后</summary>{legacyActions}</details>
  </section>;
}
