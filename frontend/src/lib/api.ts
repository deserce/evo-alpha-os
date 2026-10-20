// frontend/src/lib/api.ts
import axios from 'axios';
import type { ChartPeriod } from './chartPeriods';

// 通过环境变量配置 API 基础路径，默认走 Next.js proxy（/api → 后端）
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE || '/api';

// 创建 axios 实例
const apiInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000, // 300s 超时处理，匹配量化计算耗时
});

// 请求拦截器：记录请求（开发调试用）
apiInstance.interceptors.request.use(
  (config) => {
    if (process.env.NODE_ENV === 'development') {
      console.debug(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器：自动解包 data + 统一错误处理
apiInstance.interceptors.response.use(
  (response) => {
    // 如果返回的数据有 data 字段，自动解包
    if (response.data && typeof response.data === 'object' && 'data' in response.data) {
      return response.data.data;
    }
    return response.data;
  },
  (error) => {
    // 统一错误日志
    if (error.response) {
      const { status, data } = error.response;
      const detail = data?.detail || data?.message || '未知错误';
      console.error(`[API Error] ${status} ${error.config?.url} → ${detail}`);
    } else if (error.request) {
      console.error(`[API Error] 网络请求失败: ${error.config?.url} → 后端可能未启动`);
    } else {
      console.error(`[API Error] ${error.message}`);
    }
    return Promise.reject(error);
  }
);

// 导出时覆盖类型
export const api = apiInstance as any;

// ==================== Market API ====================
export const marketApi = {
  getDates: (limit: number = 120) => api.get('/v1/market/dates', { params: { limit } }),
  getDashboard: (date?: string) => api.get('/v1/market/dashboard', { params: { date: date || 'latest' } }),
  getStats: (tradeDate?: string) => {
    if (tradeDate) {
      return api.get(`/v1/market/stats/${tradeDate}`);
    }
    return api.get('/v1/market/stats/latest');
  },
  getLimitBoards: (date?: string, limit: number = 20) =>
    api.get('/v1/market/limit-boards', { params: { date: date || 'latest', limit } }),
  getNews: (params?: { limit?: number; sector?: string }) =>
    api.get('/v1/market/news', { params }),
  getRpsMatrix: (period: string = 'rps_20') => api.get(`/v1/market/rps_matrix?period=${period}&limit_days=20`),
  getMarketIndices: (date?: string) => api.get('/v1/market/indices', { params: { date: date || 'latest' } }),
  getMarketIndexKline: (
    indexCode: string, days: number = 250, date?: string, period: ChartPeriod = 'daily',
  ) => api.get(`/v1/market/indices/${indexCode}/kline`, {
    params: { days, date: date || 'latest', period },
    timeout: period === '30min' || period === '120min' ? 20000 : 45000,
  }),
  // 指数相关
  getIndexList: () => api.get('/v1/index/list'),
  getIndexInfo: (indexCode: string) => api.get(`/v1/index/info/${indexCode}`),
  getIndexKline: (indexCode: string, days: number = 60) => api.get(`/v1/index/kline/${indexCode}?days=${days}`),
  getIndexLatest: () => api.get('/v1/index/latest'),
  getIndexCompare: (codes: string[]) => api.get('/v1/index/compare', { params: { codes: codes.join(',') } }),
  // 个股相关
  getStockList: (params?: { sector?: string; page?: number; page_size?: number }) =>
    api.get('/v1/stock/list', { params }),
  getStockInfo: (symbol: string) => api.get(`/v1/stock/info/${symbol}`),
  getStockKline: (symbol: string, days: number = 60) => api.get(`/v1/stock/kline/${symbol}?days=${days}`),
  searchStock: (keyword: string) => api.get('/v1/stock/search', { params: { keyword } }),

  // @deprecated 以下端点后端尚未实现，请勿在页面中使用
  // getOverview: () => api.get('/v1/market/overview'),
  // getStatsHistory: (days: number = 30) => api.get(`/v1/market/history?days=${days}`),
  // getSentiment: () => api.get('/v1/market/sentiment'),
  // getNorthbound: () => api.get('/v1/market/northbound'),
  // getSectorHeatmap: (sortBy: string = 'rps_20') => api.get(`/v1/market/sectors?sort_by=${sortBy}`),
  // getSectorStocks: (sectorName: string) => api.get(`/v1/market/sector/${sectorName}/stocks`),
  // getSectorChart: (sectorName: string) => api.get(`/v1/market/sector/${sectorName}/chart`),
};

export type StockFinancialMetricKey =
  | 'total_revenue'
  | 'net_profit'
  | 'deduct_net_profit'
  | 'eps'
  | 'roe'
  | 'net_margin'
  | 'gross_margin'
  | 'operating_cashflow_per_share';

export type StockFinanceQuarter = 1 | 2 | 3 | 4;
export type StockFinanceDataStatus = 'OK' | 'PARTIAL' | 'EMPTY';
export type StockFinanceValueSource = 'explicit' | 'derived' | 'missing';
export type StockFinancialMetricValues = Partial<
  Record<StockFinancialMetricKey, number | null>
>;
export type StockFinancialMetricYoYValues = Partial<
  Record<`${StockFinancialMetricKey}_yoy`, number | null>
>;
export type StockFinancialMetricQoQValues = Partial<
  Record<`${StockFinancialMetricKey}_qoq`, number | null>
>;

export type StockFinancePerformanceRow = {
  report_date: string;
  report_type: string | null;
  report_date_name: string | null;
  year: number;
  quarter: StockFinanceQuarter;
  notice_date: string | null;
  update_date: string | null;
  source: string | null;
  fetched_at: string | null;
  value_source?: StockFinanceValueSource | null;
} & StockFinancialMetricValues & StockFinancialMetricYoYValues & StockFinancialMetricQoQValues;

export interface StockFinancePerformance {
  periods: StockFinancePerformanceRow[];
  annual: StockFinancePerformanceRow[];
  quarters: StockFinancePerformanceRow[];
  available_metrics: StockFinancialMetricKey[];
  latest_report_date: string | null;
  source: string | null;
  fetched_at: string | null;
  data_status: StockFinanceDataStatus;
  as_of: string | null;
}

export interface StockFinanceResponse {
  symbol: string;
  years?: number;
  as_of?: string | null;
  finance: Record<string, unknown>[];
  count: number;
  performance: StockFinancePerformance;
}

export const stockApi = {
  getList: (params?: { sector?: string; page?: number; page_size?: number }) =>
    api.get('/v1/stock/list', { params }),
  getChartData: (symbol: string, period: string = 'daily', endDate?: string, limit: number = 260) =>
    api.get(`/v1/stock/chart/${symbol}`, {
      params: { period, end_date: endDate, limit },
      timeout: period === '30min' || period === '120min' ? 20000 : 45000,
    }),
  getInfo: (symbol: string) => api.get(`/v1/stock/info/${symbol}`),
  getFinance: (symbol: string, years: number = 6, asOf?: string) =>
    api.get(`/v1/stock/finance/${encodeURIComponent(symbol)}`, {
      params: { years, as_of: asOf },
    }) as Promise<StockFinanceResponse>,
  getEnrichment: (symbol: string) => api.get(`/v1/stock/enrichment/${symbol}`),
  refreshEnrichment: (symbol: string, payload?: { modules?: string[]; trade_date?: string }) =>
    api.post(`/v1/stock/enrichment/${symbol}/refresh`, payload || {}),
  searchStock: (keyword: string) => api.get('/v1/stock/search', { params: { keyword } }),
  getPools: (tradeDate?: string) => api.get('/v1/stock/pools', {
    params: { trade_date: tradeDate },
  }),
  createPool: (payload: { name: string; description?: string; sort_order?: number }) =>
    api.post('/v1/stock/pools', payload),
  updatePool: (
    poolId: string,
    payload: { name?: string; description?: string; sort_order?: number; monitor_enabled?: boolean },
  ) => api.patch(`/v1/stock/pools/${poolId}`, payload),
  deletePool: (poolId: string) => api.delete(`/v1/stock/pools/${poolId}`),
  getPoolStocks: (poolId: string, tradeDate?: string) =>
    api.get(`/v1/stock/pools/${poolId}/stocks`, {
      params: { trade_date: tradeDate },
    }),
  addPoolStock: (poolId: string, payload: { symbol: string; note?: string; sort_order?: number }) =>
    api.post(`/v1/stock/pools/${poolId}/stocks`, payload),
  updatePoolStock: (
    poolId: string,
    symbol: string,
    payload: { note?: string; sort_order?: number },
  ) => api.patch(`/v1/stock/pools/${poolId}/stocks/${symbol}`, payload),
  removePoolStock: (poolId: string, symbol: string) =>
    api.delete(`/v1/stock/pools/${poolId}/stocks/${symbol}`),
};

export const watchApi = {
  getChartSignals: (params: { symbol: string; period: ChartPeriod; endDate?: string; limit: number }, signal?: AbortSignal) =>
    api.get('/v1/watch/chart-signals', {
      params: { symbol: params.symbol, period: params.period, end_date: params.endDate, limit: params.limit },
      signal, timeout: 25000,
    }),
  getStructureMonitor: (params: { tradeDate: string; poolId?: string; symbol?: string }): Promise<import('@/lib/structureMonitor').StructureMonitorPayload> =>
    api.get('/v1/watch/structure-monitor', {
      params: { trade_date: params.tradeDate, pool_id: params.poolId || undefined, symbol: params.symbol },
      timeout: 15000,
    }),
  getStructureCandidate: (params: { symbol: string; startDate: string; endDate: string }) =>
    api.get('/v1/watch/structure-candidate', {
      params: { symbol: params.symbol, start_date: params.startDate, end_date: params.endDate },
      timeout: 15000,
    }),
  getStockDetail: (symbol: string, tradeDate?: string) =>
    api.get(`/v1/watch/stocks/${encodeURIComponent(symbol)}`, {
      params: { trade_date: tradeDate },
    }),
  getConfig: () => api.get('/v1/watch/config'),
  updateConfig: (payload: { config: Record<string, unknown>; activate: boolean }) =>
    api.put('/v1/watch/config', payload),
  createScan: (payload: {
    start_date: string;
    end_date: string;
    scope: 'all_groups' | 'monitored_groups' | 'pools';
    pool_ids?: string[];
    rule_version?: number;
  }) => api.post('/v1/watch/scans', payload),
  getScan: (scanId: string) => api.get(`/v1/watch/scans/${scanId}`),
  getScanResults: (params: {
    scanId: string;
    tradeDate?: string;
    poolId?: string;
    state?: string;
    dataStatus?: string;
    symbol?: string;
    limit?: number;
    offset?: number;
  }) => api.get('/v1/watch/scan-results', {
    params: {
      scan_id: params.scanId,
      trade_date: params.tradeDate,
      pool_id: params.poolId,
      state: params.state,
      data_status: params.dataStatus,
      symbol: params.symbol,
      limit: params.limit,
      offset: params.offset,
    },
  }),
  getMarkers: (params: {
    symbol: string;
    scanId?: string;
    startDate?: string;
    endDate?: string;
  }) => api.get('/v1/watch/markers', {
    params: {
      symbol: params.symbol,
      scan_id: params.scanId,
      start_date: params.startDate,
      end_date: params.endDate,
    },
  }),
  getSummary: (params?: { tradeDate?: string; poolId?: string }) =>
    api.get('/v1/watch/summary', {
      params: {
        trade_date: params?.tradeDate,
        pool_id: params?.poolId,
      },
    }),
  getSnapshots: (params?: { tradeDate?: string; poolId?: string; state?: string }) =>
    api.get('/v1/watch/snapshots', {
      params: {
        trade_date: params?.tradeDate,
        pool_id: params?.poolId,
        state: params?.state,
      },
    }),
  getEvents: (params?: {
    tradeDate?: string;
    poolId?: string;
    eventType?: string;
    status?: string;
  }) =>
    api.get('/v1/watch/events', {
      params: {
        trade_date: params?.tradeDate,
        pool_id: params?.poolId,
        event_type: params?.eventType,
        status: params?.status,
      },
    }),
  updateEvent: (
    eventId: string | number,
    payload: { status: 'acknowledged' | 'snoozed'; snoozed_until?: string },
  ) => api.patch(`/v1/watch/events/${eventId}`, payload),
};

export const watchlistApi = {
  getAll: () => api.get('/watchlist'),
  add: (symbol: string) => api.post('/watchlist/add', { symbol }),
  remove: (symbol: string) => api.delete(`/watchlist/${symbol}`),
  getStrategyMeta: () => api.get('/strategy/meta'),
  getStrategyPool: (strategy: string = 'all', date: string = 'all', startDate?: string, endDate?: string) =>
    api.get(`/strategy/pool`, {
      params: {
        strategy_name: strategy,
        date: date,
        start_date: startDate,
        end_date: endDate
      }
    }),
};

export const strategyApi = {
  getList: () => api.get('/strategy/list'),
  getResults: (date: string, strategy?: string) => api.get('/strategy/results', { params: { date, strategy } }),
  run: (params: { strategy_name: string; trade_date?: string; start_date?: string; end_date?: string }) => {
    return api.post('/strategy/run', params);
  },
};

export const quantApi = {
  getDates: (limit: number = 120) => api.get('/v1/quant/dates', { params: { limit } }),
  getDashboard: (tradeDate?: string) => api.get('/v1/quant/dashboard', { params: { trade_date: tradeDate } }),
  getMonitor: () => api.get('/v1/quant/monitor'),
  getDataManagement: (params?: { includeQualityDetail?: boolean; includeDuplicates?: boolean }) =>
    api.get('/v1/quant/data-management', {
      params: {
        include_quality_detail: params?.includeQualityDetail,
        include_duplicates: params?.includeDuplicates,
      },
    }),
  getDataTasks: (limit: number = 20) => api.get('/v1/quant/data-tasks', { params: { limit } }),
  runDataTask: (payload: { task_id: string; params?: Record<string, unknown> }) =>
    api.post('/v1/quant/data-tasks/run', payload),
  getLifecycle: (params: { tradeDate?: string; state?: string; sortBy?: string; sortDir?: 'asc' | 'desc'; limit?: number }) =>
    api.get('/v1/quant/lifecycle', {
      params: {
        trade_date: params.tradeDate,
        state: params.state,
        sort_by: params.sortBy,
        sort_dir: params.sortDir,
        limit: params.limit ?? 200,
      },
    }),
  getSignals: (params: { tradeDate?: string; signalType?: string; limit?: number }) =>
    api.get('/v1/quant/signals', {
      params: {
        trade_date: params.tradeDate,
        signal_type: params.signalType,
        limit: params.limit ?? 200,
      },
    }),
  getTradePlans: (params: { tradeDate?: string; status?: string; limit?: number }) =>
    api.get('/v1/quant/trade-plans', {
      params: {
        trade_date: params.tradeDate,
        status: params.status,
        limit: params.limit ?? 200,
      },
    }),
  getIndustryStrength: (
    tradeDate?: string,
    limit: number = 100,
    sortBy?: string,
    sortDir: 'asc' | 'desc' = 'desc',
  ) =>
    api.get('/v1/quant/industry-strength', {
      params: { trade_date: tradeDate, limit, sort_by: sortBy, sort_dir: sortDir },
    }),
  getSectorRpsHeatmap: (params: {
    tradeDate?: string;
    rpsPeriod?: number;
    days?: number;
    sectors?: number;
    universe?: 'ranked' | 'common';
  }) =>
    api.get('/v1/quant/sector-rps-heatmap', {
      params: {
        trade_date: params.tradeDate,
        rps_period: params.rpsPeriod ?? 20,
        days: params.days ?? 20,
        sectors: params.sectors ?? 100,
        universe: params.universe ?? 'ranked',
      },
    }),
  getSectorChart: (params: {
    sectorName: string; tradeDate?: string; limit?: number;
    mode?: 'local' | 'composite' | 'vendor'; period?: ChartPeriod;
  }) =>
    api.get('/v1/quant/sector-chart', {
      params: {
        sector_name: params.sectorName,
        trade_date: params.tradeDate,
        limit: params.limit ?? 260,
        mode: params.mode ?? 'local',
        period: params.period ?? 'daily',
      },
      timeout: params.period === '30min' || params.period === '120min' ? 20000 : 45000,
    }),
  getIndustryStocks: (params: {
    tradeDate?: string;
    sectorName: string;
    rpsPeriod?: number;
    sortBy?: string;
    sortDir?: 'asc' | 'desc';
    limit?: number;
  }) =>
    api.get('/v1/quant/industry-stocks', {
      params: {
        trade_date: params.tradeDate,
        sector_name: params.sectorName,
        rps_period: params.rpsPeriod ?? 20,
        sort_by: params.sortBy,
        sort_dir: params.sortDir ?? 'desc',
        limit: params.limit ?? 200,
      },
    }),
  getAlphaOpportunities: (params: { tradeDate?: string; rpsPeriod?: number; state?: string; limit?: number }) =>
    api.get('/v1/quant/alpha-opportunities', {
      params: {
        trade_date: params.tradeDate,
        rps_period: params.rpsPeriod ?? 20,
        state: params.state,
        limit: params.limit ?? 200,
      },
    }),
  getResearchSnapshot: (tradeDate?: string, limit: number = 200) =>
    api.get('/v1/quant/research/snapshot', { params: { trade_date: tradeDate, limit } }),
  getReviewSummary: (tradeDate?: string) =>
    api.get('/v1/quant/review/summary', { params: { trade_date: tradeDate } }),
  getBacktestSummary: (params: { startDate?: string; endDate?: string; signalType?: string }) =>
    api.get('/v1/quant/backtest/summary', {
      params: {
        start_date: params.startDate,
        end_date: params.endDate,
        signal_type: params.signalType,
      },
    }),
  getAiBrief: (tradeDate?: string) => api.get('/v1/quant/ai/brief', { params: { trade_date: tradeDate } }),
  getSyncStatus: () => api.get('/v1/quant/sync/status'),
  getStrategyResults: (params: { tradeDate?: string; strategyName?: string; limit?: number }) =>
    api.get('/v1/quant/strategy/results', {
      params: {
        trade_date: params.tradeDate,
        strategy_name: params.strategyName,
        limit: params.limit ?? 500,
      },
    }),
  getStrategyMeta: () => api.get('/v1/quant/strategy/meta'),
};

// ==================== Portfolio API ====================
export type PortfolioAccountType = 'CASH' | 'SIMULATED';
export type PortfolioTradeSide = 'BUY' | 'SELL';
export type PortfolioCashFlowType = 'DEPOSIT' | 'WITHDRAWAL';
export type PortfolioPositionStatus = 'ALL' | 'OPEN' | 'CLOSED';
export type PortfolioPerformancePeriod = 'DAY' | 'WEEK' | 'MONTH' | 'YEAR';
export type PortfolioPerformanceStatus =
  | 'EMPTY'
  | 'INITIAL'
  | 'COMPLETE'
  | 'GAP'
  | 'UNAVAILABLE';
export type PortfolioSnapshotPriceStatus = 'COMPLETE' | 'PARTIAL';

export interface PortfolioAccount {
  id: number;
  name: string;
  broker_name: string | null;
  account_type: PortfolioAccountType;
  currency: string;
  opening_cash_cents: number;
  status: 'ACTIVE' | 'ARCHIVED';
  is_default: boolean;
  cash_cents: number;
  ledger_revision: number;
  baseline_date?: string | null;
  net_contributions_cents?: number | null;
  derived_data_pending?: boolean;
}

export interface PortfolioPosition {
  account_id: number;
  symbol: string;
  name: string | null;
  industry: string | null;
  position_revision: number;
  quantity: number;
  available_quantity: number;
  cost_basis_cents: number;
  avg_cost_micros: number;
  realized_pnl_cents: number;
  status: 'OPEN' | 'CLOSED';
  first_buy_date: string;
  last_trade_date: string;
  last_transaction_id: number;
  price_micros: number | null;
  price_trade_date: string | null;
  price_status: 'CURRENT' | 'STALE' | 'MISSING';
  stale: boolean;
  market_value_cents: number | null;
  unrealized_pnl_cents: number | null;
  unrealized_pnl_bps: number | null;
}

export interface PortfolioTransaction {
  id: number;
  account_id: number;
  counterparty_account_id: number | null;
  client_request_id: string;
  business_type:
    | 'BUY'
    | 'SELL'
    | 'DEPOSIT'
    | 'WITHDRAWAL'
    | 'DIVIDEND'
    | 'FEE'
    | 'TAX'
    | 'TRANSFER'
    | 'CORPORATE_ACTION'
    | 'REVERSAL';
  status: 'POSTED';
  trade_date: string;
  occurred_at: string;
  symbol: string | null;
  quantity: number | null;
  price_micros: number | null;
  gross_amount_cents: number | null;
  cash_delta_cents: number;
  fee_cents: number;
  tax_cents: number;
  target_position_bps: number | null;
  source: 'MANUAL' | 'IMPORT' | 'SYSTEM' | 'API';
  reversal_of: number | null;
  note: string | null;
}

export interface PortfolioTradePreview {
  account_id: number;
  business_type: PortfolioTradeSide;
  trade_date: string;
  symbol: string;
  quantity: number;
  price_micros: number;
  gross_amount_cents: number;
  cash_delta_cents: number;
  fee_cents: number;
  tax_cents: number;
  target_position_bps: number | null;
  ledger_revision: number;
}

export interface PortfolioAccountCreate {
  name: string;
  broker_name?: string;
  account_type: PortfolioAccountType;
  currency?: string;
  opening_cash_cents: number;
  is_default: boolean;
}

export interface PortfolioTradeRequest {
  account_id: number;
  business_type: PortfolioTradeSide;
  trade_date: string;
  symbol: string;
  price_micros: number;
  quantity?: number;
  amount_cents?: number;
  target_position_bps?: number;
  account_equity_cents?: number;
  fee_cents: number;
  tax_cents: number;
}

export interface PortfolioTradePost extends PortfolioTradeRequest {
  client_request_id: string;
  occurred_at: string;
  expected_ledger_revision: number;
  expected_quantity: number;
  expected_gross_amount_cents: number;
  expected_cash_delta_cents: number;
}

export interface PortfolioReverseRequest {
  account_id: number;
  client_request_id: string;
  trade_date: string;
  occurred_at: string;
}

export interface PortfolioCashFlowRequest {
  client_request_id: string;
  business_type: PortfolioCashFlowType;
  trade_date: string;
  occurred_at: string;
  amount_cents: number;
  expected_ledger_revision: number;
  note?: string;
}

export interface PortfolioCashFlowResult {
  transaction: PortfolioTransaction;
  cash_before_cents: number;
  cash_after_cents: number;
  ledger_revision: number;
}

export interface PortfolioPerformanceSnapshot {
  account_id: number;
  snapshot_date: string;
  ledger_revision: number;
  cash_cents: number;
  market_value_cents: number | null;
  total_equity_cents: number | null;
  net_external_flow_cents: number;
  weighted_external_flow_micros: number;
  pnl_cents: number | null;
  return_ppb: number | null;
  realized_pnl_cents: number;
  unrealized_pnl_cents: number | null;
  position_count: number;
  unpriced_position_count: number;
  price_status: PortfolioSnapshotPriceStatus;
  expected_price_date: string;
  performance_status: Exclude<PortfolioPerformanceStatus, 'EMPTY'>;
  return_base_date: string | null;
}

export interface PortfolioPeriodPerformance {
  period: PortfolioPerformancePeriod;
  start_date: string | null;
  end_date: string | null;
  status: PortfolioPerformanceStatus;
  trading_day_count: number;
  net_external_flow_cents: number | null;
  pnl_cents: number | null;
  return_ppb: number | null;
  end_equity_cents: number | null;
  end_realized_pnl_cents: number | null;
  end_unrealized_pnl_cents: number | null;
}

export type PortfolioPerformanceSummary = {
  [Period in PortfolioPerformancePeriod]: PortfolioPeriodPerformance;
};

export interface PortfolioExitPlan {
  account_id: number;
  symbol: string;
  position_revision: number;
  enabled: boolean;
  entry_transaction_id: number;
  hard_stop_micros: number | null;
  take_profit_1_micros: number | null;
  take_profit_1_reduce_bps: number | null;
  take_profit_2_micros: number | null;
  take_profit_2_reduce_bps: number | null;
  take_profit_3_micros: number | null;
  take_profit_3_reduce_bps: number | null;
  trailing_activation_bps: number | null;
  trailing_distance_bps: number | null;
  time_stop_trading_days: number | null;
  time_stop_min_return_bps: number | null;
  trend_exit_enabled: boolean;
  intraday_enabled: boolean;
  rule_version: string;
  created_at: string;
  updated_at: string;
}

export interface PortfolioExitPlanUpdate {
  position_revision: number;
  enabled: boolean;
  hard_stop_micros: number | null;
  take_profit_1_micros: number | null;
  take_profit_1_reduce_bps: number | null;
  take_profit_2_micros: number | null;
  take_profit_2_reduce_bps: number | null;
  take_profit_3_micros: number | null;
  take_profit_3_reduce_bps: number | null;
  trailing_activation_bps: number | null;
  trailing_distance_bps: number | null;
  time_stop_trading_days: number | null;
  time_stop_min_return_bps: number | null;
  trend_exit_enabled: boolean;
  intraday_enabled: boolean;
}

export interface PortfolioExitState {
  account_id: number;
  symbol: string;
  position_revision: number;
  state: string;
  highest_price_micros: number | null;
  trailing_stop_micros: number | null;
  last_price_micros: number | null;
  last_price_date: string | null;
  last_bar_time: string | null;
  data_status: string;
  last_transaction_id: number;
  rule_version: string;
  created_at: string;
  updated_at: string;
}

export type PortfolioExitEventStatus = 'ALL' | 'OPEN' | 'ACKNOWLEDGED' | 'RESOLVED';

export interface PortfolioExitEvent {
  id: number;
  account_id: number;
  symbol: string;
  position_revision: number;
  event_type: string;
  severity: string;
  event_status: Exclude<PortfolioExitEventStatus, 'ALL'>;
  trigger_date: string;
  triggered_at: string;
  bar_time: string | null;
  observed_price_micros: number | null;
  available_quantity: number;
  recommended_quantity: number;
  executable: boolean;
  tier: number | null;
  dedupe_key: string;
  payload_json: string | null;
  rule_version: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export const portfolioApi = {
  getAccounts: () =>
    apiInstance.get<PortfolioAccount[], PortfolioAccount[]>('/v1/portfolio/accounts'),
  createAccount: (payload: PortfolioAccountCreate) =>
    apiInstance.post<PortfolioAccount, PortfolioAccount, PortfolioAccountCreate>(
      '/v1/portfolio/accounts',
      payload,
    ),
  postCashFlow: (
    accountId: number,
    payload: PortfolioCashFlowRequest,
  ) =>
    apiInstance.post<
      PortfolioCashFlowResult,
      PortfolioCashFlowResult,
      PortfolioCashFlowRequest
    >(`/v1/portfolio/accounts/${accountId}/cash-flows`, payload),
  getPositions: (
    accountId: number,
    asOfDate: string,
    status: PortfolioPositionStatus = 'ALL',
  ) =>
    apiInstance.get<PortfolioPosition[], PortfolioPosition[]>(
      '/v1/portfolio/positions',
      { params: { account_id: accountId, as_of_date: asOfDate, status } },
    ),
  getStatement: (accountId: number) =>
    apiInstance.get<
      { transactions: (PortfolioTransaction & { name: string | null; is_reversed: boolean })[]; truncated: boolean; limit: number },
      { transactions: (PortfolioTransaction & { name: string | null; is_reversed: boolean })[]; truncated: boolean; limit: number }
    >('/v1/portfolio/statement', { params: { account_id: accountId } }),
  getTransactions: (accountId: number, limit: number = 100) =>
    apiInstance.get<PortfolioTransaction[], PortfolioTransaction[]>(
      '/v1/portfolio/transactions',
      { params: { account_id: accountId, limit } },
    ),
  getPerformanceSnapshots: (accountId: number, limit: number = 400) =>
    apiInstance.get<
      PortfolioPerformanceSnapshot[],
      PortfolioPerformanceSnapshot[]
    >(`/v1/portfolio/accounts/${accountId}/performance/snapshots`, {
      params: { limit },
    }),
  getPerformanceSummary: (accountId: number, asOfDate?: string) =>
    apiInstance.get<
      PortfolioPerformanceSummary,
      PortfolioPerformanceSummary
    >(`/v1/portfolio/accounts/${accountId}/performance/summary`, {
      params: { as_of_date: asOfDate },
    }),
  getExitPlans: (accountId: number) =>
    apiInstance.get<PortfolioExitPlan[], PortfolioExitPlan[]>(
      `/v1/portfolio/accounts/${accountId}/exit-plans`,
    ),
  updateExitPlan: (
    accountId: number,
    symbol: string,
    payload: PortfolioExitPlanUpdate,
  ) =>
    apiInstance.put<
      PortfolioExitPlan,
      PortfolioExitPlan,
      PortfolioExitPlanUpdate
    >(`/v1/portfolio/accounts/${accountId}/exit-plans/${symbol}`, payload),
  getExitStates: (accountId: number) =>
    apiInstance.get<PortfolioExitState[], PortfolioExitState[]>(
      `/v1/portfolio/accounts/${accountId}/exit-states`,
    ),
  getExitEvents: (
    accountId: number,
    status: PortfolioExitEventStatus = 'ALL',
  ) =>
    apiInstance.get<PortfolioExitEvent[], PortfolioExitEvent[]>(
      `/v1/portfolio/accounts/${accountId}/exit-events`,
      { params: { status } },
    ),
  acknowledgeExitEvent: (eventId: number) =>
    apiInstance.post<PortfolioExitEvent, PortfolioExitEvent>(
      `/v1/portfolio/exit-events/${eventId}/acknowledge`,
    ),
  resolveExitEvent: (eventId: number) =>
    apiInstance.post<PortfolioExitEvent, PortfolioExitEvent>(
      `/v1/portfolio/exit-events/${eventId}/resolve`,
    ),
  previewTrade: (payload: PortfolioTradeRequest) =>
    apiInstance.post<
      PortfolioTradePreview,
      PortfolioTradePreview,
      PortfolioTradeRequest
    >('/v1/portfolio/trades/preview', payload),
  postTrade: (payload: PortfolioTradePost) =>
    apiInstance.post<PortfolioTransaction, PortfolioTransaction, PortfolioTradePost>(
      '/v1/portfolio/trades',
      payload,
    ),
  reverseTransaction: (
    transactionId: number,
    payload: PortfolioReverseRequest,
  ) =>
    apiInstance.post<
      PortfolioTransaction,
      PortfolioTransaction,
      PortfolioReverseRequest
    >(`/v1/portfolio/transactions/${transactionId}/reverse`, payload),
};
