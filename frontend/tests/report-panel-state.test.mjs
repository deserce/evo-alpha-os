import assert from 'node:assert/strict';
import test from 'node:test';
import { useGlobalStore } from '../src/lib/store.ts';

test('同日详情往返保留AI页签、分组、筛选与输入', () => {
  useGlobalStore.getState().syncReportPanelDate('2026-09-30');
  useGlobalStore.getState().setReportPanelUi({ view: 'report', group: 'watchlist', filter: 'buy', sector: '电子', question: '查看机会' });
  const before = useGlobalStore.getState().reportPanelUi;
  useGlobalStore.getState().syncReportPanelDate('2026-09-30');
  assert.strictEqual(useGlobalStore.getState().reportPanelUi, before);
});

test('切换交易日清理问答与旧筛选，保留页签和开关', () => {
  useGlobalStore.getState().setReportOpen(true);
  useGlobalStore.getState().syncReportPanelDate('2026-09-29');
  const ui = useGlobalStore.getState().reportPanelUi;
  assert.equal(ui.view, 'report');
  assert.equal(ui.group, 'all');
  assert.equal(ui.filter, 'all');
  assert.equal(ui.question, '');
  assert.equal(ui.answer, null);
  assert.equal(useGlobalStore.getState().reportOpen, true);
});

test('加载中空日期与关闭重新开启不丢失侧边栏会话', () => {
  useGlobalStore.getState().setReportPanelUi({ question: '保留草稿' });
  const before = useGlobalStore.getState().reportPanelUi;
  useGlobalStore.getState().syncReportPanelDate('');
  useGlobalStore.getState().setReportOpen(false);
  useGlobalStore.getState().setReportOpen(true);
  assert.strictEqual(useGlobalStore.getState().reportPanelUi, before);
});

test('切换股票不清除已展示的问答结果', () => {
  const answer = { question: '自选机会', title: '结果', stocks: [], group: 'watchlist', allStocks: [], total: 0, sector: false, unsupported: false };
  useGlobalStore.getState().setReportPanelUi({ answer });
  useGlobalStore.getState().setSelectedStock('603268');
  useGlobalStore.getState().setSelectedStock('301108');
  useGlobalStore.getState().syncReportPanelDate('2026-09-29');
  assert.strictEqual(useGlobalStore.getState().reportPanelUi.answer, answer);
});
