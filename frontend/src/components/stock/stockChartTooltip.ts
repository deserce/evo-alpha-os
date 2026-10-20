export type TooltipNumber = number | null | undefined;

export interface StockChartTooltipInput {
  dates: string[];
  candles: TooltipNumber[][];
  pctChanges?: TooltipNumber[];
  showRps?: boolean;
  rpsSeries: Array<{
    label: string;
    values: TooltipNumber[];
  }>;
}

type AxisTooltipParam = {
  dataIndex?: unknown;
  axisValue?: unknown;
};

function finiteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function escapeHtml(value: unknown) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatPrice(value: unknown) {
  const parsed = finiteNumber(value);
  return parsed === null ? '-' : parsed.toFixed(2);
}

function formatPct(value: unknown) {
  const parsed = finiteNumber(value);
  if (parsed === null) return '-';
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(2)}%`;
}

function formatRps(value: unknown) {
  const parsed = finiteNumber(value);
  return parsed === null ? '-' : parsed.toFixed(1);
}

function closeAt(candles: TooltipNumber[][], index: number) {
  return finiteNumber(candles[index]?.[1]);
}

function pctAt(index: number, input: StockChartTooltipInput) {
  const supplied = finiteNumber(input.pctChanges?.[index]);
  if (supplied !== null) return supplied;

  const close = closeAt(input.candles, index);
  const previousClose = closeAt(input.candles, index - 1);
  if (close === null || previousClose === null || previousClose === 0) return null;
  return ((close / previousClose) - 1) * 100;
}

export function resolveTooltipIndex(params: unknown, dates: string[]) {
  const items = Array.isArray(params) ? params : [params];
  const first = items.find((item): item is AxisTooltipParam => Boolean(item && typeof item === 'object'));
  if (!first) return -1;

  const dataIndex = finiteNumber(first.dataIndex);
  if (dataIndex !== null && Number.isInteger(dataIndex) && dataIndex >= 0 && dataIndex < dates.length) {
    return dataIndex;
  }
  return dates.indexOf(String(first.axisValue ?? ''));
}

export function buildStockChartTooltip(index: number, input: StockChartTooltipInput) {
  if (!Number.isInteger(index) || index < 0 || index >= input.dates.length) return '';

  const pct = pctAt(index, input);
  const pctColor = pct === null ? '#d7dde8' : pct >= 0 ? '#ff4f6d' : '#28e070';
  const rps = input.rpsSeries
    .map((item) => `${escapeHtml(item.label)} ${formatRps(item.values[index])}`)
    .join(' · ');

  return [
    `<div style="min-width:178px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace">`,
    `<div style="margin-bottom:5px;color:#f5f7fb;font-weight:700">${escapeHtml(input.dates[index])}</div>`,
    `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:#8ea0bd">股价</span><b>${formatPrice(closeAt(input.candles, index))}</b></div>`,
    `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:#8ea0bd">涨跌幅</span><b style="color:${pctColor}">${formatPct(pct)}</b></div>`,
    ...(input.showRps === false ? [] : [
      `<div style="margin-top:5px;padding-top:5px;border-top:1px solid #27303a;color:#c4ccd8">${rps || 'RPS -'}</div>`,
    ]),
    '</div>',
  ].join('');
}
