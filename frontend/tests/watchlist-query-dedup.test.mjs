import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const workspaceSource = readFileSync(
  new URL('../src/components/stock/StockWorkspace.tsx', import.meta.url),
  'utf8',
);

test('个股图表在主面板和资料面板共用同一查询缓存', () => {
  assert.doesNotMatch(workspaceSource, /stockEnrichmentChart/);
  assert.equal(
    workspaceSource.match(/queryKey: \['stockChartRows', symbol, selectedDate \|\| 'latest'\]/g)?.length,
    2,
  );
});

test('生命周期数据不在个股资料面板重复下载', () => {
  assert.doesNotMatch(workspaceSource, /stockEnrichmentLifecycle/);
  assert.equal(workspaceSource.match(/queryKey: \['warRoomLifecycle', selectedDate\]/g)?.length, 2);
});

test('买点信号数据不在个股资料面板重复下载', () => {
  assert.doesNotMatch(workspaceSource, /stockEnrichmentSignals/);
  assert.equal(workspaceSource.match(/queryKey: \['warRoomSignals', selectedDate\]/g)?.length, 2);
});
