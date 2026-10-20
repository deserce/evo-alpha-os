import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { api, stockApi } from '../src/lib/api.ts';
import * as financialPerformance from '../src/components/stock/stockFinancialPerformance.ts';
import {
  STOCK_FINANCIAL_METRICS,
  buildFinancialYearGroups,
  collectFinancialMetricValues,
  formatFinancialMetricValue,
  resolveFinancialMetricScale,
} from '../src/components/stock/stockFinancialPerformance.ts';

const financeResponse = {
  symbol: '002821',
  years: 3,
  as_of: '2026-08-25',
  performance: {
    periods: [
      {
        report_date: '2023-12-31',
        report_type: 'annual',
        report_date_name: '2023年报',
        year: 2023,
        quarter: 4,
        total_revenue: 1_000_000_000,
        total_revenue_yoy: 8.2,
      },
      {
        report_date: '2024-06-30',
        report_type: 'cumulative',
        report_date_name: '2024中报',
        year: 2024,
        quarter: 2,
        total_revenue: 600_000_000,
        total_revenue_yoy: 10.5,
      },
      {
        report_date: '2024-12-31',
        report_type: 'annual',
        report_date_name: '2024年报',
        year: 2024,
        quarter: 4,
        total_revenue: 1_200_000_000,
        total_revenue_yoy: 20,
      },
      {
        report_date: '2025-03-31',
        report_type: 'cumulative',
        report_date_name: '2025一季报',
        year: 2025,
        quarter: 1,
        total_revenue: 200_000_000,
        total_revenue_yoy: 4,
      },
      {
        report_date: '2025-06-30',
        report_type: 'cumulative',
        report_date_name: '2025中报',
        year: 2025,
        quarter: 2,
        total_revenue: 500_000_000,
        total_revenue_yoy: -3.5,
        net_profit: -20_000_000,
        net_profit_yoy: -125,
        notice_date: '2025-08-20',
        source: 'eastmoney_f10',
      },
    ],
    annual: [
      {
        report_date: '2023-12-31',
        report_type: 'annual',
        year: 2023,
        quarter: 4,
        total_revenue: 1_000_000_000,
      },
      {
        report_date: '2024-12-31',
        report_type: 'annual',
        year: 2024,
        quarter: 4,
        total_revenue: 1_200_000_000,
        total_revenue_yoy: 20,
      },
    ],
    quarters: [
      {
        report_date: '2024-03-31',
        report_type: 'single_quarter',
        year: 2024,
        quarter: 1,
        total_revenue: 250_000_000,
        value_source: 'explicit',
      },
      {
        report_date: '2024-06-30',
        report_type: 'single_quarter',
        year: 2024,
        quarter: 2,
        total_revenue: 350_000_000,
        value_source: 'explicit',
      },
      {
        report_date: '2024-12-31',
        report_type: 'single_quarter',
        year: 2024,
        quarter: 4,
        total_revenue: -50_000_000,
        value_source: 'explicit',
      },
      {
        report_date: '2025-03-31',
        report_type: 'single_quarter',
        year: 2025,
        quarter: 1,
        net_profit: 0,
        value_source: 'explicit',
      },
      {
        report_date: '2025-06-30',
        report_type: 'single_quarter',
        year: 2025,
        quarter: 2,
        net_profit: -20_000_000,
        value_source: 'explicit',
      },
    ],
    available_metrics: ['total_revenue', 'net_profit'],
    latest_report_date: '2025-06-30',
    source: 'eastmoney_f10',
    fetched_at: '2026-08-25 09:30:00',
    data_status: 'PARTIAL',
    as_of: '2026-08-25',
  },
};

const stockWorkspaceSource = readFileSync(
  new URL('../src/components/stock/StockWorkspace.tsx', import.meta.url),
  'utf8',
);
const performanceChartSource = readFileSync(
  new URL('../src/components/stock/StockPerformanceChart.tsx', import.meta.url),
  'utf8',
);

test('getFinance 传递年数和截止日期并安全编码股票代码', async () => {
  const originalGet = api.get;
  let request = null;
  api.get = (url, config) => {
    request = { url, params: config?.params };
    return Promise.resolve(financeResponse);
  };

  try {
    const response = await stockApi.getFinance('60/0519', 6, '2026-08-25');
    assert.equal(response, financeResponse);
    assert.deepEqual(request, {
      url: '/v1/stock/finance/60%2F0519',
      params: { years: 6, as_of: '2026-08-25' },
    });
  } finally {
    api.get = originalGet;
  }
});

test('最近 N 年按年分组，年报或当年最新累计报告作为背景值', () => {
  const groups = buildFinancialYearGroups(financeResponse, 2);

  assert.deepEqual(groups.map((group) => group.year), [2024, 2025]);
  assert.equal(groups[0].summary?.quarter, 4);
  assert.equal(groups[0].summary?.label, '年报');
  assert.equal(groups[0].summary?.values.total_revenue, 1_200_000_000);
  assert.equal(groups[1].summary?.quarter, 2);
  assert.equal(groups[1].summary?.label, '中报累计');
  assert.equal(groups[1].summary?.values.total_revenue, 500_000_000);
  assert.equal(groups[1].summary?.noticeDate, '2025-08-20');
});

test('单季度只使用 quarters 真实行，缺季为 null 且保留零值和负值', () => {
  const groups = buildFinancialYearGroups(financeResponse, 2);
  const year2024 = groups[0];
  const year2025 = groups[1];

  assert.equal(year2024.quarters[0]?.values.total_revenue, 250_000_000);
  assert.equal(year2024.quarters[1]?.values.total_revenue, 350_000_000);
  assert.equal(year2024.quarters[2], null);
  assert.equal(year2024.quarters[3]?.values.total_revenue, -50_000_000);
  assert.equal(year2025.quarters[0]?.values.net_profit, 0);
  assert.equal(year2025.quarters[1]?.values.net_profit, -20_000_000);
  assert.equal(year2025.quarters[2], null);
  assert.equal(year2025.quarters[3], null);
});

test('同比从当年最新累计同口径行读取，不使用单季值推算', () => {
  const groups = buildFinancialYearGroups(financeResponse, 2);

  assert.equal(groups[0].summary?.yoy.total_revenue, 20);
  assert.equal(groups[1].summary?.yoy.total_revenue, -3.5);
  assert.equal(groups[1].summary?.yoy.net_profit, -125);
  assert.equal(groups[0].quarters[0]?.yoy.total_revenue, null);
});

test('八个指标的字段名与后端契约一致', () => {
  assert.deepEqual(STOCK_FINANCIAL_METRICS.map((metric) => metric.key), [
    'total_revenue',
    'net_profit',
    'deduct_net_profit',
    'eps',
    'roe',
    'net_margin',
    'gross_margin',
    'operating_cashflow_per_share',
  ]);
});

test('金额整组使用统一万/亿缩放，比率和每股值格式化不丢符号', () => {
  const groups = buildFinancialYearGroups(financeResponse, 2);
  const values = collectFinancialMetricValues(groups, 'total_revenue');
  const scale = resolveFinancialMetricScale('total_revenue', values);

  assert.deepEqual(scale, { divisor: 100_000_000, unit: '亿', decimals: 2 });
  assert.equal(formatFinancialMetricValue(3_231_000_000, 'total_revenue', scale), '32.31亿');
  assert.equal(formatFinancialMetricValue(-50_000_000, 'total_revenue', scale), '-0.50亿');
  assert.equal(formatFinancialMetricValue(-3.5, 'roe'), '-3.50%');
  assert.equal(formatFinancialMetricValue(0, 'eps'), '0.00元/股');
  assert.equal(formatFinancialMetricValue(null, 'net_profit'), '—');
});

test('图表将每年累计值作为背景柱，并固定保留 Q1-Q4 四个单季位置', () => {
  const groups = buildFinancialYearGroups(financeResponse, 2);
  const presentation = financialPerformance.buildFinancialChartPresentation(
    groups,
    'total_revenue',
  );

  assert.deepEqual(presentation.categories, ['2024\n年报', '2025\n中报累计']);
  assert.deepEqual(presentation.summaryValues, [12, 5]);
  assert.deepEqual(presentation.quarterValues, [
    [2.5, null],
    [3.5, null],
    [null, null],
    [-0.5, null],
  ]);
});

test('图表数据保留累计同比线、零值、负值和缺失季度 null', () => {
  const groups = buildFinancialYearGroups(financeResponse, 2);
  const presentation = financialPerformance.buildFinancialChartPresentation(
    groups,
    'net_profit',
  );

  assert.equal(presentation.scale.unit, '万');
  assert.deepEqual(presentation.summaryValues, [null, -2000]);
  assert.deepEqual(presentation.quarterValues, [
    [null, 0],
    [null, -2000],
    [null, null],
    [null, null],
  ]);
  assert.deepEqual(presentation.yoyValues, [null, -125]);
});

test('图表序列包含一个年度/累计背景柱、四个单季柱和累计同比线', () => {
  const groups = buildFinancialYearGroups(financeResponse, 2);
  const presentation = financialPerformance.buildFinancialChartPresentation(
    groups,
    'total_revenue',
  );
  const series = financialPerformance.buildFinancialChartSeries(presentation);

  assert.deepEqual(series.map((item) => [item.key, item.kind, item.name]), [
    ['summary', 'bar', '年度/累计'],
    ['q1', 'bar', 'Q1'],
    ['q2', 'bar', 'Q2'],
    ['q3', 'bar', 'Q3'],
    ['q4', 'bar', 'Q4'],
    ['yoy', 'line', '累计同比'],
  ]);
  assert.deepEqual(series.at(-1)?.data, [20, -3.5]);
});

test('资料面板将财务页升级为业绩页并且只渲染新业绩图', () => {
  assert.match(stockWorkspaceSource, /import StockPerformanceChart from/);
  assert.match(stockWorkspaceSource, /\{ id: 'finance', label: '业绩' \}/);
  assert.match(stockWorkspaceSource, /<StockPerformanceChart\b/);
  assert.equal((stockWorkspaceSource.match(/<SingleQuarterProfitChart\b/g) ?? []).length, 0);
});

test('详细业绩查询仅在业绩页可见时按股票懒加载最新披露', () => {
  assert.match(
    stockWorkspaceSource,
    /queryKey:\s*\['stockFinancialPerformance', symbol, 'latest'\]/,
  );
  assert.match(
    stockWorkspaceSource,
    /stockApi\.getFinance\(symbol, 6\)/,
  );
  assert.match(
    stockWorkspaceSource,
    /enabled:\s*Boolean\(symbol && !hidden && activeTab === 'finance'\)/,
  );
});

test('业绩页更新只补齐 finance_summary 并失效业绩与资料缓存', () => {
  assert.match(stockWorkspaceSource, /finance:\s*\['finance_summary'\]/);
  assert.match(stockWorkspaceSource, /refresh\.finance_summary/);
  assert.match(stockWorkspaceSource, /financeSummaryStatus\.updated/);
  assert.match(stockWorkspaceSource, /financeSummaryStatus\.checked/);
  assert.match(
    stockWorkspaceSource,
    /invalidateQueries\(\{ queryKey: \['stockFinancialPerformance', symbol\] \}\)/,
  );
  assert.match(
    stockWorkspaceSource,
    /invalidateQueries\(\{ queryKey: \['stockEnrichment', symbol\] \}\)/,
  );
});

test('资料面板只保留一个刷新入口，业绩图与空状态不再重复放按钮', () => {
  const performanceChartUsage = stockWorkspaceSource.match(
    /<StockPerformanceChart[\s\S]*?\/>/,
  )?.[0] ?? '';

  assert.equal((stockWorkspaceSource.match(/onClick=\{requestRefresh\}/g) ?? []).length, 1);
  assert.doesNotMatch(stockWorkspaceSource, /function EmptyCache\(\{ onRefresh/);
  assert.doesNotMatch(performanceChartUsage, /onRefresh=/);
  assert.doesNotMatch(performanceChartSource, /onRefresh\?:/);
  assert.doesNotMatch(performanceChartSource, /<RefreshCw\b/);
});

test('唯一刷新入口按当前页签提交有界模块并显示明确文案', () => {
  assert.match(
    stockWorkspaceSource,
    /const enrichmentRefreshModules: Record<EnrichmentTab, string\[]>/,
  );
  assert.match(stockWorkspaceSource, /profile:\s*\['profile', 'finance', 'concepts', 'research'\]/);
  assert.match(stockWorkspaceSource, /finance:\s*\['finance_summary'\]/);
  assert.match(stockWorkspaceSource, /f10:\s*\['f10'\]/);
  assert.match(stockWorkspaceSource, /funds:\s*\['funds'\]/);
  assert.match(stockWorkspaceSource, /risk:\s*\['risk'\]/);
  assert.match(stockWorkspaceSource, /modules:\s*enrichmentRefreshModules\[target\]/);
  assert.match(stockWorkspaceSource, /refreshMutation\.mutate\(activeTab\)/);
  assert.match(stockWorkspaceSource, /const activeRefreshAction = enrichmentRefreshActions\[activeTab\]/);
  assert.match(stockWorkspaceSource, /refreshMutation\.variables === 'finance'/);
  assert.match(stockWorkspaceSource, /'补齐中…'\s*:\s*'同步中…'/);
  assert.match(stockWorkspaceSource, /activeRefreshing\s*\?\s*'读取中…'/);
  assert.match(stockWorkspaceSource, /aria-label=\{activeRefreshTitle\}/);
});

test('非业绩资料同步必须显示全成功、部分失败或空响应回执', () => {
  assert.match(stockWorkspaceSource, /const statusEntries = Object\.entries\(refresh\)/);
  assert.match(stockWorkspaceSource, /const failedModules = statusEntries\.filter/);
  assert.match(stockWorkspaceSource, /资料同步未返回模块结果/);
  assert.match(stockWorkspaceSource, /同步部分失败/);
  assert.match(stockWorkspaceSource, /同步完成/);
  assert.match(
    stockWorkspaceSource,
    /activeTab !== 'finance' && \(refreshFeedback \|\| refreshError\)/,
  );
  assert.match(stockWorkspaceSource, /aria-live="polite"/);
});

test('个股资料面板按视口收敛，大页详情保持更宽且收起入口可见', () => {
  assert.ok(stockWorkspaceSource.includes("showDetailLink\n              ? 'lg:grid-cols-[minmax(0,1fr)_clamp(320px,25vw,360px)]'"));
  assert.ok(stockWorkspaceSource.includes("'lg:grid-cols-[minmax(0,2.125fr)_minmax(360px,1fr)]'"));
  assert.match(stockWorkspaceSource, /infoPanelHidden\s*\?\s*'lg:grid-cols-\[minmax\(0,1fr\)_44px\]'/);
});
