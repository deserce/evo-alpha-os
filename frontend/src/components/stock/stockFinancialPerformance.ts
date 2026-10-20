import type {
  StockFinancePerformance,
  StockFinancePerformanceRow,
  StockFinanceQuarter,
  StockFinanceResponse,
  StockFinanceValueSource,
  StockFinancialMetricKey,
} from '../../lib/api';

export type FinancialMetricValueKind = 'amount' | 'percentage' | 'per_share';

export interface StockFinancialMetricDefinition {
  key: StockFinancialMetricKey;
  label: string;
  shortLabel: string;
  valueKind: FinancialMetricValueKind;
}

export const STOCK_FINANCIAL_METRICS: readonly StockFinancialMetricDefinition[] = [
  {
    key: 'total_revenue',
    label: '营业总收入',
    shortLabel: '营收',
    valueKind: 'amount',
  },
  {
    key: 'net_profit',
    label: '归属母公司股东净利润',
    shortLabel: '归母净利润',
    valueKind: 'amount',
  },
  {
    key: 'deduct_net_profit',
    label: '扣除非经常性损益净利润',
    shortLabel: '扣非净利润',
    valueKind: 'amount',
  },
  {
    key: 'eps',
    label: '基本每股收益',
    shortLabel: '每股收益',
    valueKind: 'per_share',
  },
  {
    key: 'roe',
    label: '净资产收益率',
    shortLabel: 'ROE',
    valueKind: 'percentage',
  },
  {
    key: 'net_margin',
    label: '销售净利率',
    shortLabel: '净利率',
    valueKind: 'percentage',
  },
  {
    key: 'gross_margin',
    label: '销售毛利率',
    shortLabel: '毛利率',
    valueKind: 'percentage',
  },
  {
    key: 'operating_cashflow_per_share',
    label: '每股经营活动产生的现金流量净额',
    shortLabel: '每股经营现金流',
    valueKind: 'per_share',
  },
];

export type FinancialMetricValueMap = Record<StockFinancialMetricKey, number | null>;

export interface FinancialPerformancePoint {
  reportDate: string;
  reportDateName: string | null;
  reportType: string | null;
  year: number;
  quarter: StockFinanceQuarter;
  label: string;
  values: FinancialMetricValueMap;
  yoy: FinancialMetricValueMap;
  qoq: FinancialMetricValueMap;
  noticeDate: string | null;
  updateDate: string | null;
  source: string | null;
  fetchedAt: string | null;
  valueSource: StockFinanceValueSource | null;
}

export type FinancialQuarterPoints = [
  FinancialPerformancePoint | null,
  FinancialPerformancePoint | null,
  FinancialPerformancePoint | null,
  FinancialPerformancePoint | null,
];

export interface FinancialYearGroup {
  year: number;
  summary: FinancialPerformancePoint | null;
  quarters: FinancialQuarterPoints;
}

export type FinancialMetricUnit = '元' | '万' | '亿' | '%' | '元/股';

export interface FinancialMetricScale {
  divisor: number;
  unit: FinancialMetricUnit;
  decimals: number;
}

export interface FinancialChartPresentation {
  metricKey: StockFinancialMetricKey;
  metric: StockFinancialMetricDefinition;
  scale: FinancialMetricScale;
  groups: readonly FinancialYearGroup[];
  categories: string[];
  summaryValues: Array<number | null>;
  quarterValues: [
    Array<number | null>,
    Array<number | null>,
    Array<number | null>,
    Array<number | null>,
  ];
  yoyValues: Array<number | null>;
}

export type FinancialChartSeriesKey = 'summary' | 'q1' | 'q2' | 'q3' | 'q4' | 'yoy';

export interface FinancialChartSeriesModel {
  key: FinancialChartSeriesKey;
  name: string;
  kind: 'bar' | 'line';
  data: Array<number | null>;
}

type StockFinancePerformanceInput =
  | StockFinanceResponse
  | StockFinancePerformance
  | null
  | undefined;

function nullableNumber(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value !== 'string' || value.trim() === '') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function nullableText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function metricValues(row: StockFinancePerformanceRow): FinancialMetricValueMap {
  return {
    total_revenue: nullableNumber(row.total_revenue),
    net_profit: nullableNumber(row.net_profit),
    deduct_net_profit: nullableNumber(row.deduct_net_profit),
    eps: nullableNumber(row.eps),
    roe: nullableNumber(row.roe),
    net_margin: nullableNumber(row.net_margin),
    gross_margin: nullableNumber(row.gross_margin),
    operating_cashflow_per_share: nullableNumber(row.operating_cashflow_per_share),
  };
}

function metricYoYValues(row: StockFinancePerformanceRow): FinancialMetricValueMap {
  return {
    total_revenue: nullableNumber(row.total_revenue_yoy),
    net_profit: nullableNumber(row.net_profit_yoy),
    deduct_net_profit: nullableNumber(row.deduct_net_profit_yoy),
    eps: nullableNumber(row.eps_yoy),
    roe: nullableNumber(row.roe_yoy),
    net_margin: nullableNumber(row.net_margin_yoy),
    gross_margin: nullableNumber(row.gross_margin_yoy),
    operating_cashflow_per_share: nullableNumber(row.operating_cashflow_per_share_yoy),
  };
}

function metricQoQValues(row: StockFinancePerformanceRow): FinancialMetricValueMap {
  return {
    total_revenue: nullableNumber(row.total_revenue_qoq),
    net_profit: nullableNumber(row.net_profit_qoq),
    deduct_net_profit: nullableNumber(row.deduct_net_profit_qoq),
    eps: nullableNumber(row.eps_qoq),
    roe: nullableNumber(row.roe_qoq),
    net_margin: nullableNumber(row.net_margin_qoq),
    gross_margin: nullableNumber(row.gross_margin_qoq),
    operating_cashflow_per_share: nullableNumber(row.operating_cashflow_per_share_qoq),
  };
}

function reportYear(row: StockFinancePerformanceRow): number | null {
  const explicitYear = nullableNumber(row.year);
  if (explicitYear !== null && Number.isInteger(explicitYear) && explicitYear >= 1900) {
    return explicitYear;
  }
  const match = nullableText(row.report_date)?.match(/^(\d{4})-/);
  return match ? Number(match[1]) : null;
}

function reportQuarter(row: StockFinancePerformanceRow): StockFinanceQuarter | null {
  const explicitQuarter = nullableNumber(row.quarter);
  if (
    explicitQuarter !== null
    && Number.isInteger(explicitQuarter)
    && explicitQuarter >= 1
    && explicitQuarter <= 4
  ) {
    return explicitQuarter as StockFinanceQuarter;
  }
  const month = nullableText(row.report_date)?.slice(5, 7);
  const quarterByMonth: Record<string, StockFinanceQuarter> = {
    '03': 1,
    '06': 2,
    '09': 3,
    '12': 4,
  };
  return month ? quarterByMonth[month] ?? null : null;
}

function summaryLabel(quarter: StockFinanceQuarter): string {
  return {
    1: '一季报累计',
    2: '中报累计',
    3: '三季报累计',
    4: '年报',
  }[quarter];
}

function quarterLabel(quarter: StockFinanceQuarter): string {
  return {
    1: '一季度',
    2: '二季度',
    3: '三季度',
    4: '四季度',
  }[quarter];
}

function normalizePoint(
  row: StockFinancePerformanceRow,
  kind: 'summary' | 'quarter',
): FinancialPerformancePoint | null {
  const year = reportYear(row);
  const quarter = reportQuarter(row);
  if (year === null || quarter === null) {
    return null;
  }
  return {
    reportDate: nullableText(row.report_date) ?? '',
    reportDateName: nullableText(row.report_date_name),
    reportType: nullableText(row.report_type),
    year,
    quarter,
    label: kind === 'summary' ? summaryLabel(quarter) : quarterLabel(quarter),
    values: metricValues(row),
    yoy: metricYoYValues(row),
    qoq: metricQoQValues(row),
    noticeDate: nullableText(row.notice_date),
    updateDate: nullableText(row.update_date),
    source: nullableText(row.source),
    fetchedAt: nullableText(row.fetched_at),
    valueSource: nullableText(row.value_source) as StockFinanceValueSource | null,
  };
}

function performanceFromInput(
  input: StockFinancePerformanceInput,
): StockFinancePerformance | null {
  if (!input) {
    return null;
  }
  if ('performance' in input) {
    return input.performance;
  }
  return input;
}

function pointSortKey(point: FinancialPerformancePoint): string {
  return [point.reportDate, point.updateDate ?? '', point.fetchedAt ?? ''].join('|');
}

function shouldReplacePoint(
  current: FinancialPerformancePoint | null | undefined,
  candidate: FinancialPerformancePoint,
): boolean {
  if (!current) {
    return true;
  }
  if (candidate.quarter !== current.quarter) {
    return candidate.quarter > current.quarter;
  }
  return pointSortKey(candidate) >= pointSortKey(current);
}

function boundedYears(years: number): number {
  if (!Number.isFinite(years)) {
    return 6;
  }
  return Math.max(1, Math.floor(years));
}

/**
 * 将后端的累计报告期与真实单季数据转换为图表可直接消费的年度分组。
 * 单季缺口保留 null，不在前端用累计值做差分。
 */
export function buildFinancialYearGroups(
  input: StockFinancePerformanceInput,
  years: number = 6,
): FinancialYearGroup[] {
  const performance = performanceFromInput(input);
  if (!performance) {
    return [];
  }

  const summaryByYear = new Map<number, FinancialPerformancePoint>();
  const quartersByYear = new Map<number, FinancialQuarterPoints>();
  const allYears = new Set<number>();

  // annual 作为兜底；同报告期的 periods 字段更完整，后处理并覆盖。
  for (const row of [...(performance.annual ?? []), ...(performance.periods ?? [])]) {
    const point = normalizePoint(row, 'summary');
    if (!point) {
      continue;
    }
    allYears.add(point.year);
    const current = summaryByYear.get(point.year);
    if (shouldReplacePoint(current, point)) {
      summaryByYear.set(point.year, point);
    }
  }

  for (const row of performance.quarters ?? []) {
    const point = normalizePoint(row, 'quarter');
    if (!point) {
      continue;
    }
    allYears.add(point.year);
    const points = quartersByYear.get(point.year) ?? [null, null, null, null];
    const index = (point.quarter - 1) as 0 | 1 | 2 | 3;
    if (shouldReplacePoint(points[index], point)) {
      points[index] = point;
    }
    quartersByYear.set(point.year, points);
  }

  const selectedYears = [...allYears]
    .sort((left, right) => left - right)
    .slice(-boundedYears(years));

  return selectedYears.map((year) => ({
    year,
    summary: summaryByYear.get(year) ?? null,
    quarters: quartersByYear.get(year) ?? [null, null, null, null],
  }));
}

export function getFinancialMetricDefinition(
  metricKey: StockFinancialMetricKey,
): StockFinancialMetricDefinition {
  return STOCK_FINANCIAL_METRICS.find((metric) => metric.key === metricKey)
    ?? STOCK_FINANCIAL_METRICS[0];
}

export function collectFinancialMetricValues(
  groups: readonly FinancialYearGroup[],
  metricKey: StockFinancialMetricKey,
): number[] {
  const values: number[] = [];
  for (const group of groups) {
    const summaryValue = group.summary?.values[metricKey];
    if (summaryValue !== null && summaryValue !== undefined && Number.isFinite(summaryValue)) {
      values.push(summaryValue);
    }
    for (const quarter of group.quarters) {
      const quarterValue = quarter?.values[metricKey];
      if (quarterValue !== null && quarterValue !== undefined && Number.isFinite(quarterValue)) {
        values.push(quarterValue);
      }
    }
  }
  return values;
}

export function resolveFinancialMetricScale(
  metricKey: StockFinancialMetricKey,
  values: readonly (number | null | undefined)[] = [],
): FinancialMetricScale {
  const definition = getFinancialMetricDefinition(metricKey);
  if (definition.valueKind === 'percentage') {
    return { divisor: 1, unit: '%', decimals: 2 };
  }
  if (definition.valueKind === 'per_share') {
    return { divisor: 1, unit: '元/股', decimals: 2 };
  }

  const maxAbsoluteValue = values.reduce<number>((maximum, value) => {
    return typeof value === 'number' && Number.isFinite(value)
      ? Math.max(maximum, Math.abs(value))
      : maximum;
  }, 0);
  if (maxAbsoluteValue >= 100_000_000) {
    return { divisor: 100_000_000, unit: '亿', decimals: 2 };
  }
  if (maxAbsoluteValue >= 10_000) {
    return { divisor: 10_000, unit: '万', decimals: 2 };
  }
  return { divisor: 1, unit: '元', decimals: 2 };
}

function scaledChartValue(
  value: number | null | undefined,
  scale: FinancialMetricScale,
): number | null {
  return value === null || value === undefined || !Number.isFinite(value)
    ? null
    : value / scale.divisor;
}

export function buildFinancialChartPresentation(
  groups: readonly FinancialYearGroup[],
  metricKey: StockFinancialMetricKey,
): FinancialChartPresentation {
  const metric = getFinancialMetricDefinition(metricKey);
  const scale = resolveFinancialMetricScale(
    metricKey,
    collectFinancialMetricValues(groups, metricKey),
  );
  const quarterValues: FinancialChartPresentation['quarterValues'] = [[], [], [], []];

  for (const group of groups) {
    for (let index = 0; index < 4; index += 1) {
      const quarter = group.quarters[index];
      quarterValues[index].push(scaledChartValue(quarter?.values[metricKey], scale));
    }
  }

  return {
    metricKey,
    metric,
    scale,
    groups,
    categories: groups.map((group) => (
      `${group.year}\n${group.summary?.label ?? '暂无累计'}`
    )),
    summaryValues: groups.map((group) => (
      scaledChartValue(group.summary?.values[metricKey], scale)
    )),
    quarterValues,
    yoyValues: groups.map((group) => group.summary?.yoy[metricKey] ?? null),
  };
}

export function buildFinancialChartSeries(
  presentation: FinancialChartPresentation,
): FinancialChartSeriesModel[] {
  return [
    {
      key: 'summary',
      name: '年度/累计',
      kind: 'bar',
      data: presentation.summaryValues,
    },
    ...presentation.quarterValues.map((data, index) => ({
      key: `q${index + 1}` as FinancialChartSeriesKey,
      name: `Q${index + 1}`,
      kind: 'bar' as const,
      data,
    })),
    {
      key: 'yoy',
      name: '累计同比',
      kind: 'line',
      data: presentation.yoyValues,
    },
  ];
}

export function formatFinancialMetricValue(
  value: number | null | undefined,
  metricKey: StockFinancialMetricKey,
  scale?: FinancialMetricScale,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '—';
  }
  const resolvedScale = scale ?? resolveFinancialMetricScale(metricKey, [value]);
  const scaledValue = value / resolvedScale.divisor;
  const zeroThreshold = 0.5 * 10 ** -resolvedScale.decimals;
  const normalizedValue = Math.abs(scaledValue) < zeroThreshold ? 0 : scaledValue;
  return `${normalizedValue.toFixed(resolvedScale.decimals)}${resolvedScale.unit}`;
}
