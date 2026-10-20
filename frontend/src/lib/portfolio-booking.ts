export type BookingKind = 'BUY' | 'SELL' | 'DEPOSIT' | 'WITHDRAWAL';
export interface BookingRow {
  row_id: string; business_type: BookingKind | null; trade_date: string | null;
  trade_time: string | null; symbol: string | null; name?: string | null;
  price_micros: number | null; quantity: number | null; fee_cents: number | null;
  amount_cents: number | null; net_cash_cents?: number | null; image_index?: number;
  warnings?: string[]; duplicate_candidates?: { transaction_id?: number; batch_row_index?: number; business_type?: string; trade_date?: string; symbol?: string; quantity?: number; price_micros?: number; occurred_at?: string }[];
  duplicate_confirmed?: boolean;
}
export function rowIssues(row: BookingRow): string[] {
  const issues: string[] = [];
  const date = row.trade_date || '';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(date)) || new Date(date).toISOString().slice(0, 10) !== date) issues.push('补全成交日期');
  if (!/^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/.test(row.trade_time || '')) issues.push('补全时间');
  if (!['BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL'].includes(row.business_type || '')) issues.push('选择业务类型');
  if (row.business_type === 'BUY' || row.business_type === 'SELL') {
    if (!/^\d{6}$/.test(row.symbol || '')) issues.push('补全六位代码');
    if (!Number.isSafeInteger(row.price_micros) || (row.price_micros ?? 0) <= 0) issues.push('补全成交价');
    if (!Number.isSafeInteger(row.quantity) || (row.quantity ?? 0) <= 0) issues.push('补全股数');
    if (!Number.isSafeInteger(row.fee_cents) || (row.fee_cents ?? -1) < 0) issues.push('补全税费');
  } else if (!Number.isSafeInteger(row.amount_cents) || (row.amount_cents ?? 0) <= 0) issues.push('补全资金金额');
  if (Number.isSafeInteger(row.net_cash_cents)) {
    let expected: number | null = null;
    if ((row.business_type === 'BUY' || row.business_type === 'SELL') && Number.isSafeInteger(row.price_micros) && Number.isSafeInteger(row.quantity) && Number.isSafeInteger(row.fee_cents)) {
      const gross = Math.round((row.price_micros! * row.quantity!) / 10000);
      expected = (row.business_type === 'BUY' ? -gross : gross) - row.fee_cents!;
    } else if (Number.isSafeInteger(row.amount_cents)) {
      if (row.business_type === 'DEPOSIT') expected = row.amount_cents;
      if (row.business_type === 'WITHDRAWAL') expected = -row.amount_cents!;
    }
    if (expected !== null && row.net_cash_cents !== expected) issues.push('交割净额与价格、数量及税费不一致');
  } else if (row.net_cash_cents != null) issues.push('交割净额须精确到分');
  return issues;
}
export function validateImages(files: readonly { size: number; type: string }[]): string | null {
  if (files.length > 8) return '一次最多上传 8 张截图';
  if (files.some(file => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type))) return '请上传 PNG、JPEG 或 WebP 图片';
  if (files.some(file => file.size > 8 * 1024 * 1024)) return '单张截图不能超过 8 MB';
  if (files.reduce((total, file) => total + file.size, 0) > 32 * 1024 * 1024) return '截图总大小不能超过 32 MB';
  return null;
}
export function bookingRequestId(): string {
  try { if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID(); } catch { /* Older browser fallback. */ }
  return `booking-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}
