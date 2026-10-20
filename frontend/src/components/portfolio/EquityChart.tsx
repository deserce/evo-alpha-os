'use client';

import { useId, useMemo, useState } from 'react';
import { Area, AreaChart, CartesianGrid, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { PortfolioPerformanceSnapshot } from '@/lib/api';
import { buildEquitySeries, type EquityMetric, type EquityPoint, type EquityRange } from '@/lib/portfolio-chart';

interface Props { snapshots: PortfolioPerformanceSnapshot[]; baselineDate?: string | null }
const amount = (value: number | null) => value === null ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function Detail({ point }: { point?: EquityPoint }) {
  if (!point) return null;
  return <div className="rounded-xl border border-white/10 bg-[var(--bg-secondary)] p-4 text-xs shadow-lg">
    <p className="mb-3 font-semibold text-[var(--text-primary)]">{point.date}</p>
    {([['总资产', point.equity], ['现金', point.cash], ['股票市值', point.market], ['当日盈亏', point.dailyPnl], ['当日资金流', point.flow]] as const).map(([label, value]) => <div key={label} className="mt-1.5 flex min-w-48 justify-between gap-6"><span className="text-[var(--text-secondary)]">{label}</span><span className="font-mono tabular-nums text-[var(--text-primary)]">{amount(value)} 元</span></div>)}
  </div>;
}
export default function EquityChart({ snapshots, baselineDate }: Props) {
  const [metric, setMetric] = useState<EquityMetric>('equity');
  const [range, setRange] = useState<EquityRange>('all');
  const gradientId = useId().replaceAll(':', '');
  const data = useMemo(() => buildEquitySeries(snapshots, range), [snapshots, range]);
  const values = data.map(point => point[metric]).filter((value): value is number => value !== null);
  const latest = data.at(-1);
  const lastValue = latest?.[metric] ?? null;
  const min = Math.min(0, ...values), max = Math.max(0, ...values);
  const zeroOffset = max === min ? 0.5 : max / (max - min);
  const isEquity = metric === 'equity';
  const label = metric === 'equity' ? '总资产' : metric === 'pnl' ? '区间累计盈亏' : '区间收益率';
  return <section className="rounded-2xl border border-white/10 bg-[var(--bg-secondary)] p-2">
    <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold text-[var(--text-primary)]">账户权益</h2>
        <div className="flex flex-wrap items-center gap-2"><p className="text-[10px] text-[var(--text-secondary)]">{label} · {latest?.date ?? '暂无日期'}</p><p className={`text-lg font-semibold tabular-nums tracking-tight ${isEquity ? 'text-[var(--text-primary)]' : lastValue !== null && lastValue < 0 ? 'text-emerald-400' : 'text-red-400'}`}>{amount(lastValue)}<span className="ml-1 text-xs font-normal text-[var(--text-secondary)]">{metric === 'return' ? '%' : '元'}</span></p></div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg bg-[var(--bg-primary)] p-0.5" role="group" aria-label="权益指标">{(['equity', 'pnl', 'return'] as const).map((item, i) => <button key={item} type="button" aria-pressed={metric === item} onClick={() => setMetric(item)} className={`rounded-md px-2 py-1 text-xs transition-colors ${metric === item ? 'bg-[var(--bg-secondary)] font-medium text-blue-400 shadow-sm' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>{['总资产', '累计盈亏', '收益率'][i]}</button>)}</div>
        <div className="flex gap-1" role="group" aria-label="权益区间">{(['1m', '3m', 'all'] as const).map((item, i) => <button type="button" key={item} aria-pressed={range === item} onClick={() => setRange(item)} className={`rounded-md px-2 py-1 text-xs ${range === item ? 'bg-blue-500/15 font-medium text-blue-400' : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]'}`}>{['近1月', '近3月', '全部'][i]}</button>)}</div>
      </div>
    </div>
    {values.length ? <div className="w-full" style={{ height: 'clamp(150px,19vh,170px)' }}><ResponsiveContainer width="100%" height="100%"><AreaChart data={data} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
      <defs><linearGradient id={`${gradientId}-fill`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#3b82f6" stopOpacity={0.2}/><stop offset="100%" stopColor="#3b82f6" stopOpacity={0.015}/></linearGradient><linearGradient id={`${gradientId}-signed`} x1="0" y1="0" x2="0" y2="1"><stop offset={zeroOffset} stopColor="#ef4444"/><stop offset={zeroOffset} stopColor="#10b981"/></linearGradient></defs>
      <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.07)" strokeDasharray="3 5"/>
      <XAxis dataKey="date" axisLine={false} tickLine={false} minTickGap={36} tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} tickFormatter={value => String(value).slice(5)}/>
      <YAxis axisLine={false} tickLine={false} width={66} domain={isEquity ? ['auto', 'auto'] : [min, max]} tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} tickFormatter={(value: number) => metric === 'return' ? `${value.toFixed(1)}%` : Math.abs(value) >= 10000 ? `${(value / 10000).toFixed(1)}万` : value.toFixed(0)}/>
      <Tooltip cursor={{ stroke: 'rgba(255,255,255,0.18)', strokeDasharray: '3 4' }} content={({ active, payload }) => active ? <Detail point={payload?.[0]?.payload as EquityPoint | undefined}/> : null}/>
      {!isEquity && <ReferenceLine y={0} stroke="rgba(255,255,255,0.25)" strokeDasharray="4 4"/>}
      <Area type="linear" dataKey={metric} stroke={isEquity ? '#3b82f6' : `url(#${gradientId}-signed)`} strokeWidth={2} fill={isEquity ? `url(#${gradientId}-fill)` : `url(#${gradientId}-signed)`} fillOpacity={isEquity ? 1 : 0.07} connectNulls={false} isAnimationActive={false} dot={values.length === 1 ? { r: 4 } : false} activeDot={{ r: 4, strokeWidth: 2, stroke: 'var(--bg-secondary)' }}/>
      {data.filter(point => point.flow !== 0 && point[metric] !== null).map(point => <ReferenceDot key={point.date} x={point.date} y={point[metric]!} r={4} fill="#6366f1" stroke="var(--bg-secondary)" strokeWidth={2}/>) }
    </AreaChart></ResponsiveContainer></div> : <div className="flex items-center justify-center rounded-xl bg-[var(--bg-tertiary)] text-sm text-[var(--text-secondary)]" style={{ height: 'clamp(150px,19vh,170px)' }}>{data.length ? '收益链不完整，待行情与快照补齐' : '暂无权益快照，记账后生成'}</div>}
    <div className="mt-1 flex flex-wrap justify-between gap-2 text-[11px] text-[var(--text-secondary)]"><span>{data[0]?.date ?? '—'} — {latest?.date ?? '—'} · 最多400交易日</span><span>{baselineDate ? `自 ${baselineDate} 对账 · ` : ''}资金往来不计收益 · 紫点：入金 / 出金</span></div>
    {!isEquity && data.some(point => point[metric] === null) && <p className="mt-2 text-xs text-amber-400">区间收益链存在缺口，缺口后的累计值暂不展示。</p>}
  </section>;
}
