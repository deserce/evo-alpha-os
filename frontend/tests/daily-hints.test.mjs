import assert from 'node:assert/strict';
import test from 'node:test';
import { aggregateDailyHints, DAILY_HINT_LIMITS } from '../src/lib/dailyHints.ts';

const signal = (symbol, direction, source = 'A/B') => ({
  symbol, name: `股票${symbol}`, direction, source, label: '策略提示',
});

test('同股多来源去重，保留来源及原始事件，等待独立计数', () => {
  const result = aggregateDailyHints([
    { ...signal('600001', 'buy'), eventId: 'watch-1', stopPrice: 10 },
    signal('600001', 'buy', 'C'), signal('600002', 'sell', '缠论'),
    signal('600003', 'wait'),
  ]);
  assert.deepEqual(result.counts, { buy: 1, sell: 1, risk: 0, wait: 1, blocked: 0, total: 3 });
  assert.deepEqual(result.items.find(x=>x.symbol==='600001').sources, ['A/B', 'C']);
  assert.equal(result.items.find(x=>x.symbol==='600001').signals.length, 2);
  assert.equal(result.items.find(x=>x.symbol==='600001').signals[0].eventId, 'watch-1');
  assert.equal(result.items.find(x=>x.symbol==='600001').signals[0].stopPrice, 10);
  assert.match(result.summary, /等待确认 1/);
});

test('冲突归风险，数据阻断优先，风险不显示成买入', () => {
  const result = aggregateDailyHints([
    signal('600001', 'buy'), signal('600001', 'sell', 'C'),
    signal('600002', 'buy'), signal('600002', 'blocked'),
    signal('600003', 'buy'), signal('600003', 'risk'),
  ]);
  assert.deepEqual(result.counts, { buy: 0, sell: 0, risk: 2, wait: 0, blocked: 1, total: 3 });
  assert.equal(result.items.find(x=>x.symbol==='600001').conflict, true);
  assert.match(result.items.find(x=>x.symbol==='600001').summary, /冲突/);
  assert.match(result.items.find(x=>x.symbol==='600002').summary, /暂不据此行动/);
});

test('非法外部输入安全拒绝，缺少名称回退代码，无效价格不透传', () => {
  for (const input of [null, undefined, {}, 'signals']) {
    const result = aggregateDailyHints(input);
    assert.equal(result.invalidCount, 1);
    assert.equal(result.counts.total, 0);
    assert.match(result.summary, /格式异常/);
  }
  const result = aggregateDailyHints([
    null, signal('../x', 'buy'), signal('600001', 'wrong'),
    { ...signal('600002', 'wait'), source: '' },
    { ...signal('600003', 'buy'), name: null, stopPrice: Infinity },
  ]);
  assert.equal(result.invalidCount, 4);
  assert.equal(result.items[0].name, '600003');
  assert.equal(result.items[0].signals[0].stopPrice, undefined);
  assert.equal(aggregateDailyHints([]).summary, '当前范围暂无提示');
});

test('股票与输入数量有界，超出原始信号显示上限仍检查冲突', () => {
  const large = Array.from({ length: 5000 }, (_, i) => signal(String(600000 + i), 'buy'));
  const result = aggregateDailyHints(large);
  assert.equal(result.counts.total, DAILY_HINT_LIMITS.stocks);
  assert.equal(result.truncated, true);
  const repeated = Array.from({ length: 40 }, () => signal('600001', 'buy'));
  const conflict = aggregateDailyHints([...repeated, signal('600001', 'sell')]);
  assert.equal(conflict.items[0].signals.length, DAILY_HINT_LIMITS.signalsPerStock);
  assert.equal(conflict.items[0].direction, 'risk');
  assert.equal(conflict.truncated, true);
});

test('聚合不修改输入对象，外部文本有界且事件 ID 保留', () => {
  const row = Object.freeze({ ...signal('600001', 'buy'), name: '名'.repeat(100),
    reason: '因'.repeat(1000), eventId: 'id-1', stopPrice: -1 });
  const result = aggregateDailyHints(Object.freeze([row]));
  assert.equal(result.items[0].name.length, 60);
  assert.equal(result.items[0].signals[0].reason.length, 500);
  assert.equal(result.items[0].signals[0].stopPrice, undefined);
  assert.equal(row.name.length, 100);
});

const confirmed = (symbol, family, direction='buy', date='2026-09-30') => ({
  ...signal(symbol, direction, family), family, confirmed:true, confirmedDate:date,
});
test('同日已确认C与缠论共振优先，AI与观察条件不算共振', () => {
  const r=aggregateDailyHints([signal('600001','buy'), confirmed('600002','C'),
    confirmed('600002','chan'), confirmed('600003','C'),
    {...confirmed('600003','chan'),confirmed:false}]);
  assert.equal(r.items[0].symbol,'600002');
  assert.equal(r.items[0].resonance,true);
  assert.equal(r.items.find(x=>x.symbol==='600003').resonance,false);
});
test('跨日期不共振，买点在其他机会之前，冲突归风险', () => {
  const r=aggregateDailyHints([confirmed('600001','C'),confirmed('600001','chan','buy','2026-09-29'),
    confirmed('600002','C','sell'),confirmed('600002','chan','sell'),
    confirmed('600003','C'),confirmed('600003','chan','sell')]);
  assert.deepEqual(r.items.map(x=>x.symbol),['600001','600002','600003']);
  assert.equal(r.items[0].resonance,false);
  assert.equal(r.items[1].resonance,true);
  assert.equal(r.items[2].resonance,false);
});
test('价格周期和确认输入有界，不将缺失确认猜成已确认', () => {
  const s=aggregateDailyHints([{...signal('600001','buy'),currentPrice:12,triggerPrice:NaN,
    zoneLow:-1,period:'x'.repeat(80),confirmed:'true',confirmedDate:'bad'}]).items[0].signals[0];
  assert.equal(s.currentPrice,12); assert.equal(s.triggerPrice,undefined);
  assert.equal(s.zoneLow,undefined); assert.equal(s.period.length,30);
  assert.equal(s.confirmed,false); assert.equal(s.confirmedDate,undefined);
});

test('缠论候选保留买卖方向标签但不制造确认冲突', () => {
  const r=aggregateDailyHints([confirmed('600001','C'),
    {...signal('600001','wait','缠论'),family:'chan',candidateDirection:'sell',confirmed:false}]);
  assert.equal(r.items[0].direction,'buy'); assert.equal(r.items[0].conflict,false);
  assert.equal(r.items[0].resonance,false);
  assert.equal(r.items[0].signals[1].candidateDirection,'sell');
});

test('买点按C加缠候选、C、单缠、低吸降序且不伪造共振', () => {
 const r=aggregateDailyHints([
 {...signal('600001','buy'),family:'watch'},
 {...signal('600002','buy'),family:'chan'},
 confirmed('600003','C'),confirmed('600004','C'),
 {...signal('600004','wait','缠'),family:'chan',candidateDirection:'buy'}]);
 assert.deepEqual(r.items.map(x=>x.symbol),['600004','600003','600002','600001']);
 assert.equal(r.items[0].resonance,false);
});
