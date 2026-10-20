'use client';

import axios from 'axios';
import BookingDrawer from '@/components/portfolio/BookingDrawer';
import EquityChart from '@/components/portfolio/EquityChart';
import TransactionStatement from '@/components/portfolio/TransactionStatement';
import Link from 'next/link';
import { useEffect, useMemo, useRef, useState, type ComponentProps } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BellRing,
  BriefcaseBusiness,
  ChevronDown,
  CircleDollarSign,
  Landmark,
  Plus,
  Scale,
  Settings2,
  ShieldAlert,
  TrendingUp,
  WalletCards,
} from 'lucide-react';
import {
  type PortfolioAccountCreate,
  type PortfolioCashFlowRequest,
  type PortfolioCashFlowType,
  type PortfolioExitEvent,
  type PortfolioExitPlan,
  type PortfolioExitPlanUpdate,
  type PortfolioPerformancePeriod,
  type PortfolioPerformanceStatus,
  type PortfolioPosition,
  type PortfolioTradePost,
  type PortfolioTradePreview,
  type PortfolioTradeRequest,
  type PortfolioTradeSide,
  portfolioApi,
} from '@/lib/api';
import { createPortfolioClientRequestId } from '@/lib/portfolio-client-request-id';
import { cExitPresentation } from '@/lib/portfolio-exit-presentation';
import {
  CockpitPanel,
  StageBadge,
  StatCard,
  WorkbenchPage,
} from '@/components/workspace/CockpitPrimitives';

function CompactPortfolioPanel({ title, icon: Icon, children, extra }: ComponentProps<typeof CockpitPanel>) {
  return <section className="evo-panel min-w-0 overflow-hidden">
    <header className="evo-panel-header" style={{ minHeight: 34, padding: '4px 10px' }}>
      <div className="flex min-w-0 items-center gap-2"><Icon size={14} className="shrink-0 text-brand-blue-light" /><h2 className="truncate text-xs font-bold text-white">{title}</h2></div>
      {extra}
    </header>
    <div style={{ padding: 8 }}>{children}</div>
  </section>;
}

function PortfolioDisclosure({ title, icon: Icon, count, children }: {
  title: string;
  icon: typeof ShieldAlert;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <details className="evo-panel group mt-3 min-w-0 overflow-hidden">
      <summary className="evo-panel-header cursor-pointer list-none select-none hover:brightness-110 focus-visible:outline-2 focus-visible:outline-brand-blue [&::-webkit-details-marker]:hidden">
        <span className="flex min-w-0 items-center gap-2">
          <Icon size={14} className="shrink-0 text-brand-blue-light" />
          <span className="truncate text-[12px] font-black text-white">{title}</span>
          {count !== undefined && <span className="font-mono text-[10px] text-text-tertiary">{count}</span>}
        </span>
        <ChevronDown size={14} className="shrink-0 text-text-tertiary transition-transform group-open:rotate-180" />
      </summary>
      <div className="p-3">{children}</div>
    </details>
  );
}

function CompactAssetCard({ label, value, sub, tone = 'neutral' }: ComponentProps<typeof StatCard>) {
  return <div className="rounded-lg border border-border-primary bg-bg-secondary" style={{ minHeight: 0, padding: '6px 10px' }}>
    <div className="text-[10px] leading-tight text-text-tertiary">{label}</div>
    <div className={`mt-0.5 font-mono text-lg font-bold leading-tight ${tone === 'good' ? 'text-success' : tone === 'bad' ? 'text-danger' : 'text-white'}`}>{value}</div>
    {sub && <div className="text-[10px] leading-tight text-text-secondary">{sub}</div>}
  </div>;
}

type SizingMode = 'quantity' | 'amount' | 'target';

interface TradeDraft {
  businessType: PortfolioTradeSide;
  symbol: string;
  tradeDate: string;
  tradeTime: string;
  priceYuan: string;
  sizingMode: SizingMode;
  quantity: string;
  amountYuan: string;
  targetPercent: string;
  feeYuan: string;
  taxYuan: string;
}

interface CashFlowDraft {
  businessType: PortfolioCashFlowType;
  tradeDate: string;
  tradeTime: string;
  amountYuan: string;
  note: string;
}

interface PreviewSubmission {
  request: PortfolioTradeRequest;
  generation: number;
  fingerprint: string;
}

interface ExitPlanDraft {
  enabled: boolean;
  hardStopYuan: string;
  takeProfit1Yuan: string;
  takeProfit1Percent: string;
  takeProfit2Yuan: string;
  takeProfit2Percent: string;
  takeProfit3Yuan: string;
  takeProfit3Percent: string;
  trailingActivationPercent: string;
  trailingDistancePercent: string;
  timeStopTradingDays: string;
  timeStopMinReturnPercent: string;
  trendExitEnabled: boolean;
  intradayEnabled: boolean;
}

const portfolioKeys = {
  accounts: ['portfolio', 'accounts'] as const,
  positions: (accountId: number | undefined, asOfDate: string) =>
    ['portfolio', 'positions', accountId, asOfDate, 'ALL'] as const,
  transactions: (accountId: number | undefined) =>
    ['portfolio', 'transactions', 'statement', accountId] as const,
  performanceSnapshots: (accountId: number | undefined) =>
    ['portfolio', 'performance', 'snapshots', accountId] as const,
  performanceSummary: (
    accountId: number | undefined,
    asOfDate: string,
  ) => ['portfolio', 'performance', 'summary', accountId, asOfDate] as const,
  exitPlans: (accountId: number | undefined) =>
    ['portfolio', 'exit-plans', accountId] as const,
  exitStates: (accountId: number | undefined) =>
    ['portfolio', 'exit-states', accountId] as const,
  exitEvents: (accountId: number | undefined) =>
    ['portfolio', 'exit-events', accountId, 'ALL'] as const,
};

const performancePeriods: Array<{
  period: PortfolioPerformancePeriod;
  label: string;
}> = [
  { period: 'DAY', label: '每日' },
  { period: 'WEEK', label: '本周' },
  { period: 'MONTH', label: '本月' },
  { period: 'YEAR', label: '本年' },
];

function shanghaiToday() {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function formatCents(value: number | null | undefined, signed = false) {
  if (value === null || value === undefined || !Number.isSafeInteger(value)) {
    return '-';
  }
  const yuan = value / 100;
  const prefix = signed && yuan > 0 ? '+' : '';
  return `${prefix}¥${yuan.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatMicros(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isSafeInteger(value)) {
    return '-';
  }
  return `¥${(value / 1_000_000).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })}`;
}

function formatBps(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isSafeInteger(value)) {
    return '-';
  }
  const percent = value / 100;
  return `${percent > 0 ? '+' : ''}${percent.toFixed(2)}%`;
}

function formatPpb(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isSafeInteger(value)) {
    return '-';
  }
  const percent = value / 10_000_000;
  return `${percent > 0 ? '+' : ''}${percent.toFixed(2)}%`;
}

function performanceStatusText(
  status: PortfolioPerformanceStatus,
  hasNullValue = false,
) {
  if (hasNullValue && status === 'COMPLETE') return '数据尚未计算';
  return {
    EMPTY: '暂无已生成快照',
    INITIAL: '基准日，暂无可比收益',
    COMPLETE: '统计完整',
    GAP: '快照不连续，收益不可比',
    UNAVAILABLE: '缺少行情，收益不可用',
  }[status];
}

function priceStatusText(status: 'COMPLETE' | 'PARTIAL') {
  return status === 'COMPLETE' ? '行情完整' : '行情不完整';
}

function parseYuan(value: string, field: string, allowZero = false) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || (allowZero ? parsed < 0 : parsed <= 0)) {
    throw new Error(`${field}必须是${allowZero ? '非负' : '正'}数`);
  }
  const cents = Math.round(parsed * 100);
  if (
    !Number.isSafeInteger(cents)
    || (allowZero ? cents < 0 : cents <= 0)
  ) {
    throw new Error(`${field}超出安全记账范围`);
  }
  return cents;
}

function parsePriceMicros(value: string) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error('成交价格必须是正数');
  }
  const micros = Math.round(parsed * 1_000_000);
  if (!Number.isSafeInteger(micros) || micros <= 0) {
    throw new Error('成交价格超出安全记账范围');
  }
  return micros;
}

function optionalPriceMicros(value: string, field: string) {
  if (!value.trim()) return null;
  try {
    return parsePriceMicros(value);
  } catch {
    throw new Error(`${field}必须是有效正数，最多保留微元精度`);
  }
}

function optionalPercentBps(
  value: string,
  field: string,
  minimum: number,
  maximum: number,
) {
  if (!value.trim()) return null;
  const percent = Number(value);
  if (!Number.isFinite(percent) || percent < minimum || percent > maximum) {
    throw new Error(`${field}必须在 ${minimum}% 到 ${maximum}% 之间`);
  }
  const bps = Math.round(percent * 100);
  if (!Number.isSafeInteger(bps)) throw new Error(`${field}超出安全范围`);
  return bps;
}

function optionalPositiveInteger(value: string, field: string, maximum: number) {
  if (!value.trim()) return null;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > maximum) {
    throw new Error(`${field}必须是 1 到 ${maximum} 的整数`);
  }
  return parsed;
}

function microsDraft(value: number | null | undefined) {
  return value === null || value === undefined ? '' : String(value / 1_000_000);
}

function bpsDraft(value: number | null | undefined) {
  return value === null || value === undefined ? '' : String(value / 100);
}

function exitPlanDraft(plan?: PortfolioExitPlan): ExitPlanDraft {
  return {
    enabled: plan?.enabled ?? true,
    hardStopYuan: microsDraft(plan?.hard_stop_micros),
    takeProfit1Yuan: microsDraft(plan?.take_profit_1_micros),
    takeProfit1Percent: bpsDraft(plan?.take_profit_1_reduce_bps),
    takeProfit2Yuan: microsDraft(plan?.take_profit_2_micros),
    takeProfit2Percent: bpsDraft(plan?.take_profit_2_reduce_bps),
    takeProfit3Yuan: microsDraft(plan?.take_profit_3_micros),
    takeProfit3Percent: bpsDraft(plan?.take_profit_3_reduce_bps),
    trailingActivationPercent: bpsDraft(plan?.trailing_activation_bps),
    trailingDistancePercent: bpsDraft(plan?.trailing_distance_bps),
    timeStopTradingDays:
      plan?.time_stop_trading_days === null
      || plan?.time_stop_trading_days === undefined
        ? ''
        : String(plan.time_stop_trading_days),
    timeStopMinReturnPercent: bpsDraft(plan?.time_stop_min_return_bps),
    trendExitEnabled: plan?.trend_exit_enabled ?? true,
    intradayEnabled: plan?.intraday_enabled ?? true,
  };
}

function buildExitPlanUpdate(
  draft: ExitPlanDraft,
  positionRevision: number,
): PortfolioExitPlanUpdate {
  const hardStop = optionalPriceMicros(draft.hardStopYuan, '硬止损价');
  const takeProfitPrices = [
    optionalPriceMicros(draft.takeProfit1Yuan, '第一档止盈价'),
    optionalPriceMicros(draft.takeProfit2Yuan, '第二档止盈价'),
    optionalPriceMicros(draft.takeProfit3Yuan, '第三档止盈价'),
  ];
  const takeProfitReductions = [
    optionalPercentBps(draft.takeProfit1Percent, '第一档减仓比例', 0.01, 100),
    optionalPercentBps(draft.takeProfit2Percent, '第二档减仓比例', 0.01, 100),
    optionalPercentBps(draft.takeProfit3Percent, '第三档减仓比例', 0.01, 100),
  ];
  takeProfitPrices.forEach((price, index) => {
    if ((price === null) !== (takeProfitReductions[index] === null)) {
      throw new Error(`第 ${index + 1} 档止盈价和减仓比例必须同时填写`);
    }
  });
  const configuredPrices = takeProfitPrices.filter(
    (price): price is number => price !== null,
  );
  if (configuredPrices.some((price, index) =>
    index > 0 && price <= configuredPrices[index - 1])) {
    throw new Error('止盈价格必须按第一档到第三档严格递增');
  }
  const totalReduction = takeProfitReductions.reduce<number>(
    (total, reduction) => total + (reduction ?? 0),
    0,
  );
  if (totalReduction > 10_000) throw new Error('三档减仓比例合计不能超过 100%');

  const trailingActivation = optionalPercentBps(
    draft.trailingActivationPercent,
    '移动止损启动收益',
    0.01,
    1_000,
  );
  const trailingDistance = optionalPercentBps(
    draft.trailingDistancePercent,
    '移动止损回撤比例',
    0.01,
    100,
  );
  if ((trailingActivation === null) !== (trailingDistance === null)) {
    throw new Error('移动止损启动收益和回撤比例必须同时填写');
  }
  const timeStopDays = optionalPositiveInteger(
    draft.timeStopTradingDays,
    '时间止损交易日',
    10_000,
  );
  const timeStopReturn = optionalPercentBps(
    draft.timeStopMinReturnPercent,
    '时间止损最低收益',
    -100,
    1_000,
  );
  if ((timeStopDays === null) !== (timeStopReturn === null)) {
    throw new Error('时间止损交易日和最低收益必须同时填写');
  }
  if (!Number.isSafeInteger(positionRevision) || positionRevision <= 0) {
    throw new Error('持仓版本无效，请刷新后重试');
  }
  return {
    position_revision: positionRevision,
    enabled: draft.enabled,
    hard_stop_micros: hardStop,
    take_profit_1_micros: takeProfitPrices[0],
    take_profit_1_reduce_bps: takeProfitReductions[0],
    take_profit_2_micros: takeProfitPrices[1],
    take_profit_2_reduce_bps: takeProfitReductions[1],
    take_profit_3_micros: takeProfitPrices[2],
    take_profit_3_reduce_bps: takeProfitReductions[2],
    trailing_activation_bps: trailingActivation,
    trailing_distance_bps: trailingDistance,
    time_stop_trading_days: timeStopDays,
    time_stop_min_return_bps: timeStopReturn,
    trend_exit_enabled: draft.trendExitEnabled,
    intraday_enabled: draft.intradayEnabled,
  };
}

function eventSource(event: PortfolioExitEvent) {
  if (!event.payload_json) return 'daily';
  try {
    const parsed: unknown = JSON.parse(event.payload_json);
    if (
      typeof parsed === 'object'
      && parsed !== null
      && 'source' in parsed
      && typeof parsed.source === 'string'
    ) {
      return parsed.source;
    }
  } catch {
    return 'unknown';
  }
  return 'daily';
}

function exitEventText(eventType: string) {
  return {
    HARD_STOP_TRIGGERED: '硬止损',
    TAKE_PROFIT_TRIGGERED: '止盈减仓',
    TRAILING_STOP_TRIGGERED: '移动止损',
    TIME_STOP_TRIGGERED: '时间止损',
    TREND_EXIT_TRIGGERED: '趋势走弱',
    TREND_EXIT_WATCH: '趋势观察',
    EXIT_DATA_BLOCKED: '行情阻断',
    EXIT_DATA_RECOVERED: '行情恢复',
  }[eventType] ?? eventType;
}

function exitSeverityTone(severity: string) {
  if (severity === 'CRITICAL' || severity === 'HIGH') return 'text-danger';
  if (severity === 'WARNING' || severity === 'MEDIUM') return 'text-warning';
  return 'text-brand-blue-light';
}

function safeSum(values: Array<number | null | undefined>) {
  let total = 0;
  for (const value of values) {
    if (value === null || value === undefined || !Number.isSafeInteger(value)) {
      return null;
    }
    total += value;
    if (!Number.isSafeInteger(total)) return null;
  }
  return total;
}

function requestFingerprint(request: PortfolioTradeRequest, tradeTime: string) {
  return JSON.stringify({ request, tradeTime });
}

function assertSafePreview(preview: PortfolioTradePreview) {
  if (
    !Number.isSafeInteger(preview.ledger_revision)
    || preview.ledger_revision < 0
    || !Number.isSafeInteger(preview.quantity)
    || preview.quantity <= 0
    || !Number.isSafeInteger(preview.gross_amount_cents)
    || preview.gross_amount_cents <= 0
    || !Number.isSafeInteger(preview.cash_delta_cents)
  ) {
    throw new Error('预览结果超出浏览器安全整数范围');
  }
}

function errorMessage(error: unknown) {
  if (axios.isAxiosError<{ detail?: string }>(error)) {
    return error.response?.data?.detail || error.message || '请求失败';
  }
  return error instanceof Error ? error.message : '请求失败';
}

function pnlTone(value: number | null) {
  if (value === null || value === 0) return 'text-text-secondary';
  return value > 0 ? 'text-success' : 'text-danger';
}

export default function PortfolioPage() {
  const today = useMemo(shanghaiToday, []);
  const queryClient = useQueryClient();
  const clientRequestIdRef = useRef<string | null>(null);
  const cashFlowRequestIdRef = useRef<string | null>(null);
  const previewGenerationRef = useRef(0);
  const initialSearchHandledRef = useRef(false);
  const [selectedAccountId, setSelectedAccountId] = useState<number>();
  const [formError, setFormError] = useState('');
  const [tradeSuccess, setTradeSuccess] = useState('');
  const [bookingOpen, setBookingOpen] = useState(false);
  const [holdingsTab, setHoldingsTab] = useState<'positions' | 'statement'>('positions');
  const [bookingTab, setBookingTab] = useState<'screenshot' | 'manual' | 'cash'>('screenshot');
  const [accountSettingsOpen, setAccountSettingsOpen] = useState(false);
  const [newAccountOpen, setNewAccountOpen] = useState(false);
  const [accountSuccess, setAccountSuccess] = useState('');
  const [accountError, setAccountError] = useState('');
  const [cashFlowError, setCashFlowError] = useState('');
  const [cashFlowSuccess, setCashFlowSuccess] = useState('');
  const [preview, setPreview] = useState<PortfolioTradePreview | null>(null);
  const [previewRequest, setPreviewRequest] = useState<PortfolioTradeRequest | null>(
    null,
  );
  const [previewFingerprint, setPreviewFingerprint] = useState<string | null>(null);
  const [exitPlanDrafts, setExitPlanDrafts] = useState<
    Record<string, ExitPlanDraft>
  >({});
  const [expandedExitPlanSymbols, setExpandedExitPlanSymbols] = useState<Set<string>>(
    () => new Set(),
  );
  const [exitPlanErrors, setExitPlanErrors] = useState<Record<string, string>>({});
  const [exitActionError, setExitActionError] = useState('');
  const [accountDraft, setAccountDraft] = useState({
    name: '',
    brokerName: '',
    accountType: 'CASH' as PortfolioAccountCreate['account_type'],
    openingCashYuan: '0',
  });
  const [tradeDraft, setTradeDraft] = useState<TradeDraft>({
    businessType: 'BUY',
    symbol: '',
    tradeDate: today,
    tradeTime: '15:00',
    priceYuan: '',
    sizingMode: 'quantity',
    quantity: '100',
    amountYuan: '',
    targetPercent: '',
    feeYuan: '0',
    taxYuan: '0',
  });
  const [cashFlowDraft, setCashFlowDraft] = useState<CashFlowDraft>({
    businessType: 'DEPOSIT',
    tradeDate: today,
    tradeTime: '15:00',
    amountYuan: '',
    note: '',
  });

  useEffect(() => {
    if (initialSearchHandledRef.current) return;
    initialSearchHandledRef.current = true;

    const search = window.location.search;
    if (!search) return;

    const params = new URLSearchParams(search);
    const symbol = params.get('symbol')?.trim() ?? '';
    const side = params.get('side');
    const price = params.get('price')?.trim() ?? '';
    const validSymbol = /^\d{6}$/.test(symbol);
    const validSide = side === 'BUY' || side === 'SELL';
    let validPrice = false;

    if (price) {
      try {
        parsePriceMicros(price);
        validPrice = true;
      } catch {
        validPrice = false;
      }
    }

    if (validSymbol || validSide || validPrice) {
      setTradeDraft((current) => ({
        ...current,
        ...(validSymbol ? { symbol } : {}),
        ...(validSide ? { businessType: side } : {}),
        ...(validPrice ? { priceYuan: price } : {}),
      }));
      previewGenerationRef.current += 1;
      setPreview(null);
      setPreviewRequest(null);
      setPreviewFingerprint(null);
      clientRequestIdRef.current = null;
    }

    window.history.replaceState(
      window.history.state,
      '',
      `${window.location.pathname}${window.location.hash}`,
    );
  }, []);

  const accountsQuery = useQuery({
    queryKey: portfolioKeys.accounts,
    queryFn: portfolioApi.getAccounts,
  });
  const accounts = accountsQuery.data ?? [];
  const selectedAccount =
    accounts.find((account) => account.id === selectedAccountId)
    ?? accounts.find((account) => account.is_default)
    ?? accounts[0];
  const activeAccountId = selectedAccount?.id;

  const positionsQuery = useQuery({
    queryKey: portfolioKeys.positions(activeAccountId, today),
    queryFn: () => portfolioApi.getPositions(activeAccountId!, today, 'ALL'),
    enabled: activeAccountId !== undefined,
  });
  const transactionsQuery = useQuery({
    queryKey: portfolioKeys.transactions(activeAccountId),
    queryFn: () => portfolioApi.getStatement(activeAccountId!),
    enabled: activeAccountId !== undefined,
  });
  const performanceSnapshotsQuery = useQuery({
    queryKey: portfolioKeys.performanceSnapshots(activeAccountId),
    queryFn: () => portfolioApi.getPerformanceSnapshots(activeAccountId!, 400),
    enabled: activeAccountId !== undefined,
  });
  const performanceSummaryQuery = useQuery({
    queryKey: portfolioKeys.performanceSummary(activeAccountId, today),
    queryFn: () => portfolioApi.getPerformanceSummary(activeAccountId!, today),
    enabled: activeAccountId !== undefined,
  });
  const exitPlansQuery = useQuery({
    queryKey: portfolioKeys.exitPlans(activeAccountId),
    queryFn: () => portfolioApi.getExitPlans(activeAccountId!),
    enabled: activeAccountId !== undefined,
  });
  const exitStatesQuery = useQuery({
    queryKey: portfolioKeys.exitStates(activeAccountId),
    queryFn: () => portfolioApi.getExitStates(activeAccountId!),
    enabled: activeAccountId !== undefined,
  });
  const exitEventsQuery = useQuery({
    queryKey: portfolioKeys.exitEvents(activeAccountId),
    queryFn: () => portfolioApi.getExitEvents(activeAccountId!, 'ALL'),
    enabled: activeAccountId !== undefined,
  });
  const positions = useMemo(
    () => positionsQuery.data ?? [],
    [positionsQuery.data],
  );
  const heldPositions = useMemo(
    () => positions.filter((position) => position.quantity > 0),
    [positions],
  );
  const transactions = transactionsQuery.data?.transactions ?? [];
  const exitPlans = useMemo(() => exitPlansQuery.data ?? [], [exitPlansQuery.data]);
  const exitStates = useMemo(
    () => exitStatesQuery.data ?? [],
    [exitStatesQuery.data],
  );
  const exitEvents = useMemo(
    () => exitEventsQuery.data ?? [],
    [exitEventsQuery.data],
  );
  const openPositions = useMemo(
    () => positions.filter((position) => position.status === 'OPEN'),
    [positions],
  );
  const performanceSnapshots = useMemo(
    () => performanceSnapshotsQuery.data ?? [],
    [performanceSnapshotsQuery.data],
  );
  useEffect(() => {
    if (!activeAccountId || positionsQuery.isLoading || exitPlansQuery.isLoading) return;
    const plansBySymbol = new Map(exitPlans.map((plan) => [plan.symbol, plan]));
    setExitPlanDrafts((current) => {
      const next: Record<string, ExitPlanDraft> = {};
      for (const position of openPositions) {
        next[position.symbol] =
          current[position.symbol] ?? exitPlanDraft(plansBySymbol.get(position.symbol));
      }
      return next;
    });
  }, [
    activeAccountId,
    exitPlans,
    exitPlansQuery.isLoading,
    openPositions,
    positionsQuery.isLoading,
  ]);

  const summary = useMemo(() => {
    const valuedPositions = positions.filter(
      (position) => position.market_value_cents !== null,
    );
    const marketValueCents = safeSum(
      valuedPositions.map((position) => position.market_value_cents),
    );
    const unrealizedPnlCents = safeSum(
      valuedPositions.map((position) => position.unrealized_pnl_cents),
    );
    const valuedCostCents = safeSum(
      valuedPositions.map((position) => position.cost_basis_cents),
    );
    const missingPriceCount = positions.filter(
      (position) => position.quantity > 0 && position.market_value_cents === null,
    ).length;
    const rawCashCents = selectedAccount?.cash_cents ?? 0;
    const cashCents = Number.isSafeInteger(rawCashCents) ? rawCashCents : null;
    const totalAssetCents =
      cashCents !== null && marketValueCents !== null
        ? safeSum([cashCents, marketValueCents])
        : null;
    const unrealizedPnlBps =
      unrealizedPnlCents !== null
      && valuedCostCents !== null
      && valuedCostCents > 0
        ? Math.round(unrealizedPnlCents / valuedCostCents * 10_000)
        : null;
    return {
      cashCents,
      marketValueCents,
      totalAssetCents,
      unrealizedPnlCents,
      unrealizedPnlBps: Number.isSafeInteger(unrealizedPnlBps)
        ? unrealizedPnlBps
        : null,
      missingPriceCount,
    };
  }, [positions, selectedAccount?.cash_cents]);

  const invalidatePreview = (clearError = true) => {
    previewGenerationRef.current += 1;
    setPreview(null);
    setPreviewRequest(null);
    setPreviewFingerprint(null);
    clientRequestIdRef.current = null;
    setTradeSuccess('');
    if (clearError) setFormError('');
  };

  const updateTrade = <Key extends keyof TradeDraft>(
    key: Key,
    value: TradeDraft[Key],
  ) => {
    setTradeDraft((current) => ({ ...current, [key]: value }));
    invalidatePreview();
  };

  const buildTradeRequest = (): PortfolioTradeRequest => {
    if (!activeAccountId) throw new Error('请先选择账户');
    const symbol = tradeDraft.symbol.trim();
    if (!/^[0-9]{6}$/.test(symbol)) throw new Error('股票代码必须是6位数字');
    if (!tradeDraft.tradeDate || tradeDraft.tradeDate > today) {
      throw new Error('成交日期不能晚于今天');
    }
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(tradeDraft.tradeTime)) {
      throw new Error('成交时间必须是有效的 HH:mm');
    }
    const request: PortfolioTradeRequest = {
      account_id: activeAccountId,
      business_type: tradeDraft.businessType,
      trade_date: tradeDraft.tradeDate,
      symbol,
      price_micros: parsePriceMicros(tradeDraft.priceYuan),
      fee_cents: parseYuan(tradeDraft.feeYuan || '0', '手续费', true),
      tax_cents: parseYuan(tradeDraft.taxYuan || '0', '税费', true),
    };
    if (tradeDraft.sizingMode === 'quantity') {
      const quantity = Number(tradeDraft.quantity);
      if (!Number.isSafeInteger(quantity) || quantity <= 0) {
        throw new Error('股数必须是正整数');
      }
      if (tradeDraft.businessType === 'BUY' && quantity % 100 !== 0) {
        throw new Error('买入股数必须是100股的整数倍');
      }
      request.quantity = quantity;
    } else if (tradeDraft.sizingMode === 'amount') {
      request.amount_cents = parseYuan(tradeDraft.amountYuan, '交易金额');
    } else {
      const percent = Number(tradeDraft.targetPercent);
      if (!Number.isFinite(percent) || percent <= 0 || percent > 100) {
        throw new Error('目标仓位必须在0%到100%之间');
      }
      if (summary.missingPriceCount > 0) {
        throw new Error('存在缺行情持仓，暂不能使用目标仓位换算');
      }
      if (
        summary.totalAssetCents === null
        || !Number.isSafeInteger(summary.totalAssetCents)
        || summary.totalAssetCents <= 0
      ) {
        throw new Error('账户可估值资产不完整或超出安全范围');
      }
      request.target_position_bps = Math.round(percent * 100);
      request.account_equity_cents = summary.totalAssetCents;
    }
    return request;
  };

  const refreshPortfolioQueries = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: portfolioKeys.accounts }),
      queryClient.invalidateQueries({ queryKey: ['portfolio', 'positions'] }),
      queryClient.invalidateQueries({ queryKey: ['portfolio', 'transactions'] }),
      queryClient.invalidateQueries({ queryKey: ['portfolio', 'performance'] }),
    ]);
  };

  const createAccountMutation = useMutation({
    mutationFn: portfolioApi.createAccount,
    onSuccess: async (account) => {
      invalidatePreview();
      setSelectedAccountId(account.id);
      setAccountDraft({
        name: '',
        brokerName: '',
        accountType: 'CASH',
        openingCashYuan: '0',
      });
      setFormError('');
      setAccountError('');
      setAccountSuccess(`已创建账户：${account.name}`);
      setNewAccountOpen(false);
      await refreshPortfolioQueries();
    },
    onError: (error) => {
      setAccountSuccess('');
      setAccountError(errorMessage(error));
    },
  });

  const cashFlowMutation = useMutation({
    mutationFn: ({
      accountId,
      payload,
    }: {
      accountId: number;
      payload: PortfolioCashFlowRequest;
    }) => portfolioApi.postCashFlow(accountId, payload),
    onSuccess: async (result) => {
      const flowLabel = result.transaction.business_type === 'DEPOSIT'
        ? '入金'
        : '出金';
      setCashFlowError('');
      setCashFlowSuccess(
        `${flowLabel}已记账：${formatCents(result.transaction.gross_amount_cents)}，现金余额 ${formatCents(result.cash_after_cents)}`,
      );
      setCashFlowDraft((current) => ({
        ...current,
        amountYuan: '',
        note: '',
      }));
      cashFlowRequestIdRef.current = null;
      await refreshPortfolioQueries();
    },
    onError: (error) => {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        cashFlowRequestIdRef.current = null;
      }
      setCashFlowSuccess('');
      setCashFlowError(errorMessage(error));
    },
  });

  const previewMutation = useMutation({
    mutationFn: (submission: PreviewSubmission) =>
      portfolioApi.previewTrade(submission.request),
    onSuccess: (result, submission) => {
      if (submission.generation !== previewGenerationRef.current) return;
      try {
        assertSafePreview(result);
        const currentRequest = buildTradeRequest();
        const currentFingerprint = requestFingerprint(
          currentRequest,
          tradeDraft.tradeTime,
        );
        if (currentFingerprint !== submission.fingerprint) {
          invalidatePreview(false);
          return;
        }
      } catch (error) {
        invalidatePreview(false);
        setFormError(errorMessage(error));
        return;
      }
      setPreview(result);
      setPreviewRequest(submission.request);
      setPreviewFingerprint(submission.fingerprint);
      clientRequestIdRef.current = createPortfolioClientRequestId();
      setFormError('');
    },
    onError: (error, submission) => {
      if (submission.generation === previewGenerationRef.current) {
        setFormError(errorMessage(error));
      }
    },
  });

  const postTradeMutation = useMutation({
    mutationFn: portfolioApi.postTrade,
    onSuccess: async (transaction) => {
      const quantity = transaction.quantity?.toLocaleString('zh-CN') ?? '—';
      const successMessage = `已记账：${transaction.business_type === 'BUY' ? '买入' : '卖出'} ${transaction.symbol} ${quantity} 股`;
      invalidatePreview();
      setTradeSuccess(successMessage);
      await refreshPortfolioQueries();
    },
    onError: (error) => {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        invalidatePreview(false);
      }
      setFormError(errorMessage(error));
    },
  });

  const saveExitPlanMutation = useMutation({
    mutationFn: ({
      accountId,
      symbol,
      payload,
    }: {
      accountId: number;
      symbol: string;
      payload: PortfolioExitPlanUpdate;
    }) => portfolioApi.updateExitPlan(accountId, symbol, payload),
    onSuccess: async (plan, variables) => {
      if (variables.accountId !== activeAccountId) return;
      setExitPlanDrafts((current) => ({
        ...current,
        [plan.symbol]: exitPlanDraft(plan),
      }));
      setExitPlanErrors((current) => ({ ...current, [plan.symbol]: '' }));
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: portfolioKeys.exitPlans(variables.accountId),
        }),
        queryClient.invalidateQueries({
          queryKey: portfolioKeys.exitStates(variables.accountId),
        }),
      ]);
    },
    onError: (error, variables) => {
      if (variables.accountId !== activeAccountId) return;
      setExitPlanErrors((current) => ({
        ...current,
        [variables.symbol]: errorMessage(error),
      }));
    },
  });

  const exitEventActionMutation = useMutation({
    mutationFn: ({
      eventId,
      action,
    }: {
      eventId: number;
      action: 'acknowledge' | 'resolve';
      accountId: number;
    }) => action === 'acknowledge'
      ? portfolioApi.acknowledgeExitEvent(eventId)
      : portfolioApi.resolveExitEvent(eventId),
    onSuccess: async (_event, variables) => {
      if (variables.accountId !== activeAccountId) return;
      setExitActionError('');
      await queryClient.invalidateQueries({
        queryKey: portfolioKeys.exitEvents(variables.accountId),
      });
    },
    onError: (error, variables) => {
      if (variables.accountId === activeAccountId) {
        setExitActionError(errorMessage(error));
      }
    },
  });

  const updateExitDraft = <Key extends keyof ExitPlanDraft>(
    symbol: string,
    key: Key,
    value: ExitPlanDraft[Key],
  ) => {
    setExitPlanDrafts((current) => ({
      ...current,
      [symbol]: {
        ...(current[symbol] ?? exitPlanDraft()),
        [key]: value,
      },
    }));
    setExitPlanErrors((current) => ({ ...current, [symbol]: '' }));
  };

  const handleSaveExitPlan = (position: PortfolioPosition) => {
    if (!activeAccountId) return;
    try {
      const draft = exitPlanDrafts[position.symbol] ?? exitPlanDraft();
      const payload = buildExitPlanUpdate(draft, position.position_revision);
      saveExitPlanMutation.mutate({
        accountId: activeAccountId,
        symbol: position.symbol,
        payload,
      });
    } catch (error) {
      setExitPlanErrors((current) => ({
        ...current,
        [position.symbol]: errorMessage(error),
      }));
    }
  };

  const handleCreateAccount = () => {
    try {
      const name = accountDraft.name.trim();
      if (!name) throw new Error('请输入账户名称');
      const openingCashCents = parseYuan(
        accountDraft.openingCashYuan || '0',
        '初始现金',
        true,
      );
      createAccountMutation.mutate({
        name,
        broker_name: accountDraft.brokerName.trim() || undefined,
        account_type: accountDraft.accountType,
        currency: 'CNY',
        opening_cash_cents: openingCashCents,
        is_default: accounts.length === 0,
      });
      setAccountError('');
      setAccountSuccess('');
    } catch (error) {
      setAccountSuccess('');
      setAccountError(errorMessage(error));
    }
  };

  const updateCashFlow = <Key extends keyof CashFlowDraft>(
    key: Key,
    value: CashFlowDraft[Key],
  ) => {
    setCashFlowDraft((current) => ({ ...current, [key]: value }));
    setCashFlowError('');
    setCashFlowSuccess('');
    cashFlowRequestIdRef.current = null;
  };

  const handleCashFlow = () => {
    try {
      if (!selectedAccount || selectedAccount.status !== 'ACTIVE') {
        throw new Error('请先选择一个有效账户');
      }
      if (
        !cashFlowDraft.tradeDate
        || cashFlowDraft.tradeDate > today
      ) {
        throw new Error('资金日期不能晚于今天');
      }
      const dateValue = new Date(`${cashFlowDraft.tradeDate}T12:00:00+08:00`);
      if (
        Number.isNaN(dateValue.getTime())
        || dateValue.getUTCDay() === 0
        || dateValue.getUTCDay() === 6
      ) {
        throw new Error('资金日期必须是工作日');
      }
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(cashFlowDraft.tradeTime)) {
        throw new Error('资金时间必须是有效的 HH:mm');
      }
      const amountCents = parseYuan(cashFlowDraft.amountYuan, '资金金额');
      if (
        cashFlowDraft.businessType === 'WITHDRAWAL'
        && selectedAccount.cash_cents < amountCents
      ) {
        throw new Error('出金金额不能超过当前现金余额');
      }
      if (!cashFlowRequestIdRef.current) {
        cashFlowRequestIdRef.current = createPortfolioClientRequestId();
      }
      const payload: PortfolioCashFlowRequest = {
        client_request_id: cashFlowRequestIdRef.current,
        business_type: cashFlowDraft.businessType,
        trade_date: cashFlowDraft.tradeDate,
        occurred_at: `${cashFlowDraft.tradeDate}T${cashFlowDraft.tradeTime}:00+08:00`,
        amount_cents: amountCents,
        expected_ledger_revision: selectedAccount.ledger_revision,
        ...(cashFlowDraft.note.trim()
          ? { note: cashFlowDraft.note.trim() }
          : {}),
      };
      setCashFlowError('');
      cashFlowMutation.mutate({
        accountId: selectedAccount.id,
        payload,
      });
    } catch (error) {
      cashFlowRequestIdRef.current = null;
      setCashFlowSuccess('');
      setCashFlowError(errorMessage(error));
    }
  };

  const handlePreview = () => {
    try {
      const request = buildTradeRequest();
      const generation = previewGenerationRef.current + 1;
      previewGenerationRef.current = generation;
      setPreview(null);
      setPreviewRequest(null);
      setPreviewFingerprint(null);
      clientRequestIdRef.current = null;
      const fingerprint = requestFingerprint(request, tradeDraft.tradeTime);
      previewMutation.mutate({ request, generation, fingerprint });
      setFormError('');
    } catch (error) {
      setFormError(errorMessage(error));
    }
  };

  const handleConfirm = () => {
    if (!preview || !previewRequest || !previewFingerprint) return;
    let currentRequest: PortfolioTradeRequest;
    try {
      currentRequest = buildTradeRequest();
    } catch (error) {
      invalidatePreview(false);
      setFormError(errorMessage(error));
      return;
    }
    const currentFingerprint = requestFingerprint(
      currentRequest,
      tradeDraft.tradeTime,
    );
    if (currentFingerprint !== previewFingerprint) {
      invalidatePreview(false);
      setFormError('交易信息已变化，请重新预览后再确认记账');
      return;
    }
    if (!clientRequestIdRef.current) {
      clientRequestIdRef.current = createPortfolioClientRequestId();
    }
    const payload: PortfolioTradePost = {
      ...previewRequest,
      client_request_id: clientRequestIdRef.current,
      occurred_at: `${previewRequest.trade_date}T${tradeDraft.tradeTime}:00+08:00`,
      expected_ledger_revision: preview.ledger_revision,
      expected_quantity: preview.quantity,
      expected_gross_amount_cents: preview.gross_amount_cents,
      expected_cash_delta_cents: preview.cash_delta_cents,
    };
    postTradeMutation.mutate(payload);
  };

  const refresh = () => {
    void Promise.all([
      refreshPortfolioQueries(),
      queryClient.invalidateQueries({ queryKey: ['portfolio', 'exit-plans'] }),
      queryClient.invalidateQueries({ queryKey: ['portfolio', 'exit-states'] }),
      queryClient.invalidateQueries({ queryKey: ['portfolio', 'exit-events'] }),
    ]);
  };

  const requestError = formError;

  const manualBookingContent = (
        <CockpitPanel title="手工确认交易" icon={Scale}>
          {!selectedAccount ? (
            <div className="py-8 text-center text-xs text-text-secondary">
              请先创建或选择账户
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2">
                {(['BUY', 'SELL'] as PortfolioTradeSide[]).map((side) => (
                  <button
                    key={side}
                    type="button"
                    onClick={() => updateTrade('businessType', side)}
                    className={`rounded-lg border px-3 py-2 text-xs font-black ${
                      tradeDraft.businessType === side
                        ? side === 'BUY'
                          ? 'border-success bg-success/10 text-success'
                          : 'border-danger bg-danger/10 text-danger'
                        : 'border-border-primary bg-bg-primary text-text-secondary'
                    }`}
                  >
                    {side === 'BUY' ? '买入' : '卖出'}
                  </button>
                ))}
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <label className="text-[10px] text-text-secondary">
                  股票代码
                  <input
                    value={tradeDraft.symbol}
                    maxLength={6}
                    onChange={(event) => updateTrade('symbol', event.target.value)}
                    placeholder="600519"
                    className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                  />
                </label>
                <label className="text-[10px] text-text-secondary">
                  成交日期
                  <input
                    type="date"
                    max={today}
                    value={tradeDraft.tradeDate}
                    onChange={(event) =>
                      updateTrade('tradeDate', event.target.value)
                    }
                    className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 text-xs text-white"
                  />
                </label>
                <label className="text-[10px] text-text-secondary">
                  成交时间
                  <input
                    type="time"
                    step="60"
                    value={tradeDraft.tradeTime}
                    onChange={(event) =>
                      updateTrade('tradeTime', event.target.value)
                    }
                    className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                  />
                </label>
                <label className="text-[10px] text-text-secondary">
                  {tradeDraft.businessType === 'BUY' ? '买入' : '卖出'}价格（元）
                  <input
                    type="number"
                    min="0"
                    step="0.0001"
                    value={tradeDraft.priceYuan}
                    onChange={(event) =>
                      updateTrade('priceYuan', event.target.value)
                    }
                    className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                  />
                </label>
                <label className="text-[10px] text-text-secondary">
                  仓位输入方式
                  <select
                    value={tradeDraft.sizingMode}
                    onChange={(event) =>
                      updateTrade('sizingMode', event.target.value as SizingMode)
                    }
                    className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 text-xs text-white"
                  >
                    <option value="quantity">股数</option>
                    <option value="amount">金额</option>
                    <option
                      value="target"
                      disabled={summary.missingPriceCount > 0}
                    >
                      目标仓位
                    </option>
                  </select>
                </label>
                {tradeDraft.sizingMode === 'quantity' && (
                  <label className="text-[10px] text-text-secondary">
                    股数
                    <input
                      type="number"
                      min="1"
                      step="1"
                      value={tradeDraft.quantity}
                      onChange={(event) =>
                        updateTrade('quantity', event.target.value)
                      }
                      className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                    />
                  </label>
                )}
                {tradeDraft.sizingMode === 'amount' && (
                  <label className="text-[10px] text-text-secondary">
                    交易金额（元）
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={tradeDraft.amountYuan}
                      onChange={(event) =>
                        updateTrade('amountYuan', event.target.value)
                      }
                      className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                    />
                  </label>
                )}
                {tradeDraft.sizingMode === 'target' && (
                  <label className="text-[10px] text-text-secondary">
                    目标仓位（%）
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="0.01"
                      value={tradeDraft.targetPercent}
                      onChange={(event) =>
                        updateTrade('targetPercent', event.target.value)
                      }
                      className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                    />
                  </label>
                )}
                <label className="text-[10px] text-text-secondary">
                  手续费（元）
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={tradeDraft.feeYuan}
                    onChange={(event) =>
                      updateTrade('feeYuan', event.target.value)
                    }
                    className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                  />
                </label>
                <label className="text-[10px] text-text-secondary">
                  税费（元）
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={tradeDraft.taxYuan}
                    onChange={(event) =>
                      updateTrade('taxYuan', event.target.value)
                    }
                    className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                  />
                </label>
              </div>

              {requestError && (
                <div className="mt-3 rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
                  {requestError}
                </div>
              )}

              {tradeSuccess && (
                <div className="mt-3 rounded-lg border border-success/40 bg-success/10 p-3 text-xs text-success">
                  {tradeSuccess}
                </div>
              )}

              {preview && (
                <div className="mt-3 rounded-lg border border-brand-blue/40 bg-brand-blue/10 p-3">
                  <div className="text-xs font-black text-white">记账预览</div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
                    <div className="text-text-secondary">
                      数量
                      <div className="font-mono text-white">
                        {preview.quantity.toLocaleString('zh-CN')} 股
                      </div>
                    </div>
                    <div className="text-text-secondary">
                      成交金额
                      <div className="font-mono text-white">
                        {formatCents(preview.gross_amount_cents)}
                      </div>
                    </div>
                    <div className="text-text-secondary">
                      现金变化
                      <div
                        className={`font-mono ${pnlTone(
                          preview.cash_delta_cents,
                        )}`}
                      >
                        {formatCents(preview.cash_delta_cents, true)}
                      </div>
                    </div>
                    <div className="text-text-secondary">
                      目标仓位
                      <div className="font-mono text-white">
                        {formatBps(preview.target_position_bps)}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <button
                type="button"
                onClick={preview ? handleConfirm : handlePreview}
                disabled={previewMutation.isPending || postTradeMutation.isPending}
                className={`mt-3 w-full rounded-lg px-4 py-2 text-xs font-black text-white disabled:opacity-50 ${
                  preview ? 'bg-success' : 'bg-brand-blue'
                }`}
              >
                {postTradeMutation.isPending
                  ? '正在记账…'
                  : previewMutation.isPending
                    ? '正在预览…'
                    : preview
                      ? '确认记账（不连接券商、不自动下单）'
                      : '先预览交易'}
              </button>
              <div className="mt-2 text-center text-[10px] text-text-tertiary">
                仅记录已确认的真实成交，不发送任何券商委托。
              </div>
            </>
          )}
        </CockpitPanel>
  );
  const cashBookingContent = (
selectedAccount && (
              <div className="rounded-lg border border-border-primary bg-bg-primary/55 p-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2 text-xs font-black text-white">
                      <Landmark className="h-4 w-4 text-brand-blue-light" />
                      资金管理
                    </div>
                    <div className="mt-1 text-[10px] text-text-tertiary">
                      {selectedAccount.name} · 当前现金 {formatCents(selectedAccount.cash_cents)}
                    </div>
                  </div>
                  <StageBadge value={selectedAccount.status} />
                </div>

                <div className="mt-3 grid grid-cols-2 gap-2">
                  {(['DEPOSIT', 'WITHDRAWAL'] as PortfolioCashFlowType[]).map(
                    (flowType) => (
                      <button
                        key={flowType}
                        type="button"
                        onClick={() => updateCashFlow('businessType', flowType)}
                        className={`rounded border px-3 py-2 text-xs font-black ${
                          cashFlowDraft.businessType === flowType
                            ? flowType === 'DEPOSIT'
                              ? 'border-success bg-success/10 text-success'
                              : 'border-warning bg-warning/10 text-warning'
                            : 'border-border-primary bg-bg-primary text-text-secondary'
                        }`}
                      >
                        {flowType === 'DEPOSIT' ? '入金' : '出金'}
                      </button>
                    ),
                  )}
                </div>

                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <label className="text-[10px] text-text-secondary">
                    金额（元）
                    <input
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={cashFlowDraft.amountYuan}
                      onChange={(event) =>
                        updateCashFlow('amountYuan', event.target.value)
                      }
                      placeholder="0.00"
                      className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                    />
                  </label>
                  <label className="text-[10px] text-text-secondary">
                    资金日期
                    <input
                      type="date"
                      max={today}
                      value={cashFlowDraft.tradeDate}
                      onChange={(event) =>
                        updateCashFlow('tradeDate', event.target.value)
                      }
                      className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 text-xs text-white"
                    />
                  </label>
                  <label className="text-[10px] text-text-secondary">
                    资金时间
                    <input
                      type="time"
                      step="60"
                      value={cashFlowDraft.tradeTime}
                      onChange={(event) =>
                        updateCashFlow('tradeTime', event.target.value)
                      }
                      className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                    />
                  </label>
                  <label className="text-[10px] text-text-secondary">
                    备注（可选）
                    <input
                      value={cashFlowDraft.note}
                      maxLength={500}
                      onChange={(event) =>
                        updateCashFlow('note', event.target.value)
                      }
                      placeholder="例如：追加本金"
                      className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 text-xs text-white"
                    />
                  </label>
                </div>

                {cashFlowDraft.amountYuan && (() => {
                  let amountCents: number | null = null;
                  try {
                    amountCents = parseYuan(cashFlowDraft.amountYuan, '资金金额');
                  } catch {
                    amountCents = null;
                  }
                  const projectedCash = amountCents === null
                    ? null
                    : selectedAccount.cash_cents + (
                      cashFlowDraft.businessType === 'DEPOSIT'
                        ? amountCents
                        : -amountCents
                    );
                  return (
                    <div className="mt-3 rounded border border-border-primary bg-bg-primary px-3 py-2 text-[10px] text-text-secondary">
                      操作后预计现金：{' '}
                      <span className={`font-mono font-bold ${
                        projectedCash !== null && projectedCash < 0
                          ? 'text-danger'
                          : 'text-white'
                      }`}>
                        {formatCents(projectedCash)}
                      </span>
                    </div>
                  );
                })()}

                {cashFlowError && (
                  <div className="mt-3 rounded border border-danger/40 bg-danger/10 p-2 text-[10px] text-danger">
                    {cashFlowError}
                  </div>
                )}
                {cashFlowSuccess && (
                  <div className="mt-3 rounded border border-success/40 bg-success/10 p-2 text-[10px] text-success">
                    {cashFlowSuccess}
                  </div>
                )}
                <button
                  type="button"
                  onClick={handleCashFlow}
                  disabled={
                    cashFlowMutation.isPending
                    || selectedAccount.status !== 'ACTIVE'
                  }
                  className="mt-3 w-full rounded-lg bg-brand-blue px-4 py-2 text-xs font-black text-white disabled:opacity-50"
                >
                  {cashFlowMutation.isPending
                    ? '记账中…'
                    : `确认${cashFlowDraft.businessType === 'DEPOSIT' ? '入金' : '出金'}`}
                </button>
                <div className="mt-2 text-[10px] leading-5 text-text-tertiary">
                  入金和出金作为外部现金流，不计入投资收益；已生成绩效快照的日期不可回填。
                </div>
              </div>
            )
  );

  if (accountsQuery.isLoading) {
    return (
      <WorkbenchPage
        index="07"
        title="Portfolio  真实持仓"
        subtitle={`当前口径：${today}（Asia/Shanghai）`}
      >
        <div className="evo-panel p-8 text-center text-sm text-text-secondary">
          正在加载投资组合账户…
        </div>
      </WorkbenchPage>
    );
  }

  return (
    <WorkbenchPage
      index="07"
      title="Portfolio  真实持仓"
      subtitle={`多账户真实成交记账与持仓管理｜当前口径：${today}（Asia/Shanghai）`}
      date={today}
      onRefresh={refresh}
    >
      {accountsQuery.isError && (
        <div className="mb-3 rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
          账户加载失败：{errorMessage(accountsQuery.error)}
        </div>
      )}

      {accounts.length === 0 && (
        <div className="mb-3 rounded-lg border border-brand-blue/40 bg-brand-blue/10 p-4">
          <div className="text-sm font-black text-white">还没有 Portfolio 账户</div>
          <div className="mt-1 text-xs text-text-secondary">
            先创建现金或模拟账户，再记录真实成交。系统不会连接券商，也不会自动下单。
          </div>
          <button
            type="button"
            onClick={() => {
              setAccountSettingsOpen(true);
              setNewAccountOpen(true);
            }}
            className="mt-3 rounded border border-brand-blue/60 px-3 py-1.5 text-xs font-bold text-brand-blue-light"
          >
            设置首个账户
          </button>
        </div>
      )}

      <div
        className={accountSettingsOpen
          ? 'grid gap-3 xl:grid-cols-[1.25fr_0.75fr]'
          : 'grid gap-3'}
      >
        <CompactPortfolioPanel
          title={selectedAccount ? `账户总览 · ${selectedAccount.account_type}${selectedAccount.broker_name ? ` · ${selectedAccount.broker_name}` : ''}` : '账户总览'}
          icon={WalletCards}
          className="[&>div]:p-2 [&>header]:min-h-0 [&>header]:py-2"
          extra={(
            <div className="flex flex-wrap items-center justify-end gap-2">
              {selectedAccount && <StageBadge value={selectedAccount.status} />}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={!activeAccountId} onClick={() => {setBookingTab('manual'); setBookingOpen(true);}}
          className="rounded-lg border border-border-primary bg-bg-secondary px-3 py-1 text-xs font-medium text-white hover:bg-bg-tertiary disabled:opacity-40">
          记一笔
        </button>
        <button type="button" disabled={!activeAccountId} onClick={() => {setBookingTab('screenshot'); setBookingOpen(true);}}
          className="rounded-lg bg-brand-blue px-3 py-1 text-xs font-semibold text-white hover:brightness-110 disabled:opacity-40">
          导入截图
        </button>
      </div>
              {accounts.length > 0 && (
                <select
                  value={activeAccountId ?? ''}
                  onChange={(event) => {
                    setSelectedAccountId(Number(event.target.value));
                    invalidatePreview();
                  }}
                  className="rounded border border-border-primary bg-bg-primary px-2 py-1 text-[11px] text-white"
                >
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name}{account.is_default ? '（默认）' : ''}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                aria-expanded={accountSettingsOpen}
                onClick={() => setAccountSettingsOpen((current) => !current)}
                className={`flex items-center gap-1 rounded border px-2 py-1 text-[10px] transition-colors ${
                  accountSettingsOpen
                    ? 'border-brand-blue/60 bg-brand-blue/10 text-brand-blue-light'
                    : 'border-border-primary text-text-tertiary hover:text-white'
                }`}
              >
                <Settings2 className="h-3.5 w-3.5" />
                账户设置
              </button>
            </div>
          )}
        >
          {selectedAccount ? (
            <>
              <div className="overflow-x-auto">
              <div className="grid min-w-[1120px] grid-cols-8 gap-2">
                <CompactAssetCard
                  label="现金"
                  value={formatCents(summary.cashCents)}
                  sub="真实账本余额"
                />
                <CompactAssetCard
                  label="持仓市值"
                  value={formatCents(summary.marketValueCents)}
                  sub="仅计有行情持仓"
                />
                <CompactAssetCard
                  label={
                    summary.missingPriceCount > 0
                      ? '可估值资产小计'
                      : '总资产'
                  }
                  value={formatCents(summary.totalAssetCents)}
                  sub={
                    summary.missingPriceCount > 0
                      ? '不等于完整总资产'
                      : '现金 + 持仓市值'
                  }
                />
                <CompactAssetCard
                  label="未实现盈亏"
                  value={formatCents(summary.unrealizedPnlCents, true)}
                  sub="仅计有行情持仓"
                  tone={(summary.unrealizedPnlCents ?? 0) >= 0 ? 'good' : 'bad'}
                />
                <CompactAssetCard
                  label="未实现收益率"
                  value={formatBps(summary.unrealizedPnlBps)}
                  sub="按可估值成本"
                  tone={(summary.unrealizedPnlBps ?? 0) >= 0 ? 'good' : 'bad'}
                />
                {performancePeriods.filter(({period}) => period !== 'YEAR').map(({ period, label }) => {
                  const periodSummary = performanceSummaryQuery.data?.[period];
                  const status = periodSummary?.status ?? 'EMPTY';
                  const hasSafeValues =
                    Number.isSafeInteger(periodSummary?.pnl_cents)
                    && Number.isSafeInteger(periodSummary?.return_ppb);
                  const comparable = status === 'COMPLETE' && hasSafeValues;
                  const pnl = comparable ? periodSummary!.pnl_cents : null;
                  return (
                    <div key={period} className="min-w-0 rounded-lg border border-border-primary bg-bg-secondary px-2.5 py-1.5">
                      <div className="text-[10px] text-text-tertiary">
                        {label}盈亏
                      </div>
                      <div
                        className={`mt-0.5 whitespace-nowrap font-mono text-lg font-bold leading-tight ${
                          comparable
                            ? pnlTone(pnl)
                            : 'text-text-secondary'
                        }`}
                      >
                        {comparable
                          ? formatCents(periodSummary!.pnl_cents, true)
                          : performanceStatusText(
                            status,
                            status === 'COMPLETE' && !hasSafeValues,
                          )}
                      </div>
                      <div
                        className={`text-[10px] ${
                          comparable
                            ? pnlTone(periodSummary!.return_ppb)
                            : 'text-text-secondary'
                        }`}
                      >
                        {comparable
                          ? `${formatPpb(periodSummary!.return_ppb)} · `
                            + `${periodSummary!.trading_day_count} 个交易日`
                          : status === 'INITIAL'
                            ? '收益率从下一完整快照开始'
                            : '金额与收益率均不展示'}
                      </div>
                    </div>
                  );
                })}

              </div>
              </div>
              {summary.missingPriceCount > 0 && (
                <div className="mt-3 rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
                  {summary.missingPriceCount} 只持仓缺少行情，未计入市值、可估值资产小计和未实现盈亏。
                </div>
              )}
            </>
          ) : (
            <div className="py-8 text-center text-xs text-text-secondary">
              创建账户后显示真实资产摘要
            </div>
          )}
        </CompactPortfolioPanel>

        {accountSettingsOpen && (
          <CockpitPanel title="账户设置" icon={Settings2}>
            <button type="button" onClick={() => {setBookingTab('cash'); setBookingOpen(true);}}
              className="rounded-lg border border-border-primary px-3 py-2 text-sm text-text-secondary hover:text-white">
              入金 / 出金记账
            </button>

            <div className={`${selectedAccount ? 'mt-3' : ''} rounded-lg border border-border-primary bg-bg-primary/40`}>
              <button
                type="button"
                aria-expanded={newAccountOpen}
                onClick={() => {
                  setNewAccountOpen((current) => !current);
                  setAccountSuccess('');
                }}
                className="flex w-full items-center justify-between px-3 py-2 text-[11px] font-bold text-text-secondary hover:text-white"
              >
                <span className="flex items-center gap-2">
                  <Plus className="h-3.5 w-3.5" />
                  新增账户
                </span>
                <span className="text-[10px] text-text-tertiary">
                  {newAccountOpen ? '收起' : '展开'}
                </span>
              </button>

              {newAccountOpen && (
                <div className="border-t border-border-primary p-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-[10px] text-text-secondary">
                      账户名称
                      <input
                        value={accountDraft.name}
                        onChange={(event) =>
                          setAccountDraft((current) => ({
                            ...current,
                            name: event.target.value,
                          }))
                        }
                        placeholder="例如：长期账户"
                        className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 text-xs text-white"
                      />
                    </label>
                    <label className="text-[10px] text-text-secondary">
                      券商（可选）
                      <input
                        value={accountDraft.brokerName}
                        onChange={(event) =>
                          setAccountDraft((current) => ({
                            ...current,
                            brokerName: event.target.value,
                          }))
                        }
                        placeholder="券商名称"
                        className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 text-xs text-white"
                      />
                    </label>
                    <label className="text-[10px] text-text-secondary">
                      账户类型
                      <select
                        value={accountDraft.accountType}
                        onChange={(event) =>
                          setAccountDraft((current) => ({
                            ...current,
                            accountType: event.target.value as PortfolioAccountCreate['account_type'],
                          }))
                        }
                        className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 text-xs text-white"
                      >
                        <option value="CASH">现金账户</option>
                        <option value="SIMULATED">模拟账户</option>
                      </select>
                    </label>
                    <label className="text-[10px] text-text-secondary">
                      初始现金（元）
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={accountDraft.openingCashYuan}
                        onChange={(event) =>
                          setAccountDraft((current) => ({
                            ...current,
                            openingCashYuan: event.target.value,
                          }))
                        }
                        className="mt-1 w-full rounded border border-border-primary bg-bg-primary px-3 py-2 font-mono text-xs text-white"
                      />
                    </label>
                  </div>
                  {accounts.length === 0 && (
                    <label className="mt-3 flex items-center gap-2 text-xs text-text-secondary">
                      <input type="checkbox" checked disabled readOnly />
                      首个账户将自动设为默认账户
                    </label>
                  )}
                  {accountError && (
                    <div className="mt-3 rounded border border-danger/40 bg-danger/10 p-2 text-[10px] text-danger">
                      {accountError}
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={handleCreateAccount}
                    disabled={createAccountMutation.isPending}
                    className="mt-3 w-full rounded-lg border border-brand-blue/60 bg-brand-blue/10 px-4 py-2 text-xs font-black text-brand-blue-light disabled:opacity-50"
                  >
                    {createAccountMutation.isPending ? '创建中…' : '创建账户'}
                  </button>
                </div>
              )}
            </div>
            {accountSuccess && (
              <div className="mt-3 rounded border border-success/40 bg-success/10 p-2 text-[10px] text-success">
                {accountSuccess}
              </div>
            )}
          </CockpitPanel>
        )}
      </div>

      <div className="mt-2">
        <CompactPortfolioPanel title="真实持仓" icon={BriefcaseBusiness}
          className="[&>div]:p-2 [&>header]:min-h-0 [&>header]:py-1.5"
          extra={<div role="tablist" aria-label="持仓与交易对账单" className="flex gap-1">
            {([
              { value: 'positions', label: '当前持仓', count: heldPositions.length },
              { value: 'statement', label: '交易对账单', count: transactions.length },
            ] as const).map((tab) => (
              <button key={tab.value} type="button" role="tab"
                id={`portfolio-tab-${tab.value}`} aria-selected={holdingsTab === tab.value}
                aria-controls={`portfolio-panel-${tab.value}`}
                onClick={() => setHoldingsTab(tab.value)}
                className={`rounded-lg px-3 py-1 text-xs font-medium transition-colors ${holdingsTab === tab.value
                  ? 'bg-brand-blue/15 text-brand-blue-light'
                  : 'text-text-secondary hover:bg-bg-primary hover:text-white'}`}>
                {tab.label} <span className="ml-1 font-mono text-[10px]">{tab.count}</span>
              </button>
            ))}
          </div>}>
          <div role="tabpanel" id={`portfolio-panel-${holdingsTab}`} aria-labelledby={`portfolio-tab-${holdingsTab}`}>

          {holdingsTab === 'positions' && (<>
          {positionsQuery.isLoading ? (
            <div className="py-8 text-center text-xs text-text-secondary">
              正在加载持仓…
            </div>
          ) : positionsQuery.isError ? (
            <div className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
              持仓加载失败：{errorMessage(positionsQuery.error)}
            </div>
          ) : heldPositions.length === 0 ? (
            <div className="py-8 text-center text-xs text-text-secondary">
              当前账户暂无有仓位的持仓
            </div>
          ) : (
            <div className="max-h-[clamp(180px,30vh,360px)] overflow-auto overscroll-contain">
              <table className="w-full min-w-[1080px] text-left text-[11px]">
                <thead className="sticky top-0 z-10 border-b border-border-primary bg-bg-secondary text-text-tertiary">
                  <tr>
                    <th className="px-2 py-1.5">股票</th>
                    <th className="px-2 py-1.5">行业</th>
                    <th className="px-2 py-1.5 text-right">数量 / 可卖</th>
                    <th className="px-2 py-1.5 text-right">均价 / 现价</th>
                    <th className="px-2 py-1.5 text-right">成本 / 市值</th>
                    <th className="px-2 py-1.5 text-right">未实现盈亏</th>
                    <th className="px-2 py-1.5 text-right">已实现盈亏</th>
                    <th className="px-2 py-1.5">行情</th>
                    <th className="px-2 py-1.5">状态</th>
                  <th className="px-2 py-1.5">记账</th>
                  </tr>
                </thead>
                <tbody>
                  {heldPositions.map((position: PortfolioPosition) => (
                    <tr
                      key={`${position.account_id}-${position.symbol}`}
                      className="border-b border-border-primary/70"
                    >
                      <td className="px-2 py-1.5">
                        <Link
                          href={`/a/stock/${position.symbol}`}
                          className="font-mono font-bold text-brand-blue-light"
                        >
                          {position.symbol}
                        </Link>
                        <div className="text-[10px] text-text-secondary">
                          {position.name || '-'}
                        </div>
                      </td>
                      <td className="px-2 py-1.5 text-text-secondary">
                        {position.industry || '-'}
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono">
                        {position.quantity.toLocaleString('zh-CN')}
                        <div className="text-[10px] text-text-secondary">
                          可卖 {position.available_quantity.toLocaleString('zh-CN')}
                        </div>
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono">
                        {formatMicros(position.avg_cost_micros)}
                        <div className="text-[10px] text-text-secondary">
                          {formatMicros(position.price_micros)}
                        </div>
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono">
                        {formatCents(position.cost_basis_cents)}
                        <div className="text-[10px] text-text-secondary">
                          {formatCents(position.market_value_cents)}
                        </div>
                      </td>
                      <td
                        className={`px-2 py-1.5 text-right font-mono ${pnlTone(
                          position.unrealized_pnl_cents,
                        )}`}
                      >
                        {formatCents(position.unrealized_pnl_cents, true)}
                        <div className="text-[10px]">
                          {formatBps(position.unrealized_pnl_bps)}
                        </div>
                      </td>
                      <td
                        className={`px-2 py-1.5 text-right font-mono ${pnlTone(
                          position.realized_pnl_cents,
                        )}`}
                      >
                        {formatCents(position.realized_pnl_cents, true)}
                      </td>
                      <td className="px-2 py-1.5">
                        <span
                          className={
                            position.price_status === 'CURRENT'
                              ? 'text-success'
                              : position.price_status === 'STALE'
                                ? 'text-warning'
                                : 'text-danger'
                          }
                        >
                          {position.price_status}
                        </span>
                        <div className="text-[10px] text-text-secondary">
                          {position.price_trade_date || '无行情'}
                        </div>
                      </td>
                      <td className="px-2 py-1.5">
                        <StageBadge value={position.status} />
                      </td>
                      <td className="px-2 py-1.5 whitespace-nowrap">
                        {(['BUY', 'SELL'] as PortfolioTradeSide[]).map(side => <button
                          key={side} type="button" disabled={side === 'SELL' && position.quantity === 0}
                          onClick={() => {updateTrade('symbol', position.symbol); updateTrade('businessType', side); setBookingTab('manual'); setBookingOpen(true);}}
                          className="mr-2 rounded border border-border-primary px-2 py-1 text-brand-blue-light disabled:opacity-30">
                          {side === 'BUY' ? '记买入' : '记卖出'}
                        </button>)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </>)}
          {holdingsTab === 'statement' && (
            transactionsQuery.isLoading ? (
              <div className="py-8 text-center text-xs text-text-secondary">正在加载历史对账单…</div>
            ) : transactionsQuery.isError ? (
              <div className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
                对账单加载失败：{errorMessage(transactionsQuery.error)}
              </div>
            ) : (
              <TransactionStatement key={activeAccountId} transactions={transactions}
                truncated={transactionsQuery.data?.truncated} />
            )
          )}

          </div>
        </CompactPortfolioPanel>


      </div>

      <div className="mt-2 grid min-w-0 gap-2">


        {selectedAccount?.derived_data_pending && <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
          成交已记账，历史权益正在等待补齐。以下仅展示已核验快照。
        </div>}
        {performanceSnapshotsQuery.isLoading ? <div className="evo-panel p-8 text-text-secondary">正在读取权益…</div>
          : performanceSnapshotsQuery.isError ? <div className="evo-panel p-8 text-danger">权益读取失败，请刷新重试。</div>
          : <EquityChart snapshots={performanceSnapshots} baselineDate={selectedAccount?.baseline_date} />}

      </div>


      <PortfolioDisclosure title="卖点计划与监控状态" icon={ShieldAlert}>
            <div id="portfolio-exit-plans">
              {!selectedAccount ? (
            <div className="py-8 text-center text-xs text-text-secondary">
              请先创建或选择账户
            </div>
          ) : exitPlansQuery.isLoading || exitStatesQuery.isLoading
            || positionsQuery.isLoading ? (
            <div className="py-8 text-center text-xs text-text-secondary">
              正在读取卖点计划与监控状态…
            </div>
          ) : exitPlansQuery.isError || exitStatesQuery.isError ? (
            <div className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
              卖点配置加载失败：
              {errorMessage(exitPlansQuery.error ?? exitStatesQuery.error)}
            </div>
          ) : openPositions.length === 0 ? (
            <div className="py-8 text-center text-xs text-text-secondary">
              当前账户没有 OPEN 持仓；建仓后可为每只股票设置独立卖点计划。
            </div>
          ) : (
            <div className="space-y-3">
              {openPositions.map((position) => {
                const draft =
                  exitPlanDrafts[position.symbol] ?? exitPlanDraft();
                const persistedPlan = exitPlans.find(
                  (plan) =>
                    plan.symbol === position.symbol
                    && plan.position_revision === position.position_revision,
                );
                const monitorState = exitStates.find(
                  (state) =>
                    state.symbol === position.symbol
                    && state.position_revision === position.position_revision,
                );
                const isSaving =
                  saveExitPlanMutation.isPending
                  && saveExitPlanMutation.variables?.symbol === position.symbol;
                const exitPlanExpanded = expandedExitPlanSymbols.has(position.symbol);
                const inputClass =
                  'mt-1 w-full rounded border border-border-primary bg-bg-primary '
                  + 'px-2 py-2 font-mono text-xs text-white';
                return (
                  <div
                    key={`${position.account_id}-${position.symbol}-${position.position_revision}`}
                    className="rounded-lg border border-border-primary bg-bg-primary/50 p-3"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <Link
                            href={`/a/stock/${position.symbol}`}
                            className="font-mono text-sm font-black text-brand-blue-light"
                          >
                            {position.symbol}
                          </Link>
                          <span className="text-xs font-bold text-white">
                            {position.name || '未命名股票'}
                          </span>
                          <StageBadge
                            value={persistedPlan ? '已保存计划' : '尚未保存'}
                          />
                        </div>
                        <div className="mt-1 text-[10px] text-text-secondary">
                          持仓 {position.quantity.toLocaleString('zh-CN')} 股
                          {' · '}可卖 {position.available_quantity.toLocaleString('zh-CN')} 股
                          {' · '}成本 {formatMicros(position.avg_cost_micros)}
                          {' · '}持仓版本 {position.position_revision}
                        </div>
                      </div>
                      <div className="text-right text-[10px] text-text-secondary">
                        <div>
                          监控状态：
                          <span className="ml-1 text-white">
                            {monitorState?.state ?? '尚未运行'}
                          </span>
                        </div>
                        <div className="mt-1">
                          数据：
                          <span
                            className={
                              monitorState?.data_status === 'OK'
                                ? 'ml-1 text-success'
                                : 'ml-1 text-warning'
                            }
                          >
                            {monitorState?.data_status ?? '无状态'}
                          </span>
                          {monitorState?.last_price_date
                            ? ` · ${monitorState.last_price_date}`
                            : ''}
                        </div>
                        {monitorState?.trailing_stop_micros !== null
                          && monitorState?.trailing_stop_micros !== undefined && (
                          <div className="mt-1">
                            当前移动止损线：
                            <span className="ml-1 font-mono text-warning">
                              {formatMicros(monitorState.trailing_stop_micros)}
                            </span>
                          </div>
                        )}
                        <button
                          type="button"
                          aria-expanded={exitPlanExpanded}
                          aria-controls={`exit-plan-${position.symbol}`}
                          onClick={() => {
                            setExpandedExitPlanSymbols((current) => {
                              const next = new Set(current);
                              if (next.has(position.symbol)) next.delete(position.symbol);
                              else next.add(position.symbol);
                              return next;
                            });
                          }}
                          className="mt-2 inline-flex items-center gap-1 rounded border border-border-primary px-2 py-1 font-bold text-text-secondary transition-colors hover:border-brand-blue/60 hover:text-white"
                        >
                          {exitPlanExpanded ? '收起配置' : '展开配置'}
                          <ChevronDown
                            className={`h-3.5 w-3.5 transition-transform ${
                              exitPlanExpanded ? 'rotate-180' : ''
                            }`}
                          />
                        </button>
                      </div>
                    </div>

                    {exitPlanExpanded && (
                      <div id={`exit-plan-${position.symbol}`}>
                        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                      <label className="text-[10px] text-text-secondary">
                        硬止损价（元）
                        <input
                          type="number"
                          min="0"
                          step="0.000001"
                          value={draft.hardStopYuan}
                          onChange={(event) =>
                            updateExitDraft(
                              position.symbol,
                              'hardStopYuan',
                              event.target.value,
                            )}
                          placeholder="留空则不启用"
                          className={inputClass}
                        />
                      </label>
                      <label className="text-[10px] text-text-secondary">
                        移动止损启动收益（%）
                        <input
                          type="number"
                          min="0.01"
                          max="1000"
                          step="0.01"
                          value={draft.trailingActivationPercent}
                          onChange={(event) =>
                            updateExitDraft(
                              position.symbol,
                              'trailingActivationPercent',
                              event.target.value,
                            )}
                          placeholder="例如 10"
                          className={inputClass}
                        />
                      </label>
                      <label className="text-[10px] text-text-secondary">
                        移动止损回撤比例（%）
                        <input
                          type="number"
                          min="0.01"
                          max="100"
                          step="0.01"
                          value={draft.trailingDistancePercent}
                          onChange={(event) =>
                            updateExitDraft(
                              position.symbol,
                              'trailingDistancePercent',
                              event.target.value,
                            )}
                          placeholder="例如 5"
                          className={inputClass}
                        />
                      </label>
                      <label className="text-[10px] text-text-secondary">
                        时间止损交易日
                        <input
                          type="number"
                          min="1"
                          max="10000"
                          step="1"
                          value={draft.timeStopTradingDays}
                          onChange={(event) =>
                            updateExitDraft(
                              position.symbol,
                              'timeStopTradingDays',
                              event.target.value,
                            )}
                          placeholder="例如 20"
                          className={inputClass}
                        />
                      </label>
                      <label className="text-[10px] text-text-secondary">
                        时间止损最低收益（%）
                        <input
                          type="number"
                          min="-100"
                          max="1000"
                          step="0.01"
                          value={draft.timeStopMinReturnPercent}
                          onChange={(event) =>
                            updateExitDraft(
                              position.symbol,
                              'timeStopMinReturnPercent',
                              event.target.value,
                            )}
                          placeholder="例如 3 或 -2"
                          className={inputClass}
                        />
                      </label>
                    </div>

                    <div className="mt-3 overflow-x-auto">
                      <table className="w-full min-w-[720px] text-left text-[10px]">
                        <thead className="text-text-tertiary">
                          <tr>
                            <th className="px-2 py-1">止盈档位</th>
                            <th className="px-2 py-1">触发价格（元）</th>
                            <th className="px-2 py-1">建议减仓比例（%）</th>
                            <th className="px-2 py-1">说明</th>
                          </tr>
                        </thead>
                        <tbody>
                          {([
                            ['1', 'takeProfit1Yuan', 'takeProfit1Percent'],
                            ['2', 'takeProfit2Yuan', 'takeProfit2Percent'],
                            ['3', 'takeProfit3Yuan', 'takeProfit3Percent'],
                          ] as const).map(([tier, priceKey, percentKey]) => (
                            <tr key={tier} className="border-t border-border-primary/60">
                              <td className="px-2 py-2 font-mono text-white">
                                第 {tier} 档
                              </td>
                              <td className="px-2 py-2">
                                <input
                                  type="number"
                                  min="0"
                                  step="0.000001"
                                  value={draft[priceKey]}
                                  onChange={(event) =>
                                    updateExitDraft(
                                      position.symbol,
                                      priceKey,
                                      event.target.value,
                                    )}
                                  placeholder="留空"
                                  className="w-full rounded border border-border-primary bg-bg-primary px-2 py-1.5 font-mono text-xs text-white"
                                />
                              </td>
                              <td className="px-2 py-2">
                                <input
                                  type="number"
                                  min="0.01"
                                  max="100"
                                  step="0.01"
                                  value={draft[percentKey]}
                                  onChange={(event) =>
                                    updateExitDraft(
                                      position.symbol,
                                      percentKey,
                                      event.target.value,
                                    )}
                                  placeholder="留空"
                                  className="w-full rounded border border-border-primary bg-bg-primary px-2 py-1.5 font-mono text-xs text-white"
                                />
                              </td>
                              <td className="px-2 py-2 text-text-secondary">
                                价格与比例成对填写；三档价格递增、比例合计不超过 100%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
                      <label className="flex items-center gap-2 text-xs text-text-secondary">
                        <input
                          type="checkbox"
                          checked={draft.enabled}
                          onChange={(event) =>
                            updateExitDraft(
                              position.symbol,
                              'enabled',
                              event.target.checked,
                            )}
                        />
                        启用该计划
                      </label>
                      <label className="flex items-center gap-2 text-xs text-text-secondary">
                        <input
                          type="checkbox"
                          checked={draft.trendExitEnabled}
                          onChange={(event) =>
                            updateExitDraft(
                              position.symbol,
                              'trendExitEnabled',
                              event.target.checked,
                            )}
                        />
                        策略 C 日线风险与退出
                      </label>
                      <label className="flex items-center gap-2 text-xs text-text-secondary">
                        <input
                          type="checkbox"
                          checked={draft.intradayEnabled}
                          onChange={(event) =>
                            updateExitDraft(
                              position.symbol,
                              'intradayEnabled',
                              event.target.checked,
                            )}
                        />
                        盘中 5 分钟监控
                      </label>
                      <button
                        type="button"
                        onClick={() => handleSaveExitPlan(position)}
                        disabled={isSaving}
                        className="ml-auto rounded-lg bg-brand-blue px-4 py-2 text-xs font-black text-white disabled:opacity-50"
                      >
                        {isSaving ? '保存中…' : '显式保存卖点计划'}
                      </button>
                    </div>
                    {exitPlanErrors[position.symbol] && (
                      <div className="mt-3 rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
                        {exitPlanErrors[position.symbol]}
                      </div>
                    )}
                      </div>
                    )}
                  </div>
                );
              })}
              <div className="rounded-lg border border-border-primary bg-bg-primary/60 p-3 text-[10px] leading-5 text-text-tertiary">
                所有价格仅在浏览器中由“元”换算为整数微元提交，比例由百分比换算为 bps。
                空白阈值不会被填入猜测值；只有点击保存才写入计划。提醒仅供决策，
                不连接券商、不自动下单。
              </div>
            </div>
              )}
            </div>
      </PortfolioDisclosure>

      <PortfolioDisclosure title="卖点提醒行动箱" icon={BellRing} count={exitEvents.length}>
          <p className="mb-2 text-[11px] text-text-secondary">
            策略 C 按日线收盘确认：结构失效、连续两日失守 MA10 且 MACD 动能走弱，或顶背离后破位。
            结构失效线仅在建仓时存在有效 C 结构时绑定；手工价格计划继续独立生效。
          </p>
          {!selectedAccount ? (
            <div className="py-8 text-center text-xs text-text-secondary">
              请先创建或选择账户
            </div>
          ) : exitEventsQuery.isLoading ? (
            <div className="py-8 text-center text-xs text-text-secondary">
              正在加载卖点提醒…
            </div>
          ) : exitEventsQuery.isError ? (
            <div className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
              卖点提醒加载失败：{errorMessage(exitEventsQuery.error)}
            </div>
          ) : exitEvents.length === 0 ? (
            <div className="py-8 text-center text-xs text-text-secondary">
              当前账户暂无卖点提醒。页面查询不会触发评估，提醒由盘后或盘中监控任务生成。
            </div>
          ) : (
            <>
              {exitActionError && (
                <div className="mb-3 rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
                  事件操作失败：{exitActionError}
                </div>
              )}
              <div className="overflow-x-auto">
                <table className="w-full min-w-[1120px] text-left text-[11px]">
                  <thead className="border-b border-border-primary text-text-tertiary">
                    <tr>
                      <th className="px-2 py-2">触发时间</th>
                      <th className="px-2 py-2">股票</th>
                      <th className="px-2 py-2">类型 / 级别</th>
                      <th className="px-2 py-2 text-right">观察价格</th>
                      <th className="px-2 py-2 text-right">建议数量</th>
                      <th className="px-2 py-2">可执行性</th>
                      <th className="px-2 py-2">来源</th>
                      <th className="px-2 py-2">状态</th>
                      <th className="px-2 py-2 text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {exitEvents.map((event) => {
                      const cPresentation = cExitPresentation(event.event_type, event.payload_json ?? '');
                      const position = positions.find(
                        (item) => item.symbol === event.symbol,
                      );
                      const actionPending =
                        exitEventActionMutation.isPending
                        && exitEventActionMutation.variables?.eventId === event.id;
                      return (
                        <tr
                          key={event.id}
                          className="border-b border-border-primary/70"
                        >
                          <td className="px-2 py-2 font-mono">
                            {event.bar_time || event.triggered_at}
                          </td>
                          <td className="px-2 py-2">
                            <Link
                              href={`/a/stock/${event.symbol}`}
                              className="font-mono font-bold text-brand-blue-light"
                            >
                              {event.symbol}
                            </Link>
                            <div className="text-[10px] text-text-secondary">
                              {position?.name || `持仓版本 ${event.position_revision}`}
                            </div>
                          </td>
                          <td className="px-2 py-2">
                            <span className="font-bold text-white">
                              {cPresentation?.label ?? exitEventText(event.event_type)}
                              {event.tier ? ` · 第 ${event.tier} 档` : ''}
                            </span>
                            <div
                              className={`text-[10px] ${exitSeverityTone(
                                event.severity,
                              )}`}
                            >
                              {event.severity}
                            </div>
                            {cPresentation && (
                              <div className="mt-1 max-w-[280px] whitespace-normal text-[10px] text-text-secondary">
                                <div>{cPresentation.reason}</div>
                                <div>{cPresentation.structureNote}</div>
                              </div>
                            )}
                          </td>
                          <td className="px-2 py-2 text-right font-mono">
                            {formatMicros(event.observed_price_micros)}
                          </td>
                          <td className="px-2 py-2 text-right font-mono">
                            {event.recommended_quantity.toLocaleString('zh-CN')} 股
                            <div className="text-[10px] text-text-secondary">
                              当时可卖 {event.available_quantity.toLocaleString('zh-CN')}
                            </div>
                          </td>
                          <td className="px-2 py-2">
                            <span className={event.executable ? 'text-success' : 'text-warning'}>
                              {event.executable ? '可执行' : 'T+1 暂不可卖'}
                            </span>
                            <div className="text-[10px] text-text-secondary">
                              以事件生成时可卖数量为准
                            </div>
                          </td>
                          <td className="px-2 py-2 font-mono text-text-secondary">
                            {eventSource(event)}
                          </td>
                          <td className="px-2 py-2">
                            <StageBadge value={event.event_status} />
                          </td>
                          <td className="px-2 py-2 text-right">
                            <div className="flex justify-end gap-2">
                              {event.event_status === 'OPEN' && (
                                <button
                                  type="button"
                                  disabled={actionPending}
                                  onClick={() => {
                                    setExitActionError('');
                                    exitEventActionMutation.mutate({
                                      eventId: event.id,
                                      action: 'acknowledge',
                                      accountId: selectedAccount.id,
                                    });
                                  }}
                                  className="rounded border border-brand-blue/50 px-2 py-1 text-[10px] text-brand-blue-light disabled:opacity-50"
                                >
                                  确认
                                </button>
                              )}
                              {event.event_status !== 'RESOLVED' && (
                                <button
                                  type="button"
                                  disabled={actionPending}
                                  onClick={() => {
                                    setExitActionError('');
                                    exitEventActionMutation.mutate({
                                      eventId: event.id,
                                      action: 'resolve',
                                      accountId: selectedAccount.id,
                                    });
                                  }}
                                  className="rounded border border-success/50 px-2 py-1 text-[10px] text-success disabled:opacity-50"
                                >
                                  解决
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="mt-3 text-[10px] leading-5 text-text-tertiary">
                “确认”表示已看到提醒，“解决”表示该提醒无需继续处理；两者都不会创建卖出交易。
                每条事件保留触发时价格、可卖数量、规则版本和数据来源，便于复核。
              </div>
            </>
          )}
      </PortfolioDisclosure>


      <div className="mt-3 flex items-center gap-2 rounded-lg border border-border-primary bg-bg-primary/70 p-3 text-[11px] text-text-secondary">
        <CircleDollarSign size={14} className="text-brand-blue-light" />
        所有金额均由整数分、价格由整数微元换算展示；刷新只重新读取数据，不产生交易或持仓写入。
      </div>
      <BookingDrawer open={bookingOpen} onClose={() => setBookingOpen(false)}
        initialTab={bookingTab} accountId={activeAccountId} onBooked={refresh}
        manualContent={manualBookingContent} cashContent={cashBookingContent} />
    </WorkbenchPage>
  );
}
