import assert from 'node:assert/strict';
import test from 'node:test';
import { buildStructurePoints, signalTooltip, isSignalVisible, DEFAULT_STRATEGY_VISIBILITY } from '../src/components/stock/structureCandidatePresentation.ts';

test('默认只显示C的观察、买点、卖点及风险', () => {
  const visibility = DEFAULT_STRATEGY_VISIBILITY;
  assert.equal(isSignalVisible(visibility, 'A', 'TRIGGERED'), false);
  assert.equal(isSignalVisible(visibility, 'B', 'INVALIDATED'), false);
  for (const type of ['OBSERVE', 'BUY_CONFIRM', 'RISK_WARNING', 'EXIT', 'INVALIDATED']) {
    assert.equal(isSignalVisible(visibility, 'C', type), true);
  }
});

test('候选买入和模拟退出在对应K线上分开标注', () => {
  const rows = buildStructurePoints([
    { trade_date: '2026-09-01', marker_type: 'BUY_CONFIRM', price: 10, reason: '回踩确认' },
    { trade_date: '2026-09-02', marker_type: 'EXIT', price: 11, reason: '支撑失守' },
  ], ['2026-09-01', '2026-09-02'], [[10, 10, 9, 11], [11, 11, 10, 12]]);
  assert.equal(rows[0].label, 'C买确认');
  assert.ok(rows[0].value[1] < 9);
  assert.equal(rows[1].label, 'C卖出');
  assert.ok(rows[1].value[1] > 12);
});

test('重复、区间外和无效价格不产生标记', () => {
  const marker = { trade_date: '2026-09-01', marker_type: 'OBSERVE', price: 10, reason: '' };
  assert.equal(buildStructurePoints([marker, marker, {...marker, price: NaN}, {...marker, trade_date:'2026-09-02'}], ['2026-09-01'], []).length, 1);
});

test('信号解释和来源均转义HTML', () => {
  const html = signalTooltip([{label:'<b>',reason:'<img src=x onerror=alert(1)>',source:'<script>'}]);
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;b&gt;'));
});


test('A/B/C策略开关相互独立', () => {
  const v = { A: false, B: true, C: true, observe: true, buy: true, sell: true };
  assert.equal(isSignalVisible(v, 'A', 'TRIGGERED'), false);
  assert.equal(isSignalVisible(v, 'B', 'TRIGGERED'), true);
  assert.equal(isSignalVisible(v, 'C', 'BUY_CONFIRM'), true);
});

test('买点、卖点失效、观察分别过滤，不混淆失效与买入', () => {
  const v = { A: true, B: true, C: true, observe: false, buy: true, sell: false };
  assert.equal(isSignalVisible(v, 'C', 'BUY_CONFIRM'), true);
  for (const type of ['OBSERVE','IN_ZONE','EXIT','INVALIDATED','RISK_WARNING']) {
    assert.equal(isSignalVisible(v, 'C', type), false);
  }
});

test('关闭全部策略时不显示旧版未分路径或未知信号', () => {
  const v = { A: false, B: false, C: false, observe: true, buy: true, sell: true };
  assert.equal(isSignalVisible(v, '-', 'TRIGGERED'), false);
  assert.equal(isSignalVisible({...v,A:true}, 'A', 'UNEXPECTED'), false);
});

test('C分钟买卖信号精确映射各自完成柱', () => {
  const dates = ['2026-09-30 10:00:00', '2026-09-30 11:30:00'];
  const markers = dates.map((trade_date, index) => ({ trade_date, marker_type: index ? 'EXIT' : 'BUY_CONFIRM', price: 10, reason: '测试' }));
  const points = buildStructurePoints(markers, dates, [[10,10,9,11],[10,10,9,11]]);
  assert.deepEqual(points.map((point) => point.value[0]), dates);
});
