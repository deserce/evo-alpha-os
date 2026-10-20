'use client';

import { useEffect, useRef } from 'react';
import type { DailyHintsContext } from './DailyHintsPanel';
import { useSidebarScroll } from './useSidebarScroll';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, Bot, ChevronRight, FileText, Loader2, X } from 'lucide-react';
import { advanceAutoGeneration, shouldPollReport, parseReportJob, summarizeMissing, formatArchiveTime, type ReportJob } from '@/lib/mrgcReportJobState';
import { resolveAssistantQuestion, selectAssistantHints } from '@/lib/mrgcAssistant';
import { useGlobalStore, type ReportPanelUi } from '@/lib/store';
import { selectReportStocks, reportLinkedStocks, type ReportFilter, type ReportGroup, type ReportOpportunity } from '@/lib/mrgcReportSelection';

type ReportEvent = { event_type: string; reason: string; price?: number | null };
export type ReportStock = {
  group_ids: string[]; group_names: string[]; overlap_count: number; conflict?: boolean;
  opportunities: (ReportOpportunity & { label: string; confirmed_date: string; source: string; evidence: string[] })[];
  chan_signals?: { label: string; direction: string; pivot_date: string; confirmation_date: string; price: number }[];
  symbol: string; name: string; sector_name: string; price: number | null;
  pct_chg: number | null; rps20: number | null; structure_score: number | null;
  last_hit_date: string; hit_count: number; status: string; data_status: string;
  events: ReportEvent[]; entry_price: number | null; stop_price: number | null; analysis: string;
};
type ReportSector = {
  name: string; count: number; avg_pct_chg: number | null; avg_rps20: number | null;
  avg_structure_score: number | null; buy_confirm: number; exit: number;
};
type Report = {
  groups: { id: ReportGroup; name: string; pool_id: string; status: string; membership_as_of: string; count: number; counts: Record<string, number>; opportunity_counts: Record<string, number>; sectors: ReportSector[]; missing: string[] }[];
  partial_usable?: boolean; opportunity_counts: Record<string, number>;
  date: string; status: string; scope: string; counts: Record<string, number>;
  summary: string[]; sectors: ReportSector[]; stocks: ReportStock[]; missing: string[]; warnings?: string[];
  source: { window_start: string; window_end: string; window_days: number; version: string; mode: string; rules?: unknown; report_version?: string };
  ai?: { status: string; model?: string; provider?: string; summary?: string[];
    sector_notes?: Record<string, string>; stock_notes?: Record<string, string>; reason?: string };
};
type ReportResponse = { report: Report | null; email_html: string; job: ReportJob; archive: { valid: boolean; generated_at: string; complete: boolean; archive_id: string } | null };

export async function loadReport(date: string, generate = false, regenerate = false): Promise<ReportResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 35_000);
  try {
    const url = generate ? '/api/v1/watch/mrgc-report/generate' : `/api/v1/watch/mrgc-report?trade_date=${encodeURIComponent(date)}`;
    const response = await fetch(url, {
      method: generate ? 'POST' : 'GET', signal: controller.signal,
      ...(generate ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ trade_date: date, regenerate }) } : {}),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({})) as { detail?: unknown };
      throw new Error(typeof error.detail === 'string' ? error.detail : `日报请求失败 (${response.status})`);
    }
    const envelope = await response.json() as { data: ReportResponse };
    if (!envelope.data || (envelope.data.report !== null && (!Array.isArray(envelope.data.report?.stocks) || envelope.data.report.date !== date))) throw new Error('日报响应不完整');
    envelope.data.job = parseReportJob(envelope.data.job, date);
    return envelope.data;
  } finally { clearTimeout(timeout); }
}

const labels: Record<string, string> = {
  BUY_CONFIRM: 'C 新增买入确认', EXIT: 'C 模拟卖出', INVALIDATED: '结构失效',
  RISK_WARNING: '风险预警', HOLDING: '模拟持有', WATCHING: '等待结构', BLOCKED: '数据待校验',
};
const groupLabels: Record<ReportGroup, string> = {
  all: '三组总览', mrgc: 'MRGC 近20日', company: '公司', watchlist: '自选股', overlap: '多组交集',
};
const number = (value: number | null | undefined, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}${suffix}` : '—';

export default function MrgcDailyReportPanel({ date, currentSymbol, onSelectSymbol, allowGroupFilter = true, unifiedHints, expectUnifiedHints = false, onReturnHints }: {
  onReturnHints?: () => void;
  date: string; currentSymbol?: string; onSelectSymbol?: (symbol: string) => void; allowGroupFilter?: boolean; unifiedHints?: DailyHintsContext; expectUnifiedHints?: boolean;
}) {
  const { reportOpen, setReportOpen, selectedStock: globalSelectedStock, setGlobalDate, setReportContext, setReportFocus } = useGlobalStore();
  const selectedStock = currentSymbol ?? globalSelectedStock;
  const hints = unifiedHints?.date === date ? unifiedHints : undefined;
  const currentHint = hints?.items.find((item) => item.symbol === selectedStock);
  const { reportPanelUi, setReportPanelUi, syncReportPanelDate } = useGlobalStore();
  const { view, group, filter, sector, question, answer } = reportPanelUi;
  const setView = (view: ReportPanelUi['view']) => setReportPanelUi({ view });
  const setGroup = (group: ReportGroup) => setReportPanelUi({ group });
  const setFilter = (filter: ReportFilter) => setReportPanelUi({ filter });
  const setSector = (sector: string) => setReportPanelUi({ sector });
  const setQuestion = (question: string) => setReportPanelUi({ question });
  const setAnswer = (answer: ReportPanelUi['answer']) => setReportPanelUi({ answer });
  useEffect(() => { syncReportPanelDate(date); }, [date, syncReportPanelDate]);
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['mrgcDailyReport', date], queryFn: () => loadReport(date),
    enabled: reportOpen && Boolean(date), staleTime: 60_000, gcTime: 10 * 60_000,
    retry: false, refetchOnWindowFocus: false,
    refetchInterval: (query) => shouldPollReport(reportOpen, query.state.data?.job.status) ? 3_000 : false,
    refetchIntervalInBackground: false });
  const generate = useMutation({ mutationFn: ({ date: target, regenerate = false }: { date: string; regenerate?: boolean }) => loadReport(target, true, regenerate),
    onSuccess: (data) => client.setQueryData(['mrgcDailyReport', data.job.date], data) });
  const { mutate: startReportGeneration, isPending: generationPending } = generate;
  const generationError = generate.variables?.date === date && generate.isError;
  const autoGeneration = useRef({ date: '', attempted: false });
  const job = query.data?.job;
  const generating = shouldPollReport(true, job?.status);
  useEffect(() => {
    const next = advanceAutoGeneration(autoGeneration.current, date, job?.status, reportOpen, generationPending);
    autoGeneration.current = next.state;
    if (next.start) startReportGeneration({ date });
  }, [date, job?.status, reportOpen, generationPending, startReportGeneration]);
  const missingGroups = summarizeMissing(query.data?.report?.missing ?? []);
  const report = query.data?.report;
  const currentReportStock = report?.stocks.find(stock => stock.symbol === selectedStock);
  const usable = report?.status === 'READY' || Boolean(report?.partial_usable);
  const aiScroll = useSidebarScroll(`ai:${date}:${view}:${group}:${filter}:${sector}:${hints && !answer ? selectedStock : ""}`, Boolean(report) && reportOpen && (!expectUnifiedHints || Boolean(hints)));
  const aiReady = report?.ai?.status === 'READY';
  const scoped = report ? selectReportStocks(report.stocks, 'all', '', group) : [];
  const visible = selectReportStocks(scoped, filter, sector);
  function scopeSectors(kind: ReportGroup) {
    if (!report) return [];
    if (kind === 'all') return report.sectors;
    if (kind !== 'overlap') return report.groups?.find((item) => item.id === kind)?.sectors || [];
    const stocks = selectReportStocks(report.stocks, 'all', '', kind);
    return report.sectors.map((item) => {
      const members = stocks.filter((stock) => stock.sector_name === item.name);
      const average = (field: 'rps20' | 'pct_chg' | 'structure_score') => {
        const values = members.map((stock) => stock[field]).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
        return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
      };
      return { ...item, count: members.length, avg_rps20: average('rps20'), avg_pct_chg: average('pct_chg'), avg_structure_score: average('structure_score'),
        buy_confirm: selectReportStocks(members, 'buy').length };
    }).filter((item) => item.count > 0);
  }
  function linkStocks(stocks: ReportStock[], label: string) {
    if (!report || !allowGroupFilter) return;
    const mapped = reportLinkedStocks(stocks);
    setGlobalDate(report.date);
    setReportContext({ date: report.date, stocks: mapped, label });
    setReportFocus(mapped.length ? { date: report.date, symbol: mapped[0].symbol } : null);
  }
  function ask(input: string) {
    if (!report || !usable) return;
    const candidates = [...report.stocks, ...(hints?.items.filter(item => !report.stocks.some(stock => stock.symbol === item.symbol)) || [])];
    const result = resolveAssistantQuestion(input, candidates, selectedStock);
    if (hints && result.intent === 'current') { setAnswer(null); setQuestion(''); setView('chat'); return; }
    const scope = result.group === 'all' && !/全部|所有|三组|总览/.test(input) ? group : result.group;
    const matches = result.intent === 'current' || result.intent === 'stocks'
      ? report.stocks.filter((stock) => result.symbols.includes(stock.symbol))
      : selectReportStocks(report.stocks, result.filter, '', scope);
    const hintMatches = hints && !['sector', 'unsupported'].includes(result.intent)
      ? selectAssistantHints(hints.items, result).filter(item => result.group === 'all' ||
        selectReportStocks(report.stocks, 'all', '', result.group).some(stock => stock.symbol === item.symbol)) : undefined;
    setAnswer({ hintSymbols: hintMatches?.map(item => item.symbol), hintFilter: result.filter === 'exit' ? 'sell' : result.filter, question: input.slice(0, 500), title: result.title, group: scope, total: hintMatches?.length ?? matches.length,
      stocks: matches.slice(0, 20), allStocks: matches, sector: result.intent === 'sector', unsupported: result.intent === 'unsupported' });
    setQuestion(''); setView('chat');
  }
  function openAnswerHints(filter: 'all' | 'buy' | 'sell' | 'risk' | 'wait' | 'blocked', symbol?: string) {
    if (!hints || !onReturnHints) return;
    const state = useGlobalStore.getState();
    state.setStockSidebarUi({ assistantLink: { date, symbol, symbols: symbol ? undefined : answer?.hintSymbols?.slice(0, 300), filter, previousFilter: state.stockSidebarUi.hintsFilter }, hintsFilter: filter });
    if (symbol) onSelectSymbol?.(symbol);
    onReturnHints();
  }
  function focusStock(stock: ReportStock) {
    if (!report) return;
    setGlobalDate(report.date);
    if (onSelectSymbol) onSelectSymbol(stock.symbol);
    else setReportFocus({ date: report.date, symbol: stock.symbol });
  }
  function stockCard(stock: ReportStock) {
    return <article key={stock.symbol} className={`rounded-lg border p-3 ${stock.symbol === selectedStock ? 'border-brand-blue bg-brand-blue/10' : 'border-border-secondary'}`}>
      <button type="button" onClick={() => focusStock(stock)} className="flex w-full items-center gap-2 text-left text-xs font-black text-brand-blue-light">
        <span>{stock.name}</span><span className="font-mono font-normal text-text-tertiary">{stock.symbol}</span><span className="ml-auto font-mono">{number(stock.price)}</span><ChevronRight size={12} />
      </button>
      {hints && !hints.items.some(item => item.symbol === stock.symbol) && <p className="mt-1 text-[10px] text-text-tertiary">仅日报存档 · 无对应当日提示</p>}
      {hints?.items.some(item => item.symbol === stock.symbol) && onReturnHints && <button type="button" onClick={() => openAnswerHints(hints.items.find(item => item.symbol === stock.symbol)!.direction, stock.symbol)} className="mt-2 text-xs text-brand-blue-light">查看对应提示 →</button>}
      <p className="mt-2 text-[10px] text-warning">{stock.group_names?.join(' ＋ ')}{stock.overlap_count > 1 && ` · ${stock.overlap_count}组共选`}</p>
      {stock.conflict && <p className="mt-1 text-xs text-warning">C 与缠论方向分歧，先核对风险依据。</p>}
      <div className="mt-2 space-y-1 text-[10px] text-brand-blue-light">{stock.opportunities?.map((item, i) => <div key={i}><strong>{item.label}</strong> · {item.confirmed_date}<p className="leading-5 text-text-secondary">{item.evidence.join('；')}</p></div>)}</div>
      {!stock.opportunities?.length && <p className="mt-1 text-[10px] text-text-secondary">{stock.events.length ? stock.events.map((event) => labels[event.event_type] || event.event_type).join(' / ') : labels[stock.status] || stock.status} · 无今日新增买卖机会</p>}
      <p className="mt-2 text-xs leading-5 text-text-secondary">{stock.analysis}</p>
      {stock.chan_signals?.length ? <details className="mt-2 text-[10px] text-text-tertiary"><summary className="cursor-pointer">缠论候选依据 · 回绘位置与首次可见日期</summary>{stock.chan_signals.map((signal, i) => <p key={i} className="mt-1 leading-5">{signal.label} · 回绘位置 {signal.pivot_date} · 首次可见 {signal.confirmation_date} · 参考价 {number(signal.price)}</p>)}</details> : null}
      {aiReady && report?.ai?.stock_notes?.[stock.symbol] && <p className="mt-2 text-xs leading-5">AI 解读：{report.ai.stock_notes[stock.symbol]}</p>}
      <p className="mt-2 text-[10px] text-text-tertiary">RPS20 {number(stock.rps20)} · 结构 {number(stock.structure_score)} · C 失效线 {number(stock.stop_price)}<br />{stock.last_hit_date ? `MRGC 最近命中 ${stock.last_hit_date} · 20日 ${stock.hit_count} 次` : '近20日 MRGC 未命中'}</p>
    </article>;
  }
  function sectorCards(kind: ReportGroup) {
    return [...scopeSectors(kind)].sort((a, b) => (b.avg_rps20 ?? -1) - (a.avg_rps20 ?? -1)).slice(0, 8).map((item) => <div key={item.name} className="rounded-lg border border-border-secondary p-3 text-xs leading-6">
      <p className="font-bold">{item.name} · {item.count} 只</p><p>平均 RPS20 {number(item.avg_rps20)} · 均涨跌 {number(item.avg_pct_chg, '%')}</p>
      {aiReady && report?.ai?.sector_notes?.[item.name] && <p>{report.ai.sector_notes[item.name]}</p>}
      {allowGroupFilter && <button type="button" onClick={() => linkStocks(selectReportStocks(report?.stocks || [], 'all', item.name, kind), `${groupLabels[kind]} · ${item.name}`)} className="text-brand-blue-light">筛选这个行业</button>}
    </div>);
  }
  if (!reportOpen) return null;
  return <aside data-daily-assistant aria-label="统一机会 AI日报分析" className="relative flex h-full min-h-0 w-full flex-col overflow-hidden border-l border-border-primary bg-bg-secondary/40">
    <div data-sidebar-heading className="flex shrink-0 items-center gap-2 border-b border-border-primary">
      <Bot className="h-4 w-4 text-brand-blue-light" /><div className="min-w-0 flex-1"><h2 className="text-xs font-bold">{hints ? '当日提示 · 解释与日报' : '三组 · C＋缠论日报'}</h2><p className="text-[10px] leading-4 text-text-tertiary">{date || '请选择交易日'} · 邮件尚未启用投递</p></div>
      <button type="button" aria-label="关闭日报" onClick={() => setReportOpen(false)} className="rounded p-1 hover:bg-bg-tertiary"><X size={18} /></button>
    </div>
    {onReturnHints && <div data-hints-return-bar className="flex shrink-0 items-center gap-2 border-b border-border-secondary px-3 py-2 text-xs">
      <button type="button" onClick={onReturnHints} className="flex shrink-0 items-center gap-1 rounded border border-brand-blue/40 px-2 py-1 text-brand-blue-light"><ArrowLeft size={12} />返回提示</button>
      <span className="min-w-0 truncate text-text-secondary">{currentHint ? `${currentHint.name} ${currentHint.symbol}` : selectedStock}</span>
    </div>}
    <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border-secondary px-3 py-2 text-xs">
      {([['chat', '问助手'], ['report', onReturnHints ? '日报背景' : '完整日报'], ['email', '邮件预览']] as const).map(([key, label]) => <button key={key} type="button" aria-pressed={view === key} onClick={() => setView(key)} className={view === key ? 'text-brand-blue-light' : 'text-text-secondary'}>{label}</button>)}
      <button type="button" disabled={!date || query.isLoading || generate.isPending || generating}
        onClick={() => generate.mutate({ date, regenerate: job?.status === 'STALE' || job?.status === 'COMPLETED' })}
        className="ml-auto flex items-center gap-1 rounded border border-brand-blue/40 px-2 py-1 text-brand-blue-light disabled:opacity-40">
        {(generate.isPending || generating) && <Loader2 size={12} className="animate-spin" />}
        {generating ? job?.status === 'BUSY' ? '等待其他日报' : '生成中…' : job?.status === 'STALE' ? '更新日报' : job?.status === 'COMPLETED' ? '重新生成' : job?.status === 'PARTIAL' || job?.status === 'FAILED' ? '继续生成' : '生成日报'}
      </button>
    </div>
    {report && view !== 'email' && (!hints || view !== 'chat') && <div className="flex shrink-0 flex-wrap gap-1 border-b border-border-secondary p-2" aria-label="日报分组范围">{(Object.keys(groupLabels) as ReportGroup[]).map((key) => <button key={key} type="button" aria-pressed={group === key} onClick={() => { setGroup(key); setSector(''); setAnswer(null); }} className={`rounded border px-2 py-1 text-[10px] ${group === key ? 'border-brand-blue text-brand-blue-light' : 'border-border-secondary text-text-secondary'}`}>{groupLabels[key]} · {selectReportStocks(report.stocks, 'all', '', key).length}</button>)}</div>}
    <div data-sidebar-ai-scroll ref={aiScroll.ref} onScroll={aiScroll.onScroll} className={`min-h-0 flex-1 overflow-y-auto overscroll-contain custom-scrollbar ${view === 'email' ? 'flex flex-col' : ''}`}>
    {hints && view === 'chat' && !answer && <section data-hint-explanation aria-label="当日提示解读" className="shrink-0 border-b border-brand-blue/30 bg-brand-blue/5 px-3 py-3 text-xs leading-6">
      {currentReportStock ? stockCard(currentReportStock) : currentHint ? <article className="space-y-2 rounded-lg border border-brand-blue bg-brand-blue/10 p-3">
        <h3 className="font-bold">{currentHint.name} <span className="font-mono">{currentHint.symbol}</span></h3>
        <p>{currentHint.summary}</p>
        {currentHint.signals.slice(0, 12).map((signal, index) => <div key={index}>
          <strong className="text-brand-blue-light">{signal.label}</strong> · {signal.confirmedDate || date}
          <p className="text-text-secondary">{signal.reason || '暂无详细依据'}</p>
          <p className="text-text-tertiary">信号价 {number(signal.triggerPrice)} · 失效线 {number(signal.stopPrice)}</p>
        </div>)}
        <p className="text-text-tertiary">当前股票未收录在日报存档中；以上为当日提示依据，暂缺 RPS / 结构等存档指标。</p>
      </article> : <p className="text-text-tertiary">当前股票暂无详细分析，可返回提示选择机会。</p>}
    </section>}
    {job && <p role="status" className={`shrink-0 border-b border-border-secondary px-3 py-2 text-[10px] leading-5 ${job.status === 'STALE' || job.status === 'FAILED' || job.status === 'PARTIAL' ? 'text-warning' : 'text-text-secondary'}`}>
      {job.reason}{job.status === 'RUNNING' && ` · ${job.stage} · ${job.completed}/${job.total}`}
      {query.data?.archive && ` · ${query.data.archive.complete ? '完整存档' : '部分存档'} · ${formatArchiveTime(query.data.archive.generated_at)}`}
      {job.status === 'STALE' && '；当前展示旧版，更新成功后切换新版本。'}
    </p>}
    {report?.ai?.status === 'DEFERRED' && <p role="status" className="shrink-0 border-b border-border-secondary px-3 py-2 text-[10px] leading-5 text-text-secondary">策略日报已存档 · 暂不生成 AI 解读</p>}
    {report?.ai && report.ai.status !== 'DEFERRED' && <p role="status" className="shrink-0 border-b border-border-secondary px-3 py-2 text-[10px] leading-5 text-text-secondary">{aiReady ? `${report.ai.reason ? '解读可用' : '解读已保存'} · 在线模型 · ${report.ai.model || ''}${report.ai.reason ? ` · ${report.ai.reason}` : ''}` : report.ai.reason || `规则分析已就绪 · 在线模型暂未生成解读`}</p>}
    {(query.isError || generationError) && <div role="alert" className="m-3 rounded border border-danger/30 p-3 text-xs text-danger">{(generationError ? generate.error : query.error)?.message || '日报请求失败。'}<button type="button" className="ml-2 underline" onClick={() => query.refetch()}>重试读取</button></div>}
    {query.isLoading && <p className="flex items-center gap-2 p-4 text-xs text-text-secondary"><Loader2 size={14} className="animate-spin" />正在读取所选日期日报存档…</p>}
    {!report && !query.isLoading && <p className="p-4 text-xs leading-6 text-text-secondary">{job?.status === 'RUNNING' ? '所选日期首次生成中，完成后显示完整日报。' : job?.reason || '所选日期尚无日报存档。'}</p>}
    {report && view === 'email' && <iframe title="统一日报邮件预览，不发送邮件" sandbox="" srcDoc={query.data?.email_html} className="min-h-0 flex-1 w-full border-0 bg-white" />}
    {report && view !== 'email' && <>
      <div className="space-y-3 px-3 py-3">
        {!hints && <div className="rounded-lg border border-border-secondary p-3 text-xs leading-6">
          <p className="font-bold">{report.date} · 总览去重 {report.counts.total} 只 · {groupLabels[group]} {scoped.length} 只</p>
          <p>覆盖 {report.counts.total} 只 · 可信 {Math.max(0, (report.counts.total || 0) - (report.counts.blocked || 0))} 只 · 待补齐 {report.counts.blocked || 0} 只</p>
          <p>买入关注 {selectReportStocks(scoped, 'buy').length} · 卖出关注 {selectReportStocks(scoped, 'exit').length} · 风险 / 失效 {selectReportStocks(scoped, 'risk').length}</p>
          <p className="text-[10px] text-text-tertiary">当前个股：{report.stocks.find((stock) => stock.symbol === selectedStock)?.name || selectedStock || '未选择'} · 切换股票后主动提问</p>
        </div>}
        {report.status !== 'READY' && <div role="status" className="rounded border border-warning/30 p-2 text-[10px] leading-5 text-warning"><AlertTriangle size={12} className="mr-1 inline" />部分数据待校验，仅展示已通过校验的机会，不能据此判断缺失组无机会。{missingGroups.map(({ reason, items }) => <details key={reason} className="mt-1"><summary className="cursor-pointer">{reason} · {items.length} 项</summary>{items.map((item) => <p key={item}>{item}</p>)}</details>)}</div>}
        <details className="text-[10px] leading-5 text-text-tertiary"><summary className="cursor-pointer">确认逻辑与数据口径</summary><p>日报存档的 C＋缠表示 C 确认搭配近期缠论候选；当日提示仅将同日同向已确认信号标为共振，候选不算确认。C 止损、风险保留。</p><p>缠论复用知乎公式；含 ZIG 未来函数，按当日截断行情计算首次可见日期，不把回绘位置当成确认日，也不代表已验证的经典三买。</p><p>MRGC 按所选日期回看20交易日；公司 / 自选保存生成时成员快照，该快照不代表当日历史成员。多组共选表示来源交集，不能单独证明收益更高。</p></details>
        {aiReady && (!hints || view !== 'chat') && report.ai?.summary?.length ? <section aria-label="AI 日报解读" className="rounded-lg border border-brand-blue/30 bg-brand-blue/5 p-3 text-xs leading-6"><strong>AI 日报解读</strong>{report.ai.summary.map((line) => <p key={line}>{line}</p>)}</section> : null}
        {view === 'chat' ? <>
          <div className="grid grid-cols-2 gap-2">{['今天有哪些机会？', '今天有哪些卖点？', '解释当前股票', '哪些股票需要警惕？', '哪些行业更强？', '多组交集机会'].map((prompt) => <button key={prompt} type="button" disabled={!usable} onClick={() => ask(prompt)} className="rounded border border-brand-blue/30 px-2 py-2 text-xs text-brand-blue-light disabled:opacity-40">{prompt}</button>)}</div>
          <p className="text-[10px] leading-5 text-text-tertiary">机会、卖点和风险与当日提示同步；行业背景读取日报存档。</p>
          {!answer && !hints && <div className="text-xs leading-6 text-text-secondary">{report.summary.map((line) => <p key={line}>{line}</p>)}</div>}
          {answer && <details className="space-y-3" key={answer.question} open><summary className="cursor-pointer text-xs text-text-secondary">最新问答 · {answer.title}</summary><section className="space-y-3"><p className="rounded-lg bg-brand-blue/10 px-3 py-2 text-xs">{answer.question}</p><h3 className="text-xs font-bold">{answer.title} · {answer.hintSymbols ? 'Watchlist 当日提示' : groupLabels[answer.group]} · {answer.total} 只</h3>
            {answer.unsupported ? <p className="text-xs leading-6 text-text-secondary">使用快捷问题、公司 / 自选 / MRGC 分组或股票名称查询。{answer.title}</p> : answer.sector ? <div className="space-y-2">{sectorCards(answer.group)}</div> : answer.hintSymbols && hints ? <div data-answer-hints className="space-y-2">
              <button type="button" onClick={() => openAnswerHints(answer.hintFilter || 'all')} className="rounded border border-brand-blue/40 px-2 py-1 text-xs text-brand-blue-light">查看对应提示 · {answer.total} 只</button>
              {answer.hintSymbols.slice(0, 20).map(symbol => {
                const item = hints.items.find(item => item.symbol === symbol);
                if (!item) return null;
                return <button key={symbol} type="button" data-answer-hint-symbol={symbol} onClick={() => openAnswerHints(item.direction, symbol)} className="block w-full rounded border border-border-secondary p-2 text-left text-xs hover:border-brand-blue">
                  <strong>{item.name} {item.symbol}</strong><span className="float-right text-brand-blue-light">查看提示 →</span><p className="mt-1 text-text-secondary">{item.summary}</p>
                </button>;
              })}
              {!answer.total && <p className="text-xs text-text-tertiary">所选日期暂无对应提示，数据未就绪不代表没有机会。</p>}
              {answer.total > 20 && <p className="text-[10px] text-text-tertiary">展示前20只，完整列表见对应提示。</p>}
            </div> : <>
              {allowGroupFilter && answer.total > 0 && <button type="button" onClick={() => linkStocks(answer.allStocks, `${groupLabels[answer.group]} · ${answer.title}`)} className="text-xs text-brand-blue-light">筛选这组股票 · 共 {answer.total} 只</button>}
              {answer.stocks.map(stockCard)}{answer.total === 0 && <p className="text-xs text-text-secondary">没有通过校验的对应机会，当前个股也可能不在三组并集中。</p>}
              {answer.total > 20 && <p className="text-[10px] text-text-tertiary">本次展示前20只，全部 {answer.total} 只见完整日报。</p>}
            </>}
          </section></details>}
        </> : hints ? <section data-report-background className="space-y-3">
          <p className="text-xs text-text-secondary">机会与买卖信号已汇入当日提示，这里保留行业和日报背景。</p>
          <details className="text-[10px] leading-5 text-text-secondary"><summary className="cursor-pointer">分组与数据范围</summary>{report.groups?.map(item => <p key={item.id}>{item.name} · {item.id === 'mrgc' ? `回看 ${report.source.window_start} ～ ${report.source.window_end}` : '生成时成员口径，不代表历史成员'}</p>)}</details>
          <section className="space-y-2"><h3 className="text-xs font-bold">行业分析 · {groupLabels[group]}</h3>{sectorCards(group)}</section>
          {report.warnings?.length ? <details className="text-[10px] leading-5 text-warning"><summary className="cursor-pointer">背景指标 / 缠论数据提示</summary>{report.warnings.map(line => <p key={line}>{line}</p>)}</details> : null}
        </section> : <>
          <section className="text-xs leading-6 text-text-secondary">{report.summary.map((line) => <p key={line}>{line}</p>)}</section>
          <div className="space-y-2">{report.groups?.map((item) => <button key={item.id} type="button" onClick={() => { setGroup(item.id); setSector(''); }} className="block w-full rounded border border-border-secondary p-2 text-left text-xs leading-6"><strong>{item.name} · {item.count} 只 · {item.status === 'READY' ? '可信' : '待校验'}</strong><p>买入关注 {item.opportunity_counts?.buy || 0} · 卖出关注 {item.opportunity_counts?.sell || 0} · 共振买入 {item.opportunity_counts?.resonance_buy || 0}</p><p className="text-[10px] text-text-tertiary">{item.id === 'mrgc' ? `回看 ${report.source.window_start} ～ ${report.source.window_end}` : item.membership_as_of === 'generation_snapshot' ? '成员口径：生成时成员快照，非当日历史成员' : '成员口径：当前分组，非历史成员快照'}</p></button>)}</div>
          <div className="grid grid-cols-3 gap-2">{([['all', '全部成员'], ['buy', '买入关注'], ['exit', '卖出关注'], ['risk', '风险 / 失效'], ['holding', '既有模拟持有']] as const).map(([key, label]) => <button key={key} type="button" onClick={() => { setFilter(key); setSector(''); }} className={`rounded border p-2 text-left ${filter === key ? 'border-brand-blue text-brand-blue-light' : 'border-border-secondary text-text-secondary'}`}><span className="text-[10px]">{label}</span><p className="font-mono text-lg">{selectReportStocks(scoped, key).length}</p></button>)}</div>
          <details className="space-y-2"><summary className="cursor-pointer text-xs font-bold">行业分析 · {groupLabels[group]}</summary>{sectorCards(group)}</details>
          <div className="flex items-center justify-between text-xs"><strong>{groupLabels[group]} · {visible.length} 只</strong>{allowGroupFilter && <button type="button" onClick={() => linkStocks(visible, `${groupLabels[group]} · 日报筛选`)} className="text-brand-blue-light">筛选这组股票</button>}</div>
          <div className="space-y-2">{visible.map(stockCard)}</div>{!visible.length && <p className="text-xs text-text-tertiary">当前范围没有对应股票。</p>}
          {report.warnings?.length ? <details className="text-[10px] leading-5 text-warning"><summary>背景指标 / 缠论数据提示</summary>{report.warnings.map((line) => <p key={line}>{line}</p>)}</details> : null}
        </>}
        <p className="border-t border-border-secondary pt-2 text-[10px] leading-5 text-text-tertiary"><FileText size={12} className="mr-1 inline" />共振是策略证据叠加；C 为模拟策略，缠论为回绘公式候选，不代表账户下单。</p>
      </div>
    </>}
    </div>
      {report && view === 'chat' && <form onSubmit={(event) => { event.preventDefault(); if (question.trim()) ask(question); }} className="shrink-0 border-t border-border-primary p-3"><div className="flex gap-2"><input aria-label="向助手提问" maxLength={500} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="公司机会、自选卖点、交集风险…" className="min-w-0 flex-1 rounded border border-border-primary bg-bg-primary px-3 py-2 text-xs outline-none" /><button type="submit" disabled={!question.trim() || !usable} className="rounded bg-brand-blue/20 px-3 text-xs text-brand-blue-light disabled:opacity-40">提问</button></div></form>}
  </aside>;
}
