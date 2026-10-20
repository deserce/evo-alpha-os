import type { ChanPoint } from './stockChartChanOverlay';

export type ChanTextMode = 'repaint' | 'original';

/** Display-only wording: never upgrades an original repainting point to confirmation. */
export function chanPointLabel(point: ChanPoint, mode: ChanTextMode): string {
  if (mode === 'original') return point.label;
  if (point.kind === 'pivot') return point.direction === 'low' ? '低点' : '高点';
  return `回绘·${point.label.replace(/[\[\]\s]/g, '')}`;
}

export function chanPointExplanation(position: string, cutoff: string, start: string): string {
  return `图形位置：${position}；计算截止：${cutoff}。这是原式回绘，尚不能据此认定当时已确认，后续可能移动或撤销。计算起点：${start}；起点改变也可能影响历史标记。`;
}
