import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { nextStockIndex } from '../src/components/stock/stockTableNavigation.ts';

test('方向键在当前股票上下移动', () => {
  assert.equal(nextStockIndex(1, 5, 1), 2);
  assert.equal(nextStockIndex(1, 5, -1), 0);
});

test('方向键在列表边界停住且未选择时从首行开始', () => {
  assert.equal(nextStockIndex(4, 5, 1), 4);
  assert.equal(nextStockIndex(0, 5, -1), 0);
  assert.equal(nextStockIndex(-1, 5, 1), 0);
  assert.equal(nextStockIndex(-1, 5, -1), 0);
});

test('空列表或无效方向不产生选择', () => {
  assert.equal(nextStockIndex(0, 0, 1), -1);
  assert.equal(nextStockIndex(0, 5, 0), -1);
});

test('Watchlist 股票列表接入键盘焦点、自动滚动和统一索引计算', () => {
  const source = readFileSync(
    new URL('../src/components/stock/StockWorkspace.tsx', import.meta.url),
    'utf8',
  );
  assert.match(source, /nextStockIndex/);
  assert.match(source, /tabIndex=\{0\}/);
  assert.match(source, /scrollIntoView\(\{ block: 'nearest' \}\)/);
  assert.match(source, /event\.key === 'ArrowDown'/);
});
