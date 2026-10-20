export type StockNavigationDirection = -1 | 1;

export function nextStockIndex(
  currentIndex: number,
  length: number,
  direction: number,
) {
  if (!Number.isInteger(length) || length <= 0 || (direction !== -1 && direction !== 1)) {
    return -1;
  }
  const validCurrent = Number.isInteger(currentIndex) && currentIndex >= 0 && currentIndex < length;
  const baseIndex = validCurrent ? currentIndex : direction > 0 ? -1 : 0;
  return Math.min(length - 1, Math.max(0, baseIndex + direction));
}
