import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CHART_PERIODS, chartPeriodLabel, formatChartAxisTime, isMinutePeriod,
} from '../src/lib/chartPeriods.ts';
import { buildStockChartTooltip } from '../src/components/stock/stockChartTooltip.ts';

test('五个周期互不混淆，只有30与120分钟属于分钟行情', () => {
  assert.deepEqual(CHART_PERIODS.map((item) => item.key),
    ['daily', 'weekly', 'monthly', '30min', '120min']);
  for (const item of CHART_PERIODS) {
    assert.equal(chartPeriodLabel(item.key), item.label);
    assert.equal(isMinutePeriod(item.key), ['30min', '120min'].includes(item.key));
  }
});

test('跨日分钟轴保留日期和时分，日线显示格式保持不变', () => {
  assert.equal(formatChartAxisTime('2026-10-01 10:00:00', '30min'), '10-01\n10:00');
  assert.equal(formatChartAxisTime('2026-10-02T11:30:00', '120min'), '10-02\n11:30');
  assert.equal(formatChartAxisTime('2026-10-02', 'daily'), '2026-10-02');
  assert.equal(formatChartAxisTime(null, '30min'), '');
  assert.equal(formatChartAxisTime('bad-time', '120min'), 'bad-time');
});

test('分钟tooltip即使误传日RPS也不展示，保留完整时刻和真实分钟涨跌', () => {
  const html = buildStockChartTooltip(1, {
    dates: ['2026-10-01 10:00', '2026-10-01 10:30'],
    candles: [[10, 10, 9.8, 10.2], [10, 10.5, 10, 10.7]],
    showRps: false,
    rpsSeries: [{ label: 'RPS20', values: [97, 98] }],
  });
  assert.match(html, /2026-10-01 10:30/);
  assert.match(html, /\+5\.00%/);
  assert.doesNotMatch(html, /RPS|97|98/);
});

test('默认日线tooltip保留RPS，非法索引安全返回空', () => {
  const input = { dates: ['2026-10-01'], candles: [[10, 10, 9, 11]],
    rpsSeries: [{ label: 'RPS20', values: [98] }] };
  assert.match(buildStockChartTooltip(0, input), /RPS20 98\.0/);
  assert.equal(buildStockChartTooltip(3, input), '');
});
