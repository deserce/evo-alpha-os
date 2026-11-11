import type { ResearchSyncStatus } from './researchApi';

/** Accept only direct HTTP(S) links without embedded credentials. */
export function safeResearchUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.href : undefined;
  } catch { return undefined; }
}

export function researchPageNumber(value: string | null): number {
  const page = Number(value);
  return Number.isSafeInteger(page) && page > 0 ? Math.min(100000, page) : 1;
}

export function researchStockHref(symbol: string, returnTo?: string, asOfDate?: string): string {
  const params = new URLSearchParams({ returnTo: returnTo || `/a/research?symbol=${symbol}` });
  if (asOfDate && /^\d{4}-\d{2}-\d{2}$/.test(asOfDate)) params.set('trade_date', asOfDate);
  return `/a/stock/${encodeURIComponent(symbol)}?${params}`;
}

/** Format source timestamps in the product's fixed Asia/Shanghai timezone. */
export function formatResearchTime(value: string | null | undefined): string {
  if (!value) return '未提供';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '未提供';
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(date);
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value || '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}


export function researchSyncMessage(sync?: Partial<ResearchSyncStatus>): string {
  if (!sync) return '更新状态暂不可用，请刷新列表重试。';
  switch (sync.status) {
    case 'never': return '尚无自动更新记录；已有研报仍可查询。';
    case 'running': return '正在更新研报；完成后可刷新列表查看。';
    case 'success': return `最近一次更新成功：新增 ${sync.inserted || 0} 篇，更新 ${sync.updated || 0} 篇。`;
    case 'partial': return `最近一次更新仅部分完成${sync.error ? `：${sync.error}` : ''}；不能视为完整更新。`;
    case 'failed': return `最近一次更新失败${sync.error ? `：${sync.error}` : ''}；保留已有研报，请查看最近成功时间。`;
    default: return '更新状态暂不可用，请刷新列表重试。';
  }
}

export type ResearchDateMode = 'auto' | 'latest' | 'asof';

/** Keep historical research bounded while latest-market views include weekend publications. */
export function resolveResearchCutoff(
  date: string | undefined,
  latest: string | undefined,
  mode: ResearchDateMode = 'auto',
  forceAsOf = false,
): string | undefined {
  if (mode === 'latest' || !date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return undefined;
  if (mode === 'asof' || forceAsOf) return date;
  if (!latest || !/^\d{4}-\d{2}-\d{2}$/.test(latest)) return date;
  return date < latest ? date : undefined;
}
