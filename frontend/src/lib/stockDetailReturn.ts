/** Restrict detail-page returns to known local workspaces and bounded filters. */
export function stockDetailReturn(value: string | null): string {
  if (value === '/a/strategy' || value === '/a/watchlist') return value;
  if (!value || value.length > 2048 || !/^\/a\/research(?:\?|$)/.test(value)) {
    return '/a/watchlist';
  }
  const url = new URL(value, 'http://evoalpha.local');
  if (url.pathname !== '/a/research' || url.hash) return '/a/watchlist';
  const allowed = new Set(['q', 'symbol', 'report_type', 'industry', 'org',
    'date_from', 'date_to', 'page', 'v']);
  for (const [key, item] of url.searchParams) {
    if (!allowed.has(key) || item.length > 120 || /[\u0000-\u001f]/.test(item)) {
      return '/a/watchlist';
    }
    if (key === 'symbol' && item && !/^[0-9]{6}$/.test(item)) return '/a/watchlist';
  }
  return url.pathname + url.search;
}
