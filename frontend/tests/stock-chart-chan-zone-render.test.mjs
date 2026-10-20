import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import * as echarts from 'echarts';

function renderZones(zones, filterMode) {
  const chart = echarts.init(null, null, {
    renderer: 'svg', ssr: true, width: 500, height: 300,
  });
  const rendered = [];
  try {
    chart.setOption({
      animation: false,
      xAxis: { type: 'category', data: Array.from({ length: 100 }, (_, i) => String(i)) },
      yAxis: { min: 0, max: 20 },
      dataZoom: [{ type: 'inside', start: 45, end: 80,
        ...(filterMode ? { filterMode } : {}) }],
      series: [{ type: 'line', data: Array(100).fill(10) }, {
        type: 'custom', data: zones, encode: { x: [0, 1], y: [2, 3] },
        renderItem(params, api) {
          rendered.push({ index: params.dataIndex, start: api.value(0), end: api.value(1) });
          const topLeft = api.coord([api.value(0), api.value(3)]);
          const bottomRight = api.coord([api.value(1), api.value(2)]);
          return { type: 'rect', shape: {
            x: topLeft[0], y: topLeft[1],
            width: bottomRight[0] - topLeft[0], height: bottomRight[1] - topLeft[1],
          }, style: { fill: 'none', stroke: '#ff00ff' } };
        },
      }],
    });
    const svg = chart.renderToSVGString();
    return { rendered, svg };
  } finally {
    chart.dispose();
  }
}

test('真实ECharts默认过滤会丢掉跨入视窗的中枢，不调用renderItem', () => {
  const result = renderZones([[10, 70, 5, 15]]);
  assert.deepEqual(result.rendered, []);
  assert.equal(result.svg.includes('#ff00ff'), false);
});

test('weakFilter保留跨左边、右边和同时跨两边的中枢', () => {
  for (const [start, end] of [[10, 70], [60, 90], [10, 90]]) {
    const result = renderZones([[start, end, 5, 15]], 'weakFilter');
    assert.deepEqual(result.rendered, [{ index: 0, start, end }]);
    assert.equal(result.svg.includes('#ff00ff'), true);
  }
});

test('weakFilter仍过滤完全在视窗外的中枢，保留原始dataIndex', () => {
  const result = renderZones([
    [0, 20, 5, 15], [10, 70, 5, 15], [85, 99, 5, 15], [60, 75, 5, 15],
  ], 'weakFilter');
  assert.deepEqual(result.rendered, [
    { index: 1, start: 10, end: 70 }, { index: 3, start: 60, end: 75 },
  ]);
});

test('StockChart鼠标和滑块缩放都保留跨窗中枢', () => {
  const source = readFileSync(new URL('../src/components/stock/StockChart.tsx', import.meta.url), 'utf8');
  const zoom = source.slice(source.indexOf('dataZoom: ['), source.indexOf('      grid,', source.indexOf('dataZoom: [')));
  assert.match(zoom, /type: 'inside',[\s\S]*?filterMode: 'weakFilter'/);
  assert.match(zoom, /type: 'slider',[\s\S]*?filterMode: 'weakFilter'/);
  assert.equal((zoom.match(/filterMode: 'weakFilter'/g) ?? []).length, 2);
});
