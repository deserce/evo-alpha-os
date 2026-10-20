import assert from 'node:assert/strict';
import test from 'node:test';
import { buildStockTimeAxisLayout, formatStockTimeTick } from '../src/components/stock/stockChartTimeAxis.ts';

test('分钟刻度为单行，精确时间仍保留', () => {
  assert.equal(formatStockTimeTick('2026-09-30 11:30:00', '120min'), '09-30 11:30');
  assert.equal(formatStockTimeTick('2026-09-30T10:00:00', '30min'), '09-30 10:00');
  assert.equal(formatStockTimeTick('2026-09-30', 'daily'), '2026-09-30');
});
test('不同高度为日期与缩放条保留固定像素间隔', () => {
  for (const height of [360, 600, 900]) {
    const layout = buildStockTimeAxisLayout(height);
    assert.equal(Math.round(layout.bottomPercent * height / 100), 56);
    assert.equal(layout.slider.bottom, 6);
    assert.equal(layout.slider.height, 12);
    assert.equal(layout.slider.showDetail, false);
  }
});
test('无效尺寸和时间安全处理，标签自动避让', () => {
  for (const value of [0, -1, NaN, Infinity]) assert.equal(buildStockTimeAxisLayout(value).bottomPercent, 56 / 600 * 100);
  assert.equal(formatStockTimeTick(null, '30min'), '');
  assert.equal(formatStockTimeTick('bad-time', '30min'), 'bad-time');
  const { axisLabel } = buildStockTimeAxisLayout(600);
  assert.equal(axisLabel.interval, 'auto');
  assert.equal(axisLabel.hideOverlap, true);
  assert.equal(axisLabel.showMinLabel, false);
  assert.equal(axisLabel.showMaxLabel, false);
});
