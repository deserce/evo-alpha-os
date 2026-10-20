import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  buildStockChartTooltip,
  resolveTooltipIndex,
} from '../src/components/stock/stockChartTooltip.ts';

const baseInput = {
  dates: ['2026-06-09', '2026-06-10'],
  candles: [
    [10, 10.2, 9.8, 10.4],
    [10.1, 10.5, 10, 10.6],
  ],
  pctChanges: [2, 2.94],
  rpsSeries: [
    { label: 'RPS5', values: [86.3, 91.2] },
    { label: 'RPS20', values: [80.1, 83.4] },
    { label: 'RPS50', values: [72.8, 75.6] },
    { label: 'RPS250', values: [55.2, 56.7] },
  ],
};

test('悬浮提示只保留日期、股价、涨跌幅和对应 RPS', () => {
  const html = buildStockChartTooltip(1, baseInput);

  assert.match(html, /2026-06-10/);
  assert.match(html, /股价/);
  assert.match(html, /10\.50/);
  assert.match(html, /涨跌幅/);
  assert.match(html, /\+2\.94%/);
  assert.match(html, /RPS5 91\.2 · RPS20 83\.4 · RPS50 75\.6 · RPS250 56\.7/);
  assert.doesNotMatch(html, /MA5|成交量|MACD|KDJ|开盘|最高|最低/);
});

test('涨跌幅缺失时由相邻收盘价计算，RPS 缺失显示短横线', () => {
  const html = buildStockChartTooltip(1, {
    ...baseInput,
    pctChanges: [null, null],
    rpsSeries: [{ label: 'RPS50', values: [72.8, null] }],
  });

  assert.match(html, /\+2\.94%/);
  assert.match(html, /RPS50 -/);
});

test('无效索引安全返回空内容，轴参数可解析出有效日期索引', () => {
  assert.equal(buildStockChartTooltip(-1, baseInput), '');
  assert.equal(resolveTooltipIndex([{ dataIndex: 1 }], baseInput.dates), 1);
  assert.equal(resolveTooltipIndex([{ axisValue: '2026-06-09' }], baseInput.dates), 0);
  assert.equal(resolveTooltipIndex([], baseInput.dates), -1);
});

test('Watchlist 为当前股票提供直达个股详情入口', () => {
  const source = readFileSync(
    new URL('../src/components/stock/StockWorkspace.tsx', import.meta.url),
    'utf8',
  );

  assert.match(source, /showDetailLink/);
  assert.match(source, /href=\{`\/a\/stock\/\$\{symbol\}`\}/);
  assert.match(source, />\s*个股详情\s*</);
});
