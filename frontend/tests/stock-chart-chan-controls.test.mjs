import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
const source = readFileSync(new URL('../src/components/stock/StockChart.tsx', import.meta.url), 'utf8');

test('按用户截图默认开启雪球缠论与两项扩展', () => {
  assert.match(source, /showChanOverlay, setShowChanOverlay\] = useState\(true\)/);
  assert.match(source, /useState<'zhihu' \| 'xueqiu'>\('xueqiu'\)/);
  for (const name of ['showChanHistory', 'showChanPivotLabels']) {
    assert.match(source, new RegExp(`${name}, .*\\] = useState\\(true\\)`));
    assert.match(source, new RegExp(`aria-pressed=\\{${name}\\}`));
  }
  assert.match(source, /买卖文字为知乎公式扩展/);
});

test('中枢与两版撤销审计按需回放，切换后旧结果隐藏，失败和超时释放worker', () => {
  assert.match(source, /includeZones = chanVariant === 'xueqiu' && showChanHistory/);
  assert.match(source, /auditSignals = showChanRevocations/);
  assert.match(source, /!showChanOverlay \|\| \(!includeZones && !auditSignals\)/);
  assert.match(source, /!chanOverlay\?\.barCount \|\| dates.length !== candles.length/);
  assert.match(source, /chanHistoryData === data && chanHistoryResultKey === chanHistoryKey/);
  assert.match(source, /worker\?\.terminate\(\)/);
  assert.match(source, /return \(\) => \{ active = false; stop\(\); \}/);
  assert.match(source, /历史回放超时/);
  assert.match(source, /}, 10000\)/);
  assert.match(source, /worker.onerror/);
  assert.match(source, /zone.endIndex !== dates.length - 1/);
});

test('原式样式使用独立线宽和真实框边，知乎文字不附加三角', () => {
  assert.match(source, /z: historical \? 10 : 11/);
  assert.match(source, /width: line.width, color: line.color, type: line.lineType/);
  assert.match(source, /buildChanZoneEdges/);
  assert.match(source, /symbol: 'emptyCircle', symbolSize: 5/);
  assert.match(source, /itemStyle: \{ color: '#ff80ff' \}/);
  assert.match(source, /symbolSize: chanVariant === 'zhihu' \? 0 : 10/);
  assert.match(source, /原式MA20已隐藏/);
});

test('撤销信号用空心圆，不保留买卖文字，悬停显示时间和原因', () => {
  assert.match(source, /name: '缠论撤销记录'/);
  assert.match(source, /symbol: 'circle', symbolSize: 11/);
  assert.match(source, /color: 'transparent', borderColor: '#94a3b8'/);
  assert.match(source, /z: 25, animation: false/);
  assert.match(source, /label: \{ show: false \}/);
  for (const text of ['回放首次可见', '回放撤销', '原标记价位', '非确认后止损失效']) assert(source.includes(text));
});
