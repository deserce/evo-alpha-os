import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const strategySource = readFileSync(
  new URL('../src/app/a/strategy/page.tsx', import.meta.url),
  'utf8',
);
const performanceChartSource = readFileSync(
  new URL('../src/components/stock/StockPerformanceChart.tsx', import.meta.url),
  'utf8',
);

test('策略内嵌详情为当前股票提供可发现的个股详情入口', () => {
  assert.match(strategySource, /import Link from 'next\/link'/);
  assert.match(strategySource, /href=\{`\/a\/stock\/\$\{symbol\}`\}/);
  assert.match(strategySource, /aria-label=\{`打开 \$\{displayName\} 个股详情`\}/);
  assert.match(strategySource, />\s*个股详情\s*</);
});

test('策略页 340px 资料栏启用紧凑业绩布局', () => {
  assert.match(
    strategySource,
    /<StockEnrichmentPanel[\s\S]*?symbol=\{symbol\}[\s\S]*?compact[\s\S]*?className="min-h-\[420px\]"/,
  );
});

test('业绩图高度随视口收敛且不再使用会裁切底部的 380px 固定高度', () => {
  assert.match(performanceChartSource, /h-\[clamp\(270px,30vh,310px\)\]/);
  assert.match(performanceChartSource, /h-\[clamp\(290px,32vh,340px\)\]/);
  assert.doesNotMatch(performanceChartSource, /h-\[380px\]/);
});

 test('策略页由容器分配剩余高度，工具栏不参与收缩', () => {
  assert.match(strategySource, /lg:h-\[calc\(100dvh-var\(--evo-nav-height,64px\)-12px\)\]/);
  assert.match(strategySource, /evo-panel mb-1.5 shrink-0/);
  assert.doesNotMatch(strategySource, /lg:h-\[calc\(100dvh-var\(--evo-nav-height,64px\)-48px\)\]/);
});

test('左侧默认宽度容纳代码名称行业股价涨跌，固定列偏移与列宽一致', () => {
  assert.match(strategySource, /lg:min-w-\[432px\]/);
  assert.match(strategySource, /w-\[732px\] table-fixed/);
  assert.match(strategySource, /stickyLeft: 'left-\[68px\]'/);
  assert.match(strategySource, /stickyLeft: 'left-\[160px\]'/);
});
