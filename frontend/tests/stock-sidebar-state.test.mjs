import assert from 'node:assert/strict';
import test from 'node:test';
import { DEFAULT_STOCK_SIDEBAR_UI, patchStockSidebarUi, boundedSidebarRecord } from '../src/lib/stockSidebarState.ts';

test('同源侧栏保留页签资料指标及提示范围', () => {
  const first = patchStockSidebarUi(DEFAULT_STOCK_SIDEBAR_UI, { tab: 'hints', infoTab: 'f10', financialMetric: 'net_profit', scope: { poolId: 1, poolName: '自选', date: '2026-09-30', members: [{ symbol: '000703' }] } });
  const next = patchStockSidebarUi(first, { infoHidden: true });
  assert.equal(next.tab, 'hints');
  assert.equal(next.infoTab, 'f10');
  assert.equal(next.financialMetric, 'net_profit');
  assert.strictEqual(next.scope, first.scope);
});

test('分组成员去重过滤并限定300只', () => {
  const members = Array.from({ length: 400 }, (_, i) => ({ symbol: String(i).padStart(6, '0') }));
  const next = patchStockSidebarUi(DEFAULT_STOCK_SIDEBAR_UI, { scope: { poolId: 1, poolName: '自选', date: '', members: [{ symbol: 'bad' }, ...members, ...members] } });
  assert.equal(next.scope.members.length, 300);
  assert.equal(new Set(next.scope.members.map(x => x.symbol)).size, 300);
});

test('滚动缓存最多32条且输入有界', () => {
  let positions = {};
  for (let i = 0; i < 40; i++) positions = boundedSidebarRecord(positions, `k${i}`, i);
  assert.equal(Object.keys(positions).length, 32);
  assert.equal(positions.k0, undefined);
  const next = patchStockSidebarUi(DEFAULT_STOCK_SIDEBAR_UI, { scrollPositions: { negative: -3, excessive: 200000, invalid: NaN } });
  assert.deepEqual(next.scrollPositions, { negative: 0, excessive: 100000 });
});

test('非法页签不覆盖已有状态，范围可以清空', () => {
  const next = patchStockSidebarUi(DEFAULT_STOCK_SIDEBAR_UI, { tab: 'invalid', scope: null });
  assert.equal(next.tab, 'info');
  assert.equal(next.scope, null);
});

test('提示分类跨页保留，非法分类不覆盖已有选择', () => {
  const selected=patchStockSidebarUi(DEFAULT_STOCK_SIDEBAR_UI,{hintsFilter:'buy'});
  assert.equal(patchStockSidebarUi(selected,{tab:'ai'}).hintsFilter,'buy');
  assert.equal(patchStockSidebarUi(selected,{hintsFilter:'bad'}).hintsFilter,'buy');
});
