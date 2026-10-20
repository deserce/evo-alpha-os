import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const chartSource = readFileSync(
  new URL('../src/components/sector/SectorChart.tsx', import.meta.url),
  'utf8',
);
const apiSource = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');

test('板块主图默认使用本地等权 OHLC 而不是净值线', () => {
  assert.match(chartSource, /type DataMode = 'local' \| 'composite' \| 'vendor'/);
  assert.match(chartSource, /useState<DataMode>\(mode \?\? 'local'\)/);
  assert.match(chartSource, /dataMode === 'composite' \? netValues\.length > 0 : candles\.length > 0/);
});

test('本地等权 K 线开放蜡烛美国线和 KDJ 且主控件不再显示净值', () => {
  assert.match(chartSource, /dataMode !== 'composite' && indicators\.kdj/);
  assert.match(chartSource, /本地等权K线/);
  assert.doesNotMatch(chartSource, /label: '净值'/);
});

test('板块图 API 客户端默认请求 local 模式', () => {
  assert.match(apiSource, /mode\?: 'local' \| 'composite' \| 'vendor'/);
  assert.match(apiSource, /mode: params\.mode \?\? 'local'/);
});
