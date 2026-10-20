import assert from 'node:assert/strict';
import test from 'node:test';
import { buildHintPriceOverlay } from '../src/components/stock/stockChartHintPrices.ts';
const date = '2026-09-30';
const signal = { symbol: '002841', name: '视源股份', source: 'C', family: 'C', label: '买入确认', direction: 'buy', confirmed: true, confirmedDate: date, triggerPrice: 45.18, stopPrice: 43.27 };
const build = (signals, overrides = {}) => buildHintPriceOverlay({ date, items: [{ symbol: signal.symbol, signals }] }, overrides.symbol ?? signal.symbol, overrides.date ?? date, overrides.period ?? 'daily', [date]);
test('确认点和来源价位合并', () => {
 const r = build([signal, { ...signal, source: '低吸 B', family: 'watch' }]);
 assert.equal(r.lines.length, 2); assert.match(r.lines[0].label, /C 信号价 \/ 低吸 B 触发价/);
 assert.equal(r.points.length, 1); assert.equal(r.points[0].date, date); assert.equal(r.points[0].price, 45.18);
});
test('候选不确认，换股日期周期清空', () => {
 const r = build([{ ...signal, family: 'chan' }]); assert.equal(r.points.length, 0); assert.match(r.lines[0].label, /候选拐点/);
 assert.deepEqual(build([{ ...signal, confirmedDate: '2026-10-01' }]), { lines: [], points: [] });
 for (const o of [{ symbol: '000001' }, { date: '2026-09-29' }, { period: 'weekly' }]) assert.deepEqual(build([signal], o), { lines: [], points: [] });
});
test('阻断、非法与缺失清空且有界', () => {
 for (const s of [{ ...signal, direction: 'blocked' }, { ...signal, triggerPrice: NaN, stopPrice: Infinity }]) assert.deepEqual(build([s]), { lines: [], points: [] });
 assert.deepEqual(buildHintPriceOverlay(undefined, signal.symbol, date, 'daily', [date]), { lines: [], points: [] });
 const r = build(Array.from({ length: 100 }, (_, i) => ({ ...signal, triggerPrice: i + 10, stopPrice: i + 100 })));
 assert.equal(r.lines.length, 12); assert.ok(r.points.length <= 8);
});
