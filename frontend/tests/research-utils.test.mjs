import assert from 'node:assert/strict';
import test from 'node:test';
import { safeResearchUrl, researchPageNumber } from '../src/lib/researchUtils.ts';

test('研报原文和 PDF 允许完整 HTTP(S) 地址并保留查询', () => {
  assert.equal(safeResearchUrl('https://pdf.example.com/report.pdf?id=abc'), 'https://pdf.example.com/report.pdf?id=abc');
  assert.equal(safeResearchUrl('http://example.com/report/1'), 'http://example.com/report/1');
});
test('缺失与格式错误的原文地址不会生成可点击链接', () => {
  for (const value of [null, undefined, '', 'not a url', '/local.pdf']) assert.equal(safeResearchUrl(value), undefined);
});
test('不允许执行协议或携带账号密码的研报地址', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,x', 'file:///etc/passwd', 'https://user:password@example.com/report']) assert.equal(safeResearchUrl(value), undefined);
});
test('分页保留正常页数并限制最大查询范围', () => {
  assert.equal(researchPageNumber('2'), 2);
  assert.equal(researchPageNumber('100001'), 100000);
});
test('分页缺失、负数、小数或非有限数恢复到第一页', () => {
  for (const value of [null, '', '0', '-1', '2.5', 'Infinity', 'NaN', 'text']) assert.equal(researchPageNumber(value), 1);
});

test('研究个股入口保留全筛选回跳与历史截止日', async () => {
  const { researchStockHref } = await import('../src/lib/researchUtils.ts');
  const returnTo = '/a/research?q=测试&org=中信&date_to=2026-10-01&page=3';
  const url = new URL(researchStockHref('000001', returnTo, '2026-10-01'), 'http://localhost');
  assert.equal(url.pathname, '/a/stock/000001');
  assert.equal(url.searchParams.get('returnTo'), returnTo);
  assert.equal(url.searchParams.get('trade_date'), '2026-10-01');
});
test('未指定历史日期时不向个股入口传递日期', async () => {
  const { researchStockHref } = await import('../src/lib/researchUtils.ts');
  const url = new URL(researchStockHref('600519'), 'http://localhost');
  assert.equal(url.searchParams.has('trade_date'), false);
  assert.equal(url.searchParams.get('returnTo'), '/a/research?symbol=600519');
});

test('研报入库时间统一显示北京时间并省略秒和微秒', async () => {
  const { formatResearchTime } = await import('../src/lib/researchUtils.ts');
  assert.equal(formatResearchTime('2026-10-21T01:20:34.764887+00:00'), '2026-10-21 09:20');
  assert.equal(formatResearchTime('2026-10-21T23:20:00Z'), '2026-10-22 07:20');
});
test('研报入库时间缺失或非法时安全回退', async () => {
  const { formatResearchTime } = await import('../src/lib/researchUtils.ts');
  for (const value of [null, undefined, '', 'not-a-date']) assert.equal(formatResearchTime(value), '未提供');
});

test('同步失败不能被旧成功记录掩盖', async () => {
  const { researchSyncMessage } = await import('../src/lib/researchUtils.ts');
  assert.equal(researchSyncMessage({ status: 'failed', inserted: 0, updated: 0, error: '网络失败' }), '最近一次更新失败：网络失败；保留已有研报，请查看最近成功时间。');
});
test('同步成功显示增量计数，部分完成明确标记', async () => {
  const { researchSyncMessage } = await import('../src/lib/researchUtils.ts');
  assert.equal(researchSyncMessage({ status: 'success', inserted: 12, updated: 3, error: null }), '最近一次更新成功：新增 12 篇，更新 3 篇。');
  assert.match(researchSyncMessage({ status: 'partial', inserted: 2, updated: 0, error: null }), /部分完成.*不能视为完整更新/);
});
test('首次更新、运行中及未知状态均不误报成功', async () => {
  const { researchSyncMessage } = await import('../src/lib/researchUtils.ts');
  assert.match(researchSyncMessage(undefined), /状态暂不可用/);
  assert.match(researchSyncMessage({ status: 'never' }), /尚无自动更新记录/);
  assert.match(researchSyncMessage({ status: 'running' }), /正在更新/);
});

test('最新行情日默认展示最新研报，周末发布不会被行情日截断', async () => {
  const { resolveResearchCutoff } = await import('../src/lib/researchUtils.ts');
  assert.equal(resolveResearchCutoff('2026-10-23', '2026-10-23'), undefined);
  assert.equal(resolveResearchCutoff('2026-10-25', '2026-10-23'), undefined);
});
test('历史日期默认严格截止，最新行情日未知时保守截止', async () => {
  const { resolveResearchCutoff } = await import('../src/lib/researchUtils.ts');
  assert.equal(resolveResearchCutoff('2026-10-22', '2026-10-23'), '2026-10-22');
  assert.equal(resolveResearchCutoff('2026-10-23', undefined), '2026-10-23');
});
test('显式截止入口保留最新行情日截止，用户切换优先于默认模式', async () => {
  const { resolveResearchCutoff } = await import('../src/lib/researchUtils.ts');
  assert.equal(resolveResearchCutoff('2026-10-23', '2026-10-23', 'auto', true), '2026-10-23');
  assert.equal(resolveResearchCutoff('2026-10-23', '2026-10-23', 'asof'), '2026-10-23');
  assert.equal(resolveResearchCutoff('2026-10-22', '2026-10-23', 'latest', true), undefined);
  assert.equal(resolveResearchCutoff(undefined, '2026-10-23', 'asof'), undefined);
});
