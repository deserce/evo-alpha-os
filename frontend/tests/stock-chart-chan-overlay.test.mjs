import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildChanOverlay, buildChanBuySellPoints, CHAN_MAX_BARS, CHAN_MAX_INPUT_BARS, CHAN_OVERLAY_NOTICE,
  tdxBackset, tdxDrawline, tdxRef, tdxZig, tdxCount, calculateChanCurrentZones,
} from '../src/components/stock/stockChartChanOverlay.ts';

const datesFor = n => Array.from({ length: n }, (_, i) =>
  new Date(Date.UTC(2024, 0, 1 + i)).toISOString().slice(0, 10));
const wave = n => datesFor(n).map((_, i) => {
  const c = 100 + Math.sin(i * Math.PI / 9) * 15 + i * 0.03;
  return [c - 0.2, c, c - 1, c + 1];
});
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9);

test('买卖扩展沿用知乎极点文字与原ZIG条件，雪球线框不改变', () => {
  const dates = datesFor(120), candles = wave(120);
  const strict = buildChanOverlay(dates, candles, 'xueqiu');
  const points = buildChanBuySellPoints(dates, candles);
  assert.deepEqual(points, buildChanOverlay(dates, candles, 'zhihu').points);
  assert.equal(points.find(p => p.kind === 'pivot' && p.direction === 'low').label, '买');
  assert.equal(points.find(p => p.kind === 'pivot' && p.direction === 'high').label, '卖');
  assert.deepEqual(points.filter(p => p.kind === 'sell').map(p => [p.index, p.label]),
    [6, 24, 42, 60, 78, 96, 114].map(index => [index, '[1 卖]']));
  assert.deepEqual(buildChanOverlay(dates, candles, 'xueqiu'), strict);
});
test('买卖扩展短历史和无波动时不虚造标签', () => {
  assert.deepEqual(buildChanBuySellPoints(datesFor(3), [[10, 10, 10, 10], [10, 10, 10, 10], [10, 10, 10, 10]]), []);
});
test('买卖扩展错误行情安全拒绝', () => {
  assert.deepEqual(buildChanBuySellPoints(datesFor(1), [[10, null, 9, 11]]), []);
  assert.deepEqual(buildChanBuySellPoints(datesFor(2), []), []);
});

test('BACKSET 手算：当前和前三根回填，后续行情改变历史结果', () => {
  assert.deepEqual(tdxBackset([0, 0, 0, 0, 1, 0], 4), [0, 1, 1, 1, 1, 0]);
  assert.deepEqual(tdxBackset([0, 0, 0], 4), [0, 0, 0]);
  assert.deepEqual(tdxBackset([0, 0, 0, 1], 4).slice(0, 3), [1, 1, 1]);
  assert.deepEqual(tdxBackset([0, NaN, 0], 2), [0, 0, 0]);
  assert.deepEqual(tdxBackset([1], -1), []);
});

test('DRAWLINE 手算：终点出现后才插值，结束后不向右延长', () => {
  const result = tdxDrawline([0, 1, 0, 0, 0], [0, 10, 0, 0, 0],
    [0, 0, 0, 1, 0], [0, 0, 0, 16, 0]);
  assert.ok(Number.isNaN(result[0]));
  assert.deepEqual(result.slice(1, 4), [10, 13, 16]);
  assert.ok(Number.isNaN(result[4]));
  assert.ok(tdxDrawline([1, 0], [10, 0], [0, 0], [0, 0]).every(Number.isNaN));
  assert.deepEqual(tdxDrawline([1], [], [0], [0]), []);
});

test('REF 正偏移和平滑左边界，ZIG 按百分比而非五日周期', () => {
  assert.equal(tdxRef([2, 4, 8], 2, 1), 4);
  assert.equal(tdxRef([2, 4, 8], 1, 10), 2);
  assert.ok(Number.isNaN(tdxRef([2, 4, 8], 2, -1)));
  // 112 回落至 105 超过 5%，确认波峰；100 回升至106确认波谷。
  assert.deepEqual(tdxZig([100, 106, 112, 105, 100, 106], 5),
    [100, 106, 112, 106, 100, 106]);
  assert.deepEqual(tdxZig([100, 101, 102], 5), [100, 101, 102]);
  // 109 到98的跌幅超过10%，即使相对首根100没有越过10%，亦确认109波峰。
  assert.deepEqual(tdxZig([100, 109, 98, 94], 10), [100, 109, 101.5, 94]);
  // 91 到102涨幅超过10%，对称确认91波谷。
  assert.deepEqual(tdxZig([100, 91, 102, 106], 10), [100, 91, 98.5, 106]);
  assert.ok(tdxZig([100, NaN], 5).every(Number.isNaN));
  assert.deepEqual(tdxZig([100], 0), []);
});

test('雪球保留三轮原公式极点，不输出知乎的买卖文字或MA20', () => {
  const dates = datesFor(120), candles = wave(120);
  const result = buildChanOverlay(dates, candles, 'xueqiu');
  assert.equal(result.repainting, true);
  assert.match(CHAN_OVERLAY_NOTICE, /重绘/);
  assert.deepEqual(result.points.map(point => point.index), [67, 77, 85, 95, 103, 113]);
  assert.ok(result.points.every(point => point.kind === 'pivot' && point.label === ''));
  assert.equal(result.points[0].direction, 'low');
  assert.equal(result.points[1].direction, 'high');
  assert.equal(result.lines.length, 4);
  const pen = result.lines.find(line => line.name === '缠论上行笔');
  near(pen.data[67], candles[67][2]);
  near(pen.data[77], candles[77][3]);
  near(pen.data[72], (candles[67][2] + candles[77][3]) / 2);
  assert.equal(pen.data[66], null);
});

test('知乎保留原OR卖点、买卖标签和20日变色均线，不输出赋值GET', () => {
  const dates = datesFor(120), candles = wave(120);
  const result = buildChanOverlay(dates, candles, 'zhihu');
  assert.equal(result.lines.length, 6);
  assert.ok(result.lines.filter(line => /行笔/.test(line.name)).every(line => line.dashed));
  const sells = result.points.filter(point => point.kind === 'sell');
  assert.deepEqual(sells.map(point => [point.index, point.label]),
    [6, 24, 42, 60, 78, 96, 114].map(index => [index, '[1 卖]']));
  near(sells[0].price, candles[6][3] * 1.03);
  const ma = result.lines.find(line => line.name === '缠论MA20');
  assert.equal(ma.data[18], null);
  near(ma.data[19], candles.slice(0, 20).reduce((sum, bar) => sum + bar[1], 0) / 20);
  assert.ok(!result.lines.some(line => /ZIG|GET/.test(line.name)));
});

test('仅生成最近区间，保留三角形收敛B的端点和动态下界', () => {
  const dates = datesFor(180);
  const candles = dates.map((_, i) => {
    const c = 100 + Math.sin(i * Math.PI / 9) * (25 - i * 0.1);
    return [c - 0.2, c, c - 1, c + 1];
  });
  const result = buildChanOverlay(dates, candles, 'xueqiu');
  assert.equal(result.zones.length, 1);
  const zone = result.zones[0];
  assert.equal(zone.startIndex, 148);
  assert.equal(zone.endIndex, 179);
  assert.equal(zone.color, '#22d3ee');
  near(zone.low, candles[179][1]);
  near(zone.high, candles[166][3]);
  assert.equal(zone.dashedBottom, true);
  assert.equal(zone.dashedTop, false);
});

test('超过520根仅计算尾部窗口，输出索引和数组仍与图表对齐', () => {
  const dates = datesFor(600), candles = wave(600);
  const result = buildChanOverlay(dates, candles, 'zhihu');
  const tail = buildChanOverlay(dates.slice(80), candles.slice(80), 'zhihu');
  assert.equal(result.barCount, CHAN_MAX_BARS);
  assert.equal(result.truncated, true);
  assert.deepEqual(result.points.map(point => ({ ...point, index: point.index - 80 })), tail.points);
  for (let i = 0; i < result.lines.length; i++) {
    assert.equal(result.lines[i].data.length, 600);
    assert.ok(result.lines[i].data.slice(0, 80).every(value => value === null));
    assert.deepEqual(result.lines[i].data.slice(80), tail.lines[i].data);
  }
});

test('缺失、乱序、长度错配、非法OHLC均拒绝，不把null转换成零', () => {
  for (const [dates, candles] of [
    [[], []], [['2024-01-01'], []],
    [['2024-01-01'], [[null, 10, 9, 11]]],
    [['2024-01-01'], [[10, 10, 12, 11]]],
    [['2024-01-01'], [[10, 10, 9, Infinity]]],
    [['2024-01-02', '2024-01-01'], [[10, 10, 9, 11], [10, 10, 9, 11]]],
  ]) {
    const result = buildChanOverlay(dates, candles, 'zhihu');
    assert.equal(result.barCount, 0);
    assert.deepEqual(result.lines, []);
    assert.deepEqual(result.points, []);
  }
});

test('平价停牌和短历史安全返回，无伪造极点或除零数值', () => {
  for (const size of [1, 3, 30, 520]) {
    const result = buildChanOverlay(datesFor(size), Array.from({ length: size }, () => [10, 10, 10, 10]), 'zhihu');
    assert.deepEqual(result.points, []);
    assert.deepEqual(result.zones, []);
    assert.ok(result.lines.every(line => line.data.every(value => value === null || Number.isFinite(value))));
  }
});

test('绝对输入限额拒绝超大图窗，防止输出padding放大内存', () => {
  const size = CHAN_MAX_INPUT_BARS + 1;
  const result = buildChanOverlay(datesFor(size), Array.from({ length: size }, () => [10, 10, 10, 10]), 'zhihu');
  assert.equal(result.barCount, 0);
  assert.deepEqual(result.lines, []);
  assert.deepEqual(tdxZig(Array.from({ length: CHAN_MAX_BARS + 1 }, () => 100), 5), []);
});

 test('DRAWLINE 浮点端点必须保留原价，避免 C1=C2 严格相等漏判', () => {
  const down = tdxDrawline([1, 0, 0], [0.03, 0, 0], [0, 0, 1], [0, 0, 0.01]);
  const up = tdxDrawline([0, 0, 1, 0], [0, 0, 0.01, 0], [0, 0, 0, 1], [0, 0, 0, 0.03]);
  assert.equal(down[0], 0.03);
  assert.equal(down[2], 0.01);
  assert.equal(down[2], up[2]);
 });

const currentZoneFixtures = [
  ['expandingA', 'A', 13, 15, 8, 9, 11, 9, 11, true, false, '#d946ef'],
  ['expandingA', 'A', 13, 15, 8, 9, 14, 8, 13, false, false, '#d946ef'],
  ['expandingB', 'B', 15, 13, 10, 8, 11, 11, 13, false, true, '#22c55e'],
  ['expandingB', 'B', 15, 13, 10, 8, 9, 10, 13, false, false, '#22c55e'],
  ['convergingA', 'A', 13, 15, 10, 8, 11, 10, 11, true, false, '#eab308'],
  ['convergingA', 'A', 13, 15, 10, 8, 14, 10, 13, false, false, '#eab308'],
  ['convergingB', 'B', 13, 15, 10, 8, 11, 11, 13, false, true, '#22d3ee'],
  ['convergingB', 'B', 13, 15, 10, 8, 9, 10, 13, false, false, '#22d3ee'],
];
for (const [type, order, h1, h2, l1, l2, close, low, high, dashedTop, dashedBottom, color] of currentZoneFixtures) {
  test(`当前中枢手算 ${type} C=${close} 严格保留动态边界`, () => {
    const ages = order === 'A' ? { a1: 20, a2: 40, da1: 10, da2: 30 }
      : { a1: 10, a2: 30, da1: 20, da2: 40 };
    const { zones, states } = calculateChanCurrentZones({ ...ages, h1, h2, l1, l2, close, lastIndex: 99 });
    assert.deepEqual(zones, [{ type, startIndex: 69, endIndex: 99, low, high, color, dashedTop, dashedBottom }]);
    assert.equal(states.length, 4);
    assert.equal(states.filter(state => state.conditionMet).length, 1);
    assert.deepEqual(states.find(state => state.type === type), { type, historyValid: true, conditionMet: true, rendered: true });
  });
}
test('当前中枢严格边界与非法历史不放宽，未成立也返回状态', () => {
  const base = { a1: 20, a2: 40, da1: 10, da2: 30, h1: 13, h2: 15, l1: 8, l2: 9, close: 9, lastIndex: 99 };
  for (const patch of [{}, { da2: 20 }, { l1: 9 }]) {
    const result = calculateChanCurrentZones({ ...base, ...patch });
    assert.deepEqual(result.zones, []);
    assert.ok(result.states.every(state => !state.conditionMet));
  }
  for (const patch of [{ a2: NaN }, { lastIndex: 20 }, { a1: -1 }, { offset: Infinity }]) {
    const result = calculateChanCurrentZones({ ...base, close: 11, ...patch });
    assert.deepEqual(result.zones, []);
    assert.ok(result.states.every(state => !state.historyValid));
  }
});

 test('知乎源码输出线宽、点线及原色，雪球配色保持不变', () => {
  const result = buildChanOverlay(datesFor(120), wave(120), 'zhihu');
  assert.deepEqual(result.lines.map(({color, width, lineType}) => [color, width, lineType]), [
    ['#ffffff', 1, 'dotted'], ['#ffffff', 1, 'dotted'],
    ['#ff0000', 2, 'solid'], ['#ff0000', 2, 'solid'],
    ['#ff00ff', 2, 'solid'], ['#00ff00', 2, 'solid'],
  ]);
  const candles = datesFor(180).map((_, i) => {
    const c = 100 + Math.sin(i * Math.PI / 9) * (25 - i * 0.1);
    return [c - 0.2, c, c - 1, c + 1];
  });
  assert.equal(buildChanOverlay(datesFor(180), candles, 'zhihu').zones[0].color, '#80ffff');
  assert.equal(buildChanOverlay(datesFor(180), candles, 'xueqiu').zones[0].color, '#22d3ee');
 });

test('COUNT 零窗计全历史、一窗计当前，动态窗口按当前根取宽度', () => {
  const events = [1, 0, 1, 0, 1];
  assert.equal(tdxCount(events, 4, 0), 3);
  assert.equal(tdxCount(events, 4, 1), 1);
  assert.equal(tdxCount(events, 3, 1), 0);
  assert.deepEqual([1, 1, 2, 3, 4].map((width, index) => tdxCount(events, index, width)), [1, 0, 1, 1, 2]);
  assert.ok(Number.isNaN(tdxCount(events, 4, NaN)));
  assert.ok(Number.isNaN(tdxCount(events, 4, -1)));
});

test('ZIG 数学临界：120 回落恰好10%确认108，略高于阈值不确认', () => {
  assert.deepEqual(tdxZig([100, 120, 108, 119, 130], 10), [100, 120, 108, 119, 130]);
  assert.deepEqual(tdxZig([100, 120, 108.01, 119, 130], 10), [100, 107.5, 115, 122.5, 130]);
  // These check the adopted inclusive comparison, not an exported TDX numerical fixture.
});

test('双 ZIG 手算多标签：20%上行内10%两次买卖计数，保留AND优先OR', () => {
  const close = [100,100,80,85,90,100,96,92,88,96,104,112,106,100,98,108,118,128,120,112,110,120,130,140,110];
  const points = buildChanOverlay(datesFor(close.length), close.map(c => [c,c,c-1,c+1]), 'zhihu').points;
  // p20 rises from index 2 (80) to 23 (140). p10 turns upward at 3 and 9,
  // downward at 6 and 12, so ZTJZ1/2 count 1 then 2 within BARSLAST's changing window.
  assert.deepEqual(points.filter(p => p.kind !== 'pivot').map(p => [p.index,p.label]),
    [[2,'[1 卖]'],[3,'[1 买]'],[6,'[2 卖]'],[9,'[2 买]'],[12,'[1 卖]'],[24,'[1 卖]']]);
  const down = [100,100,120,110,125,110,130,105,95,105,80,95];
  const bearish = buildChanOverlay(datesFor(down.length), down.map(c => [c,c,c-1,c+1]), 'zhihu');
  assert.deepEqual(bearish.points.filter(p => p.kind !== 'pivot').map(p => [p.index,p.label]), [[9,'[类 2 买]']]);
});
