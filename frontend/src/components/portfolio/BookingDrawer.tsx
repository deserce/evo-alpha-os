'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import axios from 'axios';
import { createPortal } from 'react-dom';
import { X, Upload, Loader2, CheckCircle2 } from 'lucide-react';
import { bookingRequestId, rowIssues, validateImages, type BookingRow } from '@/lib/portfolio-booking';

type Tab = 'screenshot' | 'manual' | 'cash';
interface Props { open: boolean; onClose: () => void; initialTab?: Tab; accountId?: number; onBooked: () => void; manualContent: ReactNode; cashContent: ReactNode }
interface Preview { draft_id: string | null; version: number; ledger_revision: number; cash_before_cents: number; cash_after_cents: number; positions_before: {symbol: string; quantity: number}[]; positions_after: {symbol: string; quantity: number}[]; rows: BookingRow[]; warnings: string[]; expires_at: number | null; needs_duplicate_confirmation: boolean }
interface Receipt { booked_count: number; cash_after_cents: number; performance_status: string; duplicate_replay: boolean }
const inputClass = 'w-full min-w-0 rounded border border-white/10 bg-[var(--bg-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:border-cyan-400';
const buttonClass = 'rounded-lg border border-white/10 px-3 py-2 text-sm disabled:opacity-40 disabled:cursor-not-allowed hover:bg-white/5';
const money = (cents: number) => (cents / 100).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
function errorText(error: unknown): string {
  if (axios.isAxiosError(error)) { const detail: unknown = error.response?.data?.detail; if (typeof detail === 'string') return detail; if (detail) return JSON.stringify(detail); return error.message; }
  return error instanceof Error ? error.message : '操作未完成，请重试';
}
export default function BookingDrawer({open, onClose, initialTab = 'screenshot', accountId, onBooked, manualContent, cashContent}: Props) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [images, setImages] = useState<{file: File; url: string}[]>([]);
  const imageRef = useRef(images); imageRef.current = images;
  const [rows, setRows] = useState<BookingRow[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sourceHashes, setSourceHashes] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'recognize' | 'preview' | 'commit' | null>(null);
  const busyRef = useRef(busy); busyRef.current = busy;
  const onCloseRef = useRef(onClose); onCloseRef.current = onClose;
  const [duplicatesConfirmed, setDuplicatesConfirmed] = useState(false);
  const [replacements, setReplacements] = useState<Set<number>>(new Set());
  const [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const requestId = useRef(bookingRequestId());
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const previousAccount = useRef(accountId);
  const fileInput = useRef<HTMLInputElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const triggerElement = useRef<HTMLElement | null>(null);
  const invalidate = () => { setPreview(null); setReceipt(null); requestId.current = bookingRequestId(); };
  useEffect(() => {
    const accountChanged = previousAccount.current !== accountId;
    previousAccount.current = accountId;
    if (open) { setTab(initialTab); if (document.activeElement instanceof HTMLElement && !closeButton.current?.closest('section')?.contains(document.activeElement)) triggerElement.current = document.activeElement; closeButton.current?.focus(); if (!accountChanged) return; }
    generation.current += 1; controller.current?.abort();
    imageRef.current.forEach(image => URL.revokeObjectURL(image.url));
    setImages([]); setRows([]); setSelected(new Set()); setSourceHashes([]); setWarnings([]); setPreview(null); setReceipt(null); setError(''); setBusy(null); setDuplicatesConfirmed(false); setReplacements(new Set()); setReplaceConfirmed(false); requestId.current = bookingRequestId();
  }, [open, initialTab, accountId]);
  useEffect(() => () => { controller.current?.abort(); imageRef.current.forEach(image => URL.revokeObjectURL(image.url)); }, []);
  useEffect(() => {
    if (!open) return;
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const original = document.body.style.overflow; document.body.style.overflow = 'hidden';
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && busyRef.current !== 'commit') onCloseRef.current();
      if (event.key !== 'Tab') return;
      const dialog = closeButton.current?.closest('section');
      const elements = Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled):not([hidden]), select:not(:disabled), a[href], [tabindex="0"]') || []).filter(element => element.getClientRects().length > 0);
      const first = elements[0]; const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
    };
    document.addEventListener('keydown', keydown);
    return () => { document.body.style.overflow = original; document.removeEventListener('keydown', keydown); if (triggerElement.current?.isConnected) triggerElement.current.focus(); else returnFocus?.focus(); };
  }, [open]);
  function addImages(files: File[]) {
    if (busy || receipt || !files.length) return;
    const issue = validateImages([...images.map(image => image.file), ...files]);
    if (issue) { setError(issue); return; }
    setImages(old => [...old, ...files.map(file => ({file, url: URL.createObjectURL(file)}))]);
    setRows([]); setSelected(new Set()); setSourceHashes([]); setWarnings([]); setDuplicatesConfirmed(false); setReplacements(new Set()); setReplaceConfirmed(false); setError(''); invalidate();
  }
  function editRow(id: string, patch: Partial<BookingRow>) {
    setRows(old => old.map(row => row.row_id === id ? {...row, ...patch, duplicate_confirmed: false} : row));
    setDuplicatesConfirmed(false); setReplaceConfirmed(false); invalidate();
  }
  async function recognize() {
    if (!accountId || !images.length) return;
    const current = ++generation.current; const abort = new AbortController(); controller.current = abort;
    setBusy('recognize'); setError(''); invalidate();
    try {
      const body = new FormData(); images.forEach(image => body.append('images', image.file)); body.append('account_id', String(accountId));
      const {data} = await axios.post<{rows: BookingRow[]; warnings: string[]; source_hashes: string[]}>('/api/v1/portfolio/imports/recognize', body, {signal: abort.signal, timeout: 120000});
      if (current !== generation.current) return;
      setRows(data.rows); setSelected(new Set(data.rows.filter(row => rowIssues(row).length === 0).map(row => row.row_id))); setSourceHashes(data.source_hashes); setWarnings(data.warnings); setDuplicatesConfirmed(false); setReplacements(new Set()); setReplaceConfirmed(false);
      if (!data.rows.length) setError('未识别出成交或资金流水，请换一张清晰截图，或使用手工记账。');
    } catch (caught) { if (current === generation.current && !axios.isCancel(caught)) setError(errorText(caught)); }
    finally { if (current === generation.current) setBusy(null); }
  }
  const chosen = rows.filter(row => selected.has(row.row_id) && rowIssues(row).length === 0);
  async function makePreview() {
    if (!accountId || !chosen.length) return;
    const current = generation.current; setBusy('preview'); setError('');
    try {
      const {data} = await axios.post<Preview>('/api/v1/portfolio/imports/preview', {account_id: accountId, rows: chosen.map(row => ({...row, duplicate_confirmed: duplicatesConfirmed})), source_hashes: sourceHashes, client_request_id: requestId.current, replace_transaction_ids: replaceConfirmed ? [...replacements] : []}, {timeout: 30000});
      if (current !== generation.current) return;
      setPreview(data); setRows(old => old.map(row => {const match = data.rows.find(item => item.row_id === row.row_id); return match ? {...row, duplicate_candidates: match.duplicate_candidates} : row;}));
    } catch (caught) { if (current === generation.current) setError(errorText(caught)); }
    finally { if (current === generation.current) setBusy(null); }
  }
  async function commit() {
    if (!preview?.draft_id || preview.needs_duplicate_confirmation) return;
    setBusy('commit'); setError('');
    try { const {data} = await axios.post<Receipt>('/api/v1/portfolio/imports/commit', {draft_id: preview.draft_id, version: preview.version, client_request_id: requestId.current}, {timeout: 60000}); setReceipt(data); setPreview(null); onBooked(); }
    catch (caught) { setError(`${errorText(caught)}。若网络中断，请使用本页重试，系统会防止重复入账。`); }
    finally { setBusy(null); }
  }
  if (!open || typeof document === 'undefined') return null;
  const duplicateIds = [...new Set(chosen.flatMap(row => row.duplicate_candidates?.flatMap(item => typeof item.transaction_id === 'number' ? [item.transaction_id] : []) || []))];
  const batchDuplicates = chosen.flatMap(row => (row.duplicate_candidates || []).filter(item => item.batch_row_index != null).map(item => ({rowId: row.row_id, index: item.batch_row_index!})));
  const positions = preview ? [...new Set([...preview.positions_before, ...preview.positions_after].map(position => position.symbol))] : [];
  return createPortal(<div className="fixed inset-0 z-[80] flex justify-end bg-black/60" onMouseDown={event => {if (event.target === event.currentTarget && busy !== 'commit') onClose();}}>
    <section role="dialog" aria-modal="true" aria-labelledby="booking-title" className="flex h-full w-full max-w-5xl flex-col border-l border-white/10 bg-[var(--bg-secondary)] shadow-2xl" onPaste={event => {if (tab !== 'screenshot') return; const files = Array.from(event.clipboardData.files); if (files.length) {event.preventDefault(); addImages(files);}}}>
      <header className="flex items-center justify-between border-b border-white/10 px-6 py-5"><div><h2 id="booking-title" className="text-lg font-semibold">记一笔</h2><p className="mt-1 text-xs text-[var(--text-secondary)]">录入实盘成交，确认后更新账本与权益</p></div><button ref={closeButton} type="button" aria-label="关闭记账" disabled={busy === 'commit'} onClick={onClose} className={buttonClass}><X size={18}/></button></header>
      <nav className="flex gap-2 border-b border-white/10 px-6 py-3" aria-label="记账方式">{([['screenshot','截图导入'],['manual','手工记账'],['cash','资金流水']] as const).map(([key,label]) => <button type="button" key={key} disabled={!!busy} onClick={() => setTab(key)} aria-pressed={tab === key} className={`${buttonClass} ${tab === key ? 'border-cyan-400/40 bg-cyan-400/10 text-cyan-300' : 'text-[var(--text-secondary)]'}`}>{label}</button>)}</nav>
      <div className="flex-1 overflow-y-auto p-6">{tab === 'manual' ? manualContent : tab === 'cash' ? cashContent : <div className="space-y-5">
        {!accountId && <p className="text-amber-300">请先选择实盘账户。</p>}
        <div className="rounded-xl border border-dashed border-white/20 bg-[var(--bg-tertiary)] p-5"><div className="flex flex-wrap items-center justify-between gap-4"><div><p className="font-medium">粘贴或选择交割单截图</p><p className="mt-1 text-xs text-[var(--text-secondary)]">最多 8 张 · 每张 8 MB · 合计 32 MB；仅按需识别，持仓图不能替代成交记录。</p></div><button type="button" disabled={!!busy || !!receipt} onClick={() => fileInput.current?.click()} className={buttonClass}><Upload className="mr-2 inline" size={15}/>选择图片</button></div><input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange={event => {addImages(Array.from(event.target.files || [])); event.target.value = '';}}/>
        {images.length > 0 && <div className="mt-4 flex flex-wrap gap-3">{images.map((image,index) => <div key={image.url} className="relative"><a href={image.url} target="_blank" rel="noreferrer" aria-label={`查看截图 ${index + 1}`}><img src={image.url} alt={`交割截图 ${index+1}`} className="h-24 w-20 rounded border border-white/10 object-cover object-top"/></a><button type="button" disabled={!!busy || !!receipt} aria-label={`移除截图 ${index+1}`} onClick={() => {URL.revokeObjectURL(image.url); setImages(old => old.filter(item => item.url !== image.url)); setRows([]); setSelected(new Set()); setSourceHashes([]); invalidate();}} className="absolute -right-2 -top-2 rounded-full bg-slate-800 p-1"><X size={12}/></button></div>)}</div>}
        <div className="mt-4 flex items-center gap-3"><button type="button" disabled={!accountId || !images.length || !!busy || !!receipt} onClick={recognize} className={`${buttonClass} bg-cyan-400/10 text-cyan-300`}>{busy === 'recognize' ? <Loader2 size={14} className="mr-2 inline animate-spin"/> : null}识别截图</button>{busy === 'recognize' && <button type="button" className={buttonClass} onClick={() => {generation.current++; controller.current?.abort(); setBusy(null);}}>取消识别</button>}<span className="text-xs text-[var(--text-secondary)]">识别结果仅为草稿，需核对后确认。</span></div></div>
        {warnings.length > 0 && <ul className="space-y-1 rounded-lg bg-amber-400/5 p-3 text-xs text-amber-200">{warnings.map((warning,index) => <li key={index}>{warning}</li>)}</ul>}
        {rows.length > 0 && <fieldset disabled={!!busy || !!receipt} className="min-w-0"><legend className="mb-3 text-sm">逐笔核对 · 已选 {chosen.length} / {rows.length} 笔</legend><div className="overflow-x-auto rounded-xl border border-white/10"><table className="w-full min-w-[790px] text-left text-xs"><thead className="bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"><tr>{['选入','类型','日期 / 时间','代码 / 名称','价格 / 金额（元）','股数','税费（元）','交割净额（元）'].map(label => <th key={label} className="p-2 font-normal">{label}</th>)}</tr></thead><tbody>{rows.map(row => {const issues = rowIssues(row); const cash = row.business_type === 'DEPOSIT' || row.business_type === 'WITHDRAWAL'; return <tr key={row.row_id} className="border-t border-white/5 align-top"><td className="p-2"><input aria-label={`选入 ${row.name || row.symbol || row.row_id}`} type="checkbox" checked={selected.has(row.row_id) && !issues.length} disabled={issues.length > 0} onChange={event => {setSelected(old => {const next = new Set(old); if(event.target.checked) next.add(row.row_id); else next.delete(row.row_id); return next;}); invalidate();}}/></td><td className="w-24 p-2"><select aria-label="业务类型" className={inputClass} value={row.business_type || ''} onChange={event => editRow(row.row_id,{business_type:event.target.value as BookingRow['business_type']})}><option value="">待补全</option><option value="BUY">买入</option><option value="SELL">卖出</option><option value="DEPOSIT">入金</option><option value="WITHDRAWAL">出金</option></select></td><td className="w-40 space-y-1 p-2"><input type="date" aria-label="成交日期" className={inputClass} value={row.trade_date || ''} onChange={event => editRow(row.row_id,{trade_date:event.target.value})}/><input type="time" step="1" aria-label="成交时间" className={inputClass} value={row.trade_time || ''} onChange={event => editRow(row.row_id,{trade_time:event.target.value})}/></td><td className="w-28 p-2"><input aria-label="股票代码" disabled={cash} className={inputClass} value={row.symbol || ''} onChange={event => editRow(row.row_id,{symbol:event.target.value})}/><p className="mt-1 text-[var(--text-secondary)]">{row.name}</p></td><td className="w-32 p-2"><input type="number" min="0" step={cash ? '0.01' : '0.001'} aria-label={cash ? '资金金额' : '成交价格'} className={inputClass} value={cash ? (row.amount_cents === null ? '' : row.amount_cents/100) : (row.price_micros === null ? '' : row.price_micros/1000000)} onChange={event => editRow(row.row_id,cash ? {amount_cents:event.target.value === '' ? null : Math.round(Number(event.target.value)*100)} : {price_micros:event.target.value === '' ? null : Math.round(Number(event.target.value)*1000000)})}/>{[...issues,...(row.warnings || [])].map((issue,index) => <p key={index} className="mt-1 text-[10px] text-amber-300">{issue}</p>)}</td><td className="w-24 p-2"><input type="number" min="1" step="1" aria-label="成交股数" disabled={cash} className={inputClass} value={row.quantity ?? ''} onChange={event => editRow(row.row_id,{quantity:event.target.value === '' ? null : Number(event.target.value)})}/></td><td className="w-24 p-2"><input type="number" min="0" step="0.01" aria-label="税费合计" disabled={cash} className={inputClass} value={row.fee_cents === null ? '' : row.fee_cents/100} onChange={event => editRow(row.row_id,{fee_cents:event.target.value === '' ? null : Math.round(Number(event.target.value)*100)})}/></td><td className="w-32 p-2"><input type="number" step="0.01" aria-label="交割净额" className={inputClass} value={row.net_cash_cents == null ? '' : row.net_cash_cents / 100} onChange={event => editRow(row.row_id, {net_cash_cents: event.target.value === '' ? null : Math.round(Number(event.target.value) * 100)})}/><p className="mt-1 text-[10px] text-[var(--text-secondary)]">买入/出金为负，含税费</p></td></tr>;})}</tbody></table></div></fieldset>}
        {(duplicateIds.length > 0 || batchDuplicates.length > 0) && !receipt && <fieldset disabled={!!busy} className="space-y-3 rounded-xl border border-amber-400/20 bg-amber-400/5 p-4 text-xs"><legend className="px-1 text-amber-300">发现疑似重复，请核对原始成交</legend><p className="text-[var(--text-secondary)]">相同时间和价格可能是不同分笔，系统不会自动删除。选择替换将保留旧记录的冲正轨迹。</p>{batchDuplicates.map((item,index) => <p key={`${item.rowId}-${index}`} className="text-amber-200">记录 {item.rowId} 与本批第 {item.index + 1} 行疑似重叠，请取消重复行或确认确为不同成交。</p>)}{duplicateIds.map(id => <label key={id} className="flex items-center gap-2"><input type="checkbox" checked={replacements.has(id)} onChange={event => {setReplacements(old => {const next = new Set(old); if(event.target.checked) next.add(id); else next.delete(id); return next;}); setReplaceConfirmed(false); invalidate();}}/>替换旧记录 #{id} · {(() => {const match = chosen.flatMap(row => row.duplicate_candidates || []).find(item => item.transaction_id === id); return match ? `${match.trade_date || ''} ${match.symbol || ''} ${match.business_type === 'BUY' ? '买入' : match.business_type === 'SELL' ? '卖出' : match.business_type || ''} ${match.quantity ?? ''}股${match.price_micros ? ` @ ${match.price_micros / 1000000}` : ''}` : '';})()}</label>)}{replacements.size > 0 && <label className="flex items-center gap-2"><input type="checkbox" checked={replaceConfirmed} onChange={event => {setReplaceConfirmed(event.target.checked); invalidate();}}/>确认冲正所选旧记录，并用本批成交替换</label>}<label className="flex items-center gap-2"><input type="checkbox" checked={duplicatesConfirmed} onChange={event => {setDuplicatesConfirmed(event.target.checked); invalidate();}}/>已核对其余疑似重复，仍作为新成交导入</label></fieldset>}
        {preview && <div className="space-y-3 rounded-xl border border-cyan-400/20 bg-cyan-400/5 p-4"><p className="text-sm font-medium">入账预览</p><p className="text-sm">现金 <span className="font-mono">¥{money(preview.cash_before_cents)} → ¥{money(preview.cash_after_cents)}</span></p><div className="flex flex-wrap gap-2">{positions.map(symbol => {const before = preview.positions_before.find(item => item.symbol === symbol)?.quantity || 0; const after = preview.positions_after.find(item => item.symbol === symbol)?.quantity || 0; return before === after ? null : <span key={symbol} className="rounded bg-black/20 px-2 py-1 text-xs">{symbol}：{before} → {after} 股</span>;})}</div>{preview.warnings.map((warning,index) => <p key={index} className="text-xs text-amber-200">{warning}</p>)}{preview.needs_duplicate_confirmation && <p className="text-xs text-amber-300">请先处理疑似重复，再重新预览。</p>}</div>}
        {receipt && <div role="status" className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 p-5"><p className="flex items-center gap-2 font-medium text-emerald-300"><CheckCircle2 size={18}/>已入账 {receipt.booked_count} 笔{receipt.duplicate_replay ? '（已确认的同一批次）' : ''}</p><p className="mt-2 text-sm">账户现金 ¥{money(receipt.cash_after_cents)}</p><p className="mt-1 text-xs text-[var(--text-secondary)]">权益更新：{receipt.performance_status === 'COMPLETE' ? '已更新' : receipt.performance_status === 'PENDING' ? '待重算' : '更新未完成，请稍后核对'}</p></div>}
        {error && <p role="alert" className="rounded-lg border border-red-400/20 bg-red-400/5 p-3 text-sm text-red-300">{error}</p>}
      </div>}</div>
      {tab === 'screenshot' && <footer className="flex items-center justify-between gap-3 border-t border-white/10 px-6 py-4"><p className="text-xs text-[var(--text-secondary)]">{receipt ? '成交已保存，可关闭后查看持仓。' : '缺失字段请补全；价格变化不会作为现金流水。'}</p><div className="flex shrink-0 gap-2">{receipt ? <button type="button" className={buttonClass} onClick={onClose}>完成</button> : <><button type="button" disabled={!chosen.length || !!busy || !accountId || (replacements.size > 0 && !replaceConfirmed)} onClick={makePreview} className={buttonClass}>{busy === 'preview' ? '核算中…' : '预览入账'}</button><button type="button" disabled={!preview?.draft_id || preview.needs_duplicate_confirmation || !!busy} onClick={commit} className={`${buttonClass} bg-cyan-400/15 text-cyan-200`}>{busy === 'commit' ? '入账中…' : '确认入账'}</button></>}</div></footer>}
    </section>
  </div>, document.body);
}
