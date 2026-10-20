import assert from 'node:assert/strict';
import test from 'node:test';

import { buildWatchChartPoints } from '../src/components/stock/stockChartWatchMarkers.ts';

const dates = ['2026-06-10', '2026-06-11', '2026-06-12'];
const candles = [
  [10, 10.2, 9.8, 10.4],
  [10.1, 10.5, 10, 10.6],
  [10.4, 10.9, 10.3, 11],
];

test('入区和确认转折点映射到对应 K 线上方', () => {
  const points = buildWatchChartPoints([
    { trade_date: '2026-06-10', marker_type: 'IN_ZONE', reason: '缩量进入回调区', factors: { setup_path: 'TREND_PULLBACK' } },
    { trade_date: '2026-06-12', marker_type: 'TRIGGERED', reason: '低点抬高且 MACD 收敛', factors: { setup_path: 'REPAIR_REVERSAL' } },
  ], dates, candles);

  assert.deepEqual(points.map((point) => [point.date, point.markerType, point.pathCode, point.label]), [
    ['2026-06-10', 'IN_ZONE', 'A', 'A观察'],
    ['2026-06-12', 'TRIGGERED', 'B', 'B确认'],
  ]);
  assert.equal(points[0].value[1], 10.66);
  assert.equal(points[1].value[1], 11.66);
});

test('忽略冷却重复行、非目标状态和图表区间外的点', () => {
  const points = buildWatchChartPoints([
    { trade_date: '2026-06-11', marker_type: 'TRIGGERED' },
    { trade_date: '2026-06-11', marker_type: 'TRIGGERED' },
    { trade_date: '2026-06-11', marker_type: 'WATCHING' },
    { trade_date: '2026-06-20', marker_type: 'IN_ZONE' },
  ], dates, candles);

  assert.equal(points.length, 1);
  assert.equal(points[0].key, '2026-06-11:TRIGGERED');
});

test('缺少有效价格时安全忽略标记', () => {
  const points = buildWatchChartPoints([
    { trade_date: '2026-06-10', marker_type: 'IN_ZONE', current_price: null },
  ], ['2026-06-10'], [[null, null, null, null]]);

  assert.deepEqual(points, []);
});

test('失效转折显示独立标签并保留正式来源，不冒充卖出', () => {
  const points = buildWatchChartPoints([
    { trade_date: '2026-06-11', marker_type: 'INVALIDATED', source: 'daily_snapshot', rule_version: 25, reason: '跌破失效价', factors: { setup_path: 'REPAIR_REVERSAL' } },
  ], dates, candles);
  assert.equal(points.length, 1);
  assert.equal(points[0].label, 'B失效');
  assert.equal(points[0].source, 'daily_snapshot');
  assert.equal(points[0].ruleVersion, 25);
});

test('K线最高价缺失时使用有效现价，不将null当作零', () => {
  const points = buildWatchChartPoints([
    { trade_date: dates[0], marker_type: 'TRIGGERED', current_price: 10 },
  ], [dates[0]], [[null, null, null, null]]);
  assert.equal(points.length, 1);
  assert.equal(points[0].value[1], 10.6);
});

test('分钟标记保留同日多个完成柱时间', () => {
  const times = ['2026-09-30 10:00:00', '2026-09-30 10:30:00'];
  const points = buildWatchChartPoints(times.map((trade_date) => ({ trade_date, marker_type: 'TRIGGERED' })), times, candles);
  assert.deepEqual(points.map((point) => point.date), times);
});

test('日线仍兼容数据库带时间的标记', () => {
  const points = buildWatchChartPoints([{ trade_date: '2026-06-10 00:00:00', marker_type: 'IN_ZONE' }], dates, candles);
  assert.equal(points[0].date, dates[0]);
});

test('分钟图不将缺少时间或其他周期的标记放入当前柱', () => {
  const points = buildWatchChartPoints([
    { trade_date: '2026-09-30', marker_type: 'TRIGGERED' },
    { trade_date: '2026-09-30 11:30:00', marker_type: 'IN_ZONE' },
  ], ['2026-09-30 10:00:00'], candles);
  assert.deepEqual(points, []);
});
