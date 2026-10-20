import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const portfolioSource = readFileSync(
  new URL('../src/app/a/portfolio/page.tsx', import.meta.url),
  'utf8',
);

test('卖点计划区域默认折叠并可独立展开', () => {
  assert.match(
    portfolioSource,
    /const \[exitPlansOpen, setExitPlansOpen\] = useState\(false\)/,
  );
  assert.match(portfolioSource, /aria-expanded=\{exitPlansOpen\}/);
  assert.match(portfolioSource, /\{exitPlansOpen && \(/);
});

test('每只持仓的详细配置默认折叠且可单独展开', () => {
  assert.match(
    portfolioSource,
    /const \[expandedExitPlanSymbols, setExpandedExitPlanSymbols\] = useState<Set<string>>\(\s*\(\) => new Set\(\),\s*\)/,
  );
  assert.match(
    portfolioSource,
    /const exitPlanExpanded = expandedExitPlanSymbols\.has\(position\.symbol\)/,
  );
  assert.match(portfolioSource, /aria-expanded=\{exitPlanExpanded\}/);
  assert.match(portfolioSource, /\{exitPlanExpanded && \(/);
});

test('卖点提醒行动箱保持在计划折叠区域之外', () => {
  const plansEnd = portfolioSource.indexOf(
    '<CockpitPanel title="\u5356\u70b9\u63d0\u9192\u884c\u52a8\u7bb1" icon={BellRing}>',
  );
  const plansConditional = portfolioSource.lastIndexOf('{exitPlansOpen && (', plansEnd);
  const plansConditionalEnd = portfolioSource.indexOf(')}', plansConditional);

  assert.ok(plansConditional >= 0);
  assert.ok(plansConditionalEnd >= 0 && plansConditionalEnd < plansEnd);
});
