/** Preserve exact line positions while separating nearby axis labels. */
export function arrangePriceLabels<T extends { y: number }>(rows: T[], min: number, max: number) {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [];
  const visible = rows.filter(row => Number.isFinite(row.y) && row.y >= min - 8 && row.y <= max + 8)
    .slice(0, 12).sort((a, b) => a.y - b.y);
  const gap = Math.min(18, (max - min) / Math.max(1, visible.length - 1));
  const placed = visible.map(row => ({ ...row, top: Math.min(max, Math.max(min, row.y)) }));
  for (let i = 1; i < placed.length; i++) placed[i].top = Math.max(placed[i].top, placed[i - 1].top + gap);
  for (let i = placed.length - 1; i >= 0; i--) placed[i].top = Math.min(placed[i].top, i === placed.length - 1 ? max : placed[i + 1].top - gap);
  return placed;
}
