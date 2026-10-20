import assert from 'node:assert/strict';
import test from 'node:test';
import { buildChanZoneEdges } from '../src/components/stock/stockChartChanZoneGeometry.ts';
const bounds = { x: 10, y: 10, width: 100, height: 100 };
const edge = (x1, y1, x2, y2, dotted = false) => ({ x1, y1, x2, y2, dotted });
test('左右跨界只保留真实竖边，不生成裁剪边框', () => {
  assert.deepEqual(buildChanZoneEdges([0, 20], [50, 80], bounds, true, false),
    [edge(10, 20, 50, 20, true), edge(10, 80, 50, 80), edge(50, 20, 50, 80)]);
  assert.deepEqual(buildChanZoneEdges([50, 20], [120, 80], bounds, false, true),
    [edge(50, 20, 110, 20), edge(50, 80, 110, 80, true), edge(50, 20, 50, 80)]);
});
test('双侧跨界保留上下横边，纵向跨界仅保留真实竖边', () => {
  assert.deepEqual(buildChanZoneEdges([0, 20], [120, 80], bounds, false, false),
    [edge(10, 20, 110, 20), edge(10, 80, 110, 80)]);
  assert.deepEqual(buildChanZoneEdges([20, 0], [80, 120], bounds, false, false),
    [edge(20, 10, 20, 110), edge(80, 10, 80, 110)]);
});
test('退化框保留非零线，重合横边实线优先；反转输入不改变虚实', () => {
  assert.deepEqual(buildChanZoneEdges([20, 30], [80, 30], bounds, true, false),
    [edge(20, 30, 80, 30)]);
  assert.deepEqual(buildChanZoneEdges([40, 20], [40, 80], bounds, true, false),
    [edge(40, 20, 40, 80)]);
  assert.deepEqual(buildChanZoneEdges([80, 80], [20, 20], bounds, true, false),
    buildChanZoneEdges([20, 20], [80, 80], bounds, true, false));
  assert.deepEqual(buildChanZoneEdges([20, 20], [20, 20], bounds, false, false), []);
});
test('完全在外或仅包围窗口但四边均在外不画伪造边', () => {
  assert.deepEqual(buildChanZoneEdges([0, 20], [5, 80], bounds, false, false), []);
  assert.deepEqual(buildChanZoneEdges([0, 0], [120, 120], bounds, false, false), []);
});
test('非法坐标和无效裁剪区拒绝，所有边有界', () => {
  for (const bad of [NaN, Infinity, -Infinity]) {
    assert.deepEqual(buildChanZoneEdges([bad, 20], [80, 80], bounds, false, false), []);
    assert.deepEqual(buildChanZoneEdges([20, 20], [80, 80], { ...bounds, width: bad }, false, false), []);
  }
  assert.deepEqual(buildChanZoneEdges([20, 20], [80, 80], { ...bounds, width: 0 }, false, false), []);
  assert.deepEqual(buildChanZoneEdges([20, 20], [80, 80], { ...bounds, height: -1 }, false, false), []);
  const result = buildChanZoneEdges([20, 20], [80, 80], bounds, true, true);
  assert.equal(result.length, 4);
  assert.ok(result.every(e => [e.x1, e.x2].every(x => x >= 10 && x <= 110)
    && [e.y1, e.y2].every(y => y >= 10 && y <= 110)));
});
