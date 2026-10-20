'use client';

import { useState } from 'react';
import { STRUCTURE_LABELS, structureVersionLabel, type StructureCandidatePayload } from './structureCandidatePresentation';

function number(value: unknown, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
}

const STATE_LABELS: Record<string, string> = {
  WATCHING: '等待结构', OBSERVING: '已进入观察', HOLDING: '策略持有中（模拟）', BLOCKED: '数据阻断',
};

export default function StructureStrategyPanel({ payload }: { payload: StructureCandidatePayload }) {
  const [selected, setSelected] = useState('');
  const markers = payload.markers ?? [];
  const daily = !payload.period || payload.period === 'daily';
  const previous = daily ? '前日' : '前一根';
  const unit = daily ? '天' : '根';
  const event = markers.find((m, i) => `${m.trade_date}:${m.marker_type}:${i}` === selected) ?? markers.at(-1);
  const state = payload.state;
  const evidence = event?.factors;
  const checks = evidence?.checks ?? [];
  const pivots = evidence?.pivot_lows as Array<{ trade_date: string; confirmed_date: string; price: number }> | undefined;
  return (
    <details className="border-b border-[#1e2226] px-2 py-1 text-[10px] text-text-secondary">
      <summary className="cursor-pointer text-violet-200">C策略规则、触发依据与买卖过程 · {structureVersionLabel(payload.version)}</summary>
      <div className="max-h-64 space-y-2 overflow-auto py-2">
        <p className="text-violet-200">回放截至 {state?.trade_date ?? payload.history_end ?? '—'}：{STATE_LABELS[state?.status ?? ''] ?? '尚无状态'}
          {state?.stop_price != null && ` · 结构失效线 ${number(state.stop_price)}`}</p>
        {state?.waiting_for?.length ? <p>下一步：{state.waiting_for.join('；')}</p> : null}
        <table className="w-full text-left leading-relaxed"><tbody>
          <tr><th className="w-20 align-top text-violet-200">观察</th><td>回踩路径：两个低点经右侧2根K线确认，形成双底或低点抬高，容差按ATR判断。背离路径：价格新低而MACD负柱较参考低点抬高，识别下跌动能衰减。</td></tr>
          <tr><th className="align-top text-violet-200">买入确认</th><td>回踩路径：观察后收复MA5、突破{previous}高点、MACD改善。背离路径：背离同时有价格止跌、辅助动能与风险条件支持；证据不足仅观察。具体通过项见下方。</td></tr>
          <tr><th className="align-top text-amber-200">观察失效</th><td>收盘跌破结构失效线、观察窗口超期，或被新的有效结构替代。尚未买入时的失效不是卖出。</td></tr>
          <tr><th className="align-top text-rose-300">卖出条件</th><td>确认后收盘跌破失效线，或连续两{unit}低于MA10且MACD未改善；顶部背离先预警，结合价格破坏才退出。策略只提示，不代表真实成交。</td></tr>
        </tbody></table>
        <div className="rounded border border-border-secondary p-2">
          <label className="flex flex-wrap items-center gap-2">逐根触发依据
            <select aria-label="C信号触发依据" value={event ? `${event.trade_date}:${event.marker_type}:${markers.indexOf(event)}` : ''}
              onChange={(e) => setSelected(e.target.value)} className="rounded bg-bg-primary px-1 py-0.5 text-text-primary">
              {markers.map((m, i) => <option key={`${m.trade_date}:${m.marker_type}:${i}`} value={`${m.trade_date}:${m.marker_type}:${i}`}>
                {m.trade_date} {STRUCTURE_LABELS[m.marker_type]}
              </option>).reverse()}
            </select>
          </label>
          {event ? <>
            <p className="mt-1">{event.reason} · 柱收盘价 {number(event.price)} · 失效线 {number(event.stop_price)}</p>
            {pivots?.map((p) => <p key={p.trade_date}>摆动低点 {p.trade_date} / {number(p.price)}，确认于 {p.confirmed_date}</p>)}
            <p>MA5 {number(evidence?.ma5)} · MA10 {number(evidence?.ma10)} · {previous}最高 {number(evidence?.previous_high)} · ATR {number(evidence?.atr)}</p>
            <p>MACD柱 {number(evidence?.macd_hist, 4)} / {previous} {number(evidence?.macd_hist_previous, 4)} · 连续走弱 {number(evidence?.weak_days, 0 )} {unit}</p>
            {checks.length > 0 && <ul className="mt-1 space-y-0.5">{checks.map((check, i) => (
              <li key={`${check.dimension}:${i}`} className={check.passed === true ? 'text-emerald-300' : check.passed === false ? 'text-amber-200' : 'text-text-secondary'}>
                {check.passed === true ? '✓' : check.passed === false ? '○' : '·'} {check.label}：{check.value}
              </li>
            ))}</ul>}
          </> : <p>当前区间无信号；请查看上方等待条件。</p>}
        </div>
        <p className="text-text-tertiary">最近8笔覆盖当前区间的完整过程。按信号收盘参考，未计费用、滑点、涨跌停与T+1；最大有利/不利变动统计整个持有阶段。</p>
        {!payload.trades?.length ? <p>当前区间没有买入确认。</p> : <table className="w-full text-left tabular-nums">
          <thead><tr><th>买入确认</th><th>卖出信号</th><th>持有K线</th><th>最大有利</th><th>最大不利</th></tr></thead>
          <tbody>{payload.trades.slice(-8).reverse().map((trade) => (
            <tr key={trade.entry_date} className="border-t border-border-secondary" title={trade.exit_reason ?? '仍未退出'}>
              <td>{trade.entry_date} / {number(trade.entry_price)}</td>
              <td>{trade.exit_date ? `${trade.exit_date} / ${number(trade.exit_price)}` : '未退出'}</td>
              <td>{trade.holding_bars}</td><td>{number(trade.mfe_pct)}%</td><td>{number(trade.mae_pct)}%</td>
            </tr>
          ))}</tbody>
        </table>}
      </div>
    </details>
  );
}
