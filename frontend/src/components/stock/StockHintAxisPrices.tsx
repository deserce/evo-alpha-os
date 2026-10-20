'use client';

import { useEffect, useState, type RefObject } from 'react';
import type ReactECharts from 'echarts-for-react';
import type { HintPriceLine } from './stockChartHintPrices';
import { arrangePriceLabels } from './stockHintAxisLayout';

type AxisLabel = HintPriceLine & { y: number; top: number };

/** Reproject only on chart changes; no polling or extra data requests. */
export function StockHintAxisPrices({ chartRef, lines, revision, ready }: {
  chartRef: RefObject<ReactECharts | null>; lines: HintPriceLine[]; revision: unknown; ready: number;
}) {
  const [labels, setLabels] = useState<AxisLabel[]>([]);
  useEffect(() => {
    const chart = chartRef.current?.getEchartsInstance();
    if (!chart || !lines.length) { setLabels([]); return; }
    let frame = 0;
    const refresh = () => {
      frame = 0;
      if (chart.isDisposed()) return;
      const grid = (chart.getOption().grid as { top: number | string; height: number | string }[])[0];
      if (!grid) return;
      const pixels = (value: number | string) => typeof value === 'number' ? value
        : value.endsWith('%') ? parseFloat(value) * chart.getHeight() / 100 : Number(value);
      const top = pixels(grid.top), height = pixels(grid.height);
      if (!Number.isFinite(top) || !Number.isFinite(height)) return;
      const projected = lines.map(line => ({ ...line, y: Number(chart.convertToPixel({ yAxisIndex: 0 }, line.price)) }));
      const next = arrangePriceLabels(projected, top + 8, top + height - 8);
      setLabels(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(refresh); };
    chart.on('finished', schedule);
    chart.on('datazoom', schedule);
    const observer = new ResizeObserver(schedule);
    observer.observe(chart.getDom());
    schedule();
    return () => {
      if (frame) cancelAnimationFrame(frame);
      observer.disconnect();
      if (!chart.isDisposed()) { chart.off('finished', schedule); chart.off('datazoom', schedule); }
    };
  }, [chartRef, lines, revision, ready]);
  return <div className="pointer-events-none absolute inset-y-0 right-0 z-20 w-12" aria-label="策略关键价位">
    <svg className="absolute inset-0 h-full w-full" aria-hidden="true">
      {labels.map(line => <line key={line.price} x1="0" x2="7" y1={line.y} y2={line.top} stroke={line.color} strokeWidth="1" />)}
    </svg>
    {labels.map(line => <span key={line.price} data-hint-axis-price={line.price}
      title={`${line.label} · ${line.price.toFixed(2)}`} style={{ top: line.top, backgroundColor: line.color }}
      className="pointer-events-auto absolute right-1 -translate-y-1/2 cursor-help rounded px-1 py-0.5 font-mono text-[9px] leading-3 text-[#07111f]">
      {line.price.toFixed(2)}
    </span>)}
  </div>;
}
