'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  Check,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Settings2,
  ShieldCheck,
} from 'lucide-react';
import Modal from '@/components/ui/Modal';
import { watchApi } from '@/lib/api';
import {
  describeWatchFactors,
  setupPathLabel,
  validateRepairRuleConfig,
  type WatchFactorMap,
} from '@/components/stock/watchRulePresentation';

type PoolOption = {
  pool_id: string;
  name: string;
  stock_count?: number | null;
  monitor_enabled?: number | boolean | null;
  is_default?: number | boolean | null;
};

type WatchRuleConfig = {
  market_states: string[];
  industry_states: string[];
  rps50_min: number;
  structure_score_min: number;
  pullback_min_pct: number;
  pullback_max_pct: number;
  near_ma_pct: number;
  trend_include_ma10_support: boolean;
  volume_ratio_max_pct: number;
  volume_shrink_days: number;
  max_stop_loss_pct: number;
  chase_limit_pct: number;
  setup_valid_days: number;
  cooldown_trade_days: number;
  structure_lookback_days: number;
  double_bottom_tolerance_pct: number;
  macd_improve_days: number;
  confirmation_methods: string[];
  require_above_ma50: boolean;
  repair_path_enabled: boolean;
  repair_market_states: string[];
  repair_industry_states: string[];
  repair_setup_score_min: number;
  repair_trigger_score_min: number;
  repair_pullback_max_pct: number;
  repair_ma50_below_max_pct: number;
  repair_volume_ratio_max_pct: number;
  repair_reversal_volume_ratio_max_pct: number;
  constructive_max_decline_pct: number;
  repair_confirmation_min_count: number;
  repair_min_rps_anchor: number;
  market_rebound_delta_min: number;
  industry_rebound_delta_min: number;
};

type RuleVersion = {
  version: number;
  status: 'ACTIVE' | 'DRAFT' | 'ARCHIVED';
  config: WatchRuleConfig;
  created_at?: string;
  activated_at?: string | null;
};

type WatchConfigResponse = {
  active: RuleVersion;
  draft?: RuleVersion | null;
  versions: RuleVersion[];
  defaults: WatchRuleConfig;
};

type ScanRun = {
  scan_id: string;
  start_date: string;
  end_date: string;
  scope: string;
  pool_ids: string[];
  rule_version: number;
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  total_symbols: number;
  total_dates: number;
  total_cells: number;
  processed_cells: number;
  opportunity_count: number;
  progress_pct: number;
  error?: string | null;
};

type ScanResult = {
  trade_date: string;
  symbol: string;
  name?: string | null;
  pool_ids: string[];
  watch_state: string;
  rule_version: number;
  current_price?: number | null;
  zone_low?: number | null;
  zone_high?: number | null;
  trigger_price?: number | null;
  invalidation_price?: number | null;
  distance_to_trigger_pct?: number | null;
  reason?: string | null;
  data_status: string;
  factors?: WatchFactorMap | null;
};

type ScanResultsResponse = {
  scan: ScanRun;
  rows: ScanResult[];
  count: number;
  limit: number;
  offset: number;
};

type ScanScope = 'all_groups' | 'monitored_groups' | 'pools';

const sharedConfirmationOptions = [
  { value: 'reversal_confluence', label: '反转多因子' },
  { value: 'structure_macd', label: '结构 + MACD' },
  { value: 'break_prev_high', label: '突破前高（偏右侧）' },
  { value: 'reclaim_ma10', label: '重上 MA10（偏右侧）' },
];
const sharedConfirmationMethods = new Set(
  sharedConfirmationOptions.map((option) => option.value),
);
const repairOnlyConfirmationMethods = new Set(['constructive_consolidation']);
const confirmationMethodOrder = [
  ...sharedConfirmationOptions.map((option) => option.value),
  'constructive_consolidation',
];

const stateLabels: Record<string, string> = {
  WATCHING: '持续观察',
  IN_ZONE: '进入回调区',
  TRIGGERED: '确认触发',
  INVALIDATED: '已经失效',
  UNKNOWN: '数据异常',
};

function messageFromError(error: unknown) {
  const candidate = error as {
    response?: { data?: { detail?: string } };
    message?: string;
  };
  return candidate.response?.data?.detail || candidate.message || '操作失败，请稍后重试';
}

function formatPrice(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '-';
}

function asPayload(config: WatchRuleConfig): Record<string, unknown> {
  return { ...config };
}

function NumberField({
  label,
  value,
  min,
  max,
  step = 1,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block rounded-md border border-border-secondary bg-bg-primary/45 px-2 py-1.5">
      <span className="mb-1 block text-[10px] font-bold text-text-tertiary">{label}</span>
      <span className="flex items-center gap-1">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(event) => onChange(Number(event.target.value))}
          className="h-7 min-w-0 flex-1 rounded border border-border-primary bg-bg-secondary px-2 font-mono text-[11px] text-text-primary outline-none focus:border-brand-blue"
        />
        {suffix && <span className="text-[10px] text-text-tertiary">{suffix}</span>}
      </span>
    </label>
  );
}

function ToggleGroup({
  label,
  values,
  options,
  onChange,
}: {
  label: string;
  values: string[];
  options: Array<{ value: string; label: string }>;
  onChange: (values: string[]) => void;
}) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-bold text-text-tertiary">{label}</div>
      <div className="flex flex-wrap gap-1">
        {options.map((option) => {
          const active = values.includes(option.value);
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange(
                active
                  ? values.filter((value) => value !== option.value)
                  : [...values, option.value],
              )}
              className={clsx(
                'rounded border px-2 py-1 text-[10px] font-bold transition-colors',
                active
                  ? 'border-brand-blue/60 bg-brand-blue/15 text-brand-blue-light'
                  : 'border-border-primary bg-bg-primary text-text-tertiary hover:text-text-primary',
              )}
            >
              {active && <Check size={9} className="mr-1 inline" />}
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function RuleScannerWorkbench({
  configData,
  pools,
  dates,
  selectedPoolId,
  onSelectSymbol,
  onClose,
}: {
  configData: WatchConfigResponse;
  pools: PoolOption[];
  dates: string[];
  selectedPoolId: string;
  onSelectSymbol: (symbol: string) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const sortedDates = useMemo(() => [...new Set(dates)].sort(), [dates]);
  const initialVersion = configData.draft || configData.active;
  const [form, setForm] = useState<WatchRuleConfig>(() => ({ ...initialVersion.config }));
  const [scope, setScope] = useState<ScanScope>(selectedPoolId ? 'pools' : 'all_groups');
  const [poolIds, setPoolIds] = useState<string[]>(() => selectedPoolId ? [selectedPoolId] : []);
  const [startDate, setStartDate] = useState(
    sortedDates[Math.max(0, sortedDates.length - 60)] || sortedDates[0] || '',
  );
  const [endDate, setEndDate] = useState(sortedDates.at(-1) || '');
  const [scanId, setScanId] = useState('');
  const [resultDate, setResultDate] = useState('');
  const [resultPool, setResultPool] = useState('');
  const [resultState, setResultState] = useState('');
  const [notice, setNotice] = useState('');

  const setNumber = (key: keyof WatchRuleConfig, value: number) => {
    setForm((current) => ({ ...current, [key]: value }));
  };
  const setList = (key: keyof WatchRuleConfig, values: string[]) => {
    setForm((current) => ({ ...current, [key]: values }));
  };
  const setConfirmationSubset = (
    subset: 'shared' | 'repair',
    values: string[],
  ) => {
    setForm((current) => {
      const retained = current.confirmation_methods.filter((method) => (
        subset === 'shared'
          ? repairOnlyConfirmationMethods.has(method)
          : sharedConfirmationMethods.has(method)
      ));
      const selected = new Set([...retained, ...values]);
      return {
        ...current,
        confirmation_methods: confirmationMethodOrder.filter((method) => selected.has(method)),
      };
    });
  };

  const saveMutation = useMutation<RuleVersion, unknown, boolean>({
    mutationFn: (activate) => watchApi.updateConfig({
      config: asPayload(form),
      activate,
    }) as Promise<RuleVersion>,
    onSuccess: (saved) => {
      setNotice(saved.status === 'ACTIVE'
        ? `规则 v${saved.version} 已启用，将用于后续实时提醒。`
        : `规则 v${saved.version} 已保存为草稿。`);
      queryClient.invalidateQueries({ queryKey: ['watchConfig'] });
    },
  });

  const scanMutation = useMutation<ScanRun, unknown>({
    mutationFn: async () => {
      const saved = await watchApi.updateConfig({
        config: asPayload(form),
        activate: false,
      }) as RuleVersion;
      const created = await watchApi.createScan({
        start_date: startDate,
        end_date: endDate,
        scope,
        pool_ids: scope === 'pools' ? poolIds : [],
        rule_version: saved.version,
      }) as ScanRun;
      return created;
    },
    onSuccess: (scan) => {
      setScanId(scan.scan_id);
      setNotice(`已提交规则 v${scan.rule_version} 的历史遍历。`);
      queryClient.invalidateQueries({ queryKey: ['watchConfig'] });
    },
  });

  const requestedScanId = scanId || 'latest';
  const scanQuery = useQuery<ScanRun>({
    queryKey: ['watchScan', requestedScanId],
    queryFn: () => watchApi.getScan(requestedScanId) as Promise<ScanRun>,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'PENDING' || status === 'RUNNING' ? 1000 : false;
    },
  });
  const scan = scanQuery.data;
  const activeScanId = scan?.scan_id || scanId;
  const resultsQuery = useQuery<ScanResultsResponse>({
    queryKey: ['watchScanResults', activeScanId, resultDate, resultPool, resultState],
    queryFn: () => watchApi.getScanResults({
      scanId: activeScanId,
      tradeDate: resultDate || undefined,
      poolId: resultPool || undefined,
      state: resultState || undefined,
      limit: 5000,
    }) as Promise<ScanResultsResponse>,
    enabled: Boolean(activeScanId && scan?.status === 'COMPLETED'),
  });

  const selectedDates = sortedDates.filter((value) => value >= startDate && value <= endDate);
  const selectedPools = scope === 'pools'
    ? pools.filter((pool) => poolIds.includes(pool.pool_id))
    : scope === 'monitored_groups'
      ? pools.filter((pool) => Boolean(pool.monitor_enabled) || Boolean(pool.is_default))
      : pools;
  const estimatedStocks = selectedPools.reduce(
    (total, pool) => total + Number(pool.stock_count || 0),
    0,
  );
  const repairValidationErrors = validateRepairRuleConfig(form);
  const selectedSharedConfirmationMethods = form.confirmation_methods.filter(
    (method) => sharedConfirmationMethods.has(method),
  );
  const selectedRepairConfirmationMethods = form.confirmation_methods.filter(
    (method) => repairOnlyConfirmationMethods.has(method),
  );
  const invalidForm = form.market_states.length === 0
    || form.industry_states.length === 0
    || selectedSharedConfirmationMethods.length === 0
    || form.pullback_min_pct > form.pullback_max_pct
    || form.setup_valid_days < 1
    || form.setup_valid_days > 20
    || form.structure_lookback_days < 7
    || form.structure_lookback_days > 30
    || form.double_bottom_tolerance_pct < 0.1
    || form.double_bottom_tolerance_pct > 10
    || form.macd_improve_days < 1
    || form.macd_improve_days > 3
    || repairValidationErrors.length > 0;
  const invalidScan = invalidForm
    || !startDate
    || !endDate
    || startDate > endDate
    || selectedDates.length === 0
    || (scope === 'pools' && poolIds.length === 0);
  const busy = saveMutation.isPending || scanMutation.isPending;
  const error = saveMutation.error || scanMutation.error || scanQuery.error || resultsQuery.error;
  const resultRows = resultsQuery.data?.rows || [];

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-border-primary bg-bg-primary/30 p-3">
        <div className="mb-3 flex items-center gap-2">
          <Settings2 size={14} className="text-brand-blue-light" />
          <h3 className="text-[12px] font-black text-white">低吸规则参数</h3>
          <span className="rounded border border-border-primary px-1.5 py-0.5 font-mono text-[9px] text-text-tertiary">
            当前启用 v{configData.active.version} · 编辑源 v{initialVersion.version} {initialVersion.status}
          </span>
          <button
            type="button"
            onClick={() => setForm({ ...configData.defaults })}
            className="ml-auto inline-flex items-center gap-1 rounded border border-border-primary px-2 py-1 text-[10px] text-text-secondary hover:text-white"
          >
            <RotateCcw size={11} /> 恢复默认
          </button>
        </div>

        <div className="mb-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          <div className="rounded border border-brand-blue/35 bg-brand-blue/10 px-2 py-1.5">
            <div className="text-[10px] font-black text-brand-blue-light">路径 A · 强势回调</div>
            <div className="mt-0.5 text-[9px] leading-4 text-text-tertiary">强市场+强行业+强个股，等待缩量回踩后确认。</div>
          </div>
          <div className="rounded border border-warning/35 bg-warning/10 px-2 py-1.5">
            <div className="text-[10px] font-black text-warning">路径 B · 领先修复</div>
            <div className="mt-0.5 text-[9px] leading-4 text-text-tertiary">领先股在市场/行业修复初期先行，支持反转放量和建设性整理确认。</div>
          </div>
          <div className="rounded border border-success/35 bg-success/10 px-2 py-1.5">
            <div className="text-[10px] font-black text-success">确认触发</div>
            <div className="mt-0.5 text-[9px] leading-4 text-text-tertiary">表示本轮 setup 已等到可执行形态；仍需人工决定，系统不会自动下单。</div>
          </div>
          <div className="rounded border border-danger/35 bg-danger/10 px-2 py-1.5">
            <div className="text-[10px] font-black text-danger">什么是“失效”？</div>
            <div className="mt-0.5 text-[9px] leading-4 text-text-tertiary">收盘价跌破本轮 setup 的失效价，该轮观察逻辑结束并等待下一轮。失效不等于自动卖出，也不代表股票永久不能再观察。</div>
          </div>
        </div>

        <div className="grid gap-3 lg:grid-cols-[1fr_2fr]">
          <div className="space-y-3 rounded-md border border-border-secondary bg-bg-secondary/35 p-2.5">
            <div className="rounded border border-brand-blue/35 bg-brand-blue/10 px-2 py-1.5">
              <div className="text-[10px] font-black text-brand-blue-light">路径 A · 强势回调</div>
              <div className="mt-0.5 text-[9px] leading-4 text-text-tertiary">
                市场、行业、RPS 和结构都达标后，等待缩量回踩。
              </div>
            </div>
            <ToggleGroup
              label="强势路径市场状态"
              values={form.market_states}
              options={['M0', 'M1', 'M2', 'M3', 'M4'].map((value) => ({ value, label: value }))}
              onChange={(values) => setList('market_states', values)}
            />
            <ToggleGroup
              label="强势路径行业状态"
              values={form.industry_states}
              options={['I0', 'I1', 'I2', 'I3', 'I4'].map((value) => ({ value, label: value }))}
              onChange={(values) => setList('industry_states', values)}
            />
            <label className="flex items-center justify-between rounded border border-border-primary bg-bg-primary px-2 py-1.5 text-[10px] text-text-secondary">
              必须站在 MA50 上方
              <input
                type="checkbox"
                checked={form.require_above_ma50}
                onChange={(event) => setForm((current) => ({
                  ...current,
                  require_above_ma50: event.target.checked,
                }))}
                className="accent-blue-500"
              />
            </label>
            <label className="flex items-center justify-between rounded border border-brand-blue/35 bg-brand-blue/10 px-2 py-1.5 text-[10px] text-brand-blue-light">
              允许 MA10 趋势支撑
              <input
                type="checkbox"
                checked={form.trend_include_ma10_support}
                onChange={(event) => setForm((current) => ({
                  ...current,
                  trend_include_ma10_support: event.target.checked,
                }))}
                className="accent-blue-500"
              />
            </label>
            <label className="flex items-center justify-between rounded border border-warning/45 bg-warning/10 px-2 py-2 text-[10px] font-bold text-warning">
              启用路径 B · 领先修复
              <input
                type="checkbox"
                checked={form.repair_path_enabled}
                onChange={(event) => setForm((current) => ({
                  ...current,
                  repair_path_enabled: event.target.checked,
                }))}
                className="accent-amber-500"
              />
            </label>
            {form.repair_path_enabled && (
              <div className="space-y-3 rounded border border-warning/25 bg-bg-primary/55 p-2">
                <p className="text-[9px] leading-4 text-text-tertiary">
                  允许领先股在市场/行业尚未升级时先入区，但 M0、跌破 MA50 过深、放量下跌和止损过宽仍会硬拦截。
                </p>
                <ToggleGroup
                  label="修复路径市场状态"
                  values={form.repair_market_states}
                  options={['M0', 'M1', 'M2', 'M3', 'M4'].map((value) => ({ value, label: value }))}
                  onChange={(values) => setList('repair_market_states', values)}
                />
                <ToggleGroup
                  label="修复路径行业状态"
                  values={form.repair_industry_states}
                  options={['I0', 'I1', 'I2', 'I3', 'I4'].map((value) => ({ value, label: value }))}
                  onChange={(values) => setList('repair_industry_states', values)}
                />
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
            <div className="col-span-full text-[10px] font-black text-brand-blue-light">路径 A · 强势回调参数</div>
            <NumberField label="RPS50 下限" value={form.rps50_min} min={0} max={100} onChange={(value) => setNumber('rps50_min', value)} />
            <NumberField label="结构分下限" value={form.structure_score_min} min={0} max={100} onChange={(value) => setNumber('structure_score_min', value)} />
            <NumberField label="最小回撤" value={form.pullback_min_pct} min={0} max={100} suffix="%" onChange={(value) => setNumber('pullback_min_pct', value)} />
            <NumberField label="最大回撤" value={form.pullback_max_pct} min={0} max={100} suffix="%" onChange={(value) => setNumber('pullback_max_pct', value)} />
            <NumberField label={form.trend_include_ma10_support ? '接近均线（MA10/MA20/MA50）' : '接近均线（MA20/MA50）'} value={form.near_ma_pct} min={0} max={20} step={0.5} suffix="%" onChange={(value) => setNumber('near_ma_pct', value)} />
            <NumberField label="量能 / 20日均量" value={form.volume_ratio_max_pct} min={0} max={200} suffix="%" onChange={(value) => setNumber('volume_ratio_max_pct', value)} />
            <NumberField label="连续缩量天数" value={form.volume_shrink_days} min={2} max={10} onChange={(value) => setNumber('volume_shrink_days', value)} />
            <NumberField label="最大止损距离" value={form.max_stop_loss_pct} min={0.1} max={30} step={0.5} suffix="%" onChange={(value) => setNumber('max_stop_loss_pct', value)} />
            <NumberField label="追高过滤" value={form.chase_limit_pct} min={0} max={20} step={0.5} suffix="%" onChange={(value) => setNumber('chase_limit_pct', value)} />
            <NumberField label="回调区确认窗口" value={form.setup_valid_days} min={1} max={20} suffix="交易日" onChange={(value) => setNumber('setup_valid_days', value)} />
            <NumberField label="价格结构回看" value={form.structure_lookback_days} min={7} max={30} suffix="交易日" onChange={(value) => setNumber('structure_lookback_days', value)} />
            <NumberField label="双底价格容差" value={form.double_bottom_tolerance_pct} min={0.1} max={10} step={0.5} suffix="%" onChange={(value) => setNumber('double_bottom_tolerance_pct', value)} />
            <NumberField label="MACD 绿柱收敛" value={form.macd_improve_days} min={1} max={3} suffix="连续日" onChange={(value) => setNumber('macd_improve_days', value)} />
            <NumberField label="触发冷却" value={form.cooldown_trade_days} min={0} max={60} suffix="交易日" onChange={(value) => setNumber('cooldown_trade_days', value)} />
            {form.repair_path_enabled && (
              <>
                <div className="col-span-full mt-1 border-t border-border-secondary pt-2 text-[10px] font-black text-warning">路径 B · 领先修复参数</div>
                <NumberField label="入区综合分" value={form.repair_setup_score_min} min={0} max={100} onChange={(value) => setNumber('repair_setup_score_min', value)} />
                <NumberField label="触发综合分" value={form.repair_trigger_score_min} min={0} max={100} onChange={(value) => setNumber('repair_trigger_score_min', value)} />
                <NumberField label="最大回撤" value={form.repair_pullback_max_pct} min={5} max={60} suffix="%" onChange={(value) => setNumber('repair_pullback_max_pct', value)} />
                <NumberField label="MA50 下方容忍" value={form.repair_ma50_below_max_pct} min={0} max={30} suffix="%" onChange={(value) => setNumber('repair_ma50_below_max_pct', value)} />
                <NumberField label="量能 / 20日均量" value={form.repair_volume_ratio_max_pct} min={20} max={200} suffix="%" onChange={(value) => setNumber('repair_volume_ratio_max_pct', value)} />
                <NumberField label="反转阳线量能上限" value={form.repair_reversal_volume_ratio_max_pct} min={20} max={200} suffix="%" onChange={(value) => setNumber('repair_reversal_volume_ratio_max_pct', value)} />
                <NumberField label="建设性整理最大跌幅" value={form.constructive_max_decline_pct} min={0.1} max={5} step={0.1} suffix="%" onChange={(value) => setNumber('constructive_max_decline_pct', value)} />
                <NumberField label="RPS 最低锚点" value={form.repair_min_rps_anchor} min={0} max={100} onChange={(value) => setNumber('repair_min_rps_anchor', value)} />
                <NumberField label="市场改善分" value={form.market_rebound_delta_min} min={0} max={100} onChange={(value) => setNumber('market_rebound_delta_min', value)} />
                <NumberField label="行业改善分" value={form.industry_rebound_delta_min} min={0} max={100} onChange={(value) => setNumber('industry_rebound_delta_min', value)} />
              </>
            )}
          </div>
        </div>

        <div className="mt-3 grid gap-3 rounded-md border border-success/30 bg-success/5 p-2.5 lg:grid-cols-[1fr_auto]">
          <div className="space-y-2">
            <ToggleGroup
              label="两条路径共享确认（任一满足即可）"
              values={selectedSharedConfirmationMethods}
              options={sharedConfirmationOptions}
              onChange={(values) => setConfirmationSubset('shared', values)}
            />
            <p className="text-[9px] leading-4 text-text-tertiary">
              反转多因子与结构 + MACD 用于较早确认；突破前高和重上 MA10 属于偏右侧确认，可按操作习惯选用。勾选项之间是“或”的关系，不要求同时满足。
            </p>
            {form.repair_path_enabled && (
              <div className="rounded border border-warning/25 bg-warning/5 p-2">
                <ToggleGroup
                  label="路径 B 专属确认"
                  values={selectedRepairConfirmationMethods}
                  options={[{ value: 'constructive_consolidation', label: '建设性整理' }]}
                  onChange={(values) => setConfirmationSubset('repair', values)}
                />
                <p className="mt-1 text-[9px] leading-4 text-text-tertiary">
                  建设性整理只用于路径 B：允许受控小阴线，但必须缩量、低点抬高、守住 MA10/MA20，且 MACD 收敛或翻红。
                </p>
              </div>
            )}
          </div>
          <div className="w-full lg:w-48">
            <NumberField
              label="反转多因子最少共振数"
              value={form.repair_confirmation_min_count}
              min={2}
              max={4}
              suffix="/ 4"
              onChange={(value) => setNumber('repair_confirmation_min_count', value)}
            />
          </div>
        </div>

        {invalidForm && (
          <div className="mt-2 rounded border border-danger/40 bg-danger/10 px-2 py-1.5 text-[10px] text-danger">
            {repairValidationErrors[0]
              || '市场、行业和共享确认方式至少各选一项；请检查回撤、确认窗口、结构回看、双底容差和 MACD 参数范围。'}
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            disabled={busy || invalidForm}
            onClick={() => saveMutation.mutate(false)}
            className="inline-flex h-8 items-center gap-1 rounded border border-border-primary bg-bg-primary px-3 text-[10px] font-bold text-text-secondary disabled:opacity-40"
          >
            <Save size={11} /> 保存草稿
          </button>
          <button
            type="button"
            disabled={busy || invalidForm}
            onClick={() => saveMutation.mutate(true)}
            className="inline-flex h-8 items-center gap-1 rounded border border-success/45 bg-success/10 px-3 text-[10px] font-bold text-success disabled:opacity-40"
          >
            <ShieldCheck size={11} /> 保存并启用
          </button>
        </div>
      </section>

      <section className="rounded-lg border border-border-primary bg-bg-primary/30 p-3">
        <div className="mb-3 flex items-center gap-2">
          <Play size={13} className="text-success" />
          <h3 className="text-[12px] font-black text-white">重新遍历历史机会</h3>
          <span className="text-[9px] text-text-tertiary">使用当前表单生成独立草稿版本，不触发邮件</span>
        </div>
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-5">
          <label className="text-[10px] text-text-tertiary">
            开始日期
            <select value={startDate} onChange={(event) => setStartDate(event.target.value)} className="mt-1 h-8 w-full rounded border border-border-primary bg-bg-secondary px-2 font-mono text-[11px] text-white">
              {sortedDates.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <label className="text-[10px] text-text-tertiary">
            结束日期
            <select value={endDate} onChange={(event) => setEndDate(event.target.value)} className="mt-1 h-8 w-full rounded border border-border-primary bg-bg-secondary px-2 font-mono text-[11px] text-white">
              {sortedDates.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <label className="text-[10px] text-text-tertiary">
            遍历范围
            <select value={scope} onChange={(event) => setScope(event.target.value as ScanScope)} className="mt-1 h-8 w-full rounded border border-border-primary bg-bg-secondary px-2 text-[11px] text-white">
              <option value="pools">指定分组</option>
              <option value="all_groups">全部分组</option>
              <option value="monitored_groups">仅持续跟踪分组</option>
            </select>
          </label>
          <div className="rounded border border-border-secondary bg-bg-secondary/40 px-2 py-1.5">
            <div className="text-[9px] text-text-tertiary">预计规模</div>
            <div className="mt-1 font-mono text-[11px] font-bold text-white">
              {selectedDates.length} 日 × 约 {estimatedStocks} 股
            </div>
            <div className="mt-0.5 text-[9px] text-text-tertiary">后端会按股票代码去重</div>
          </div>
          <button
            type="button"
            disabled={busy || invalidScan || scan?.status === 'RUNNING' || scan?.status === 'PENDING'}
            onClick={() => scanMutation.mutate()}
            className="mt-[14px] inline-flex h-8 items-center justify-center gap-1 rounded border border-success/50 bg-success/15 px-3 text-[11px] font-black text-success disabled:opacity-40"
          >
            {scanMutation.isPending ? <RefreshCw size={12} className="animate-spin" /> : <Play size={12} />}
            遍历机会
          </button>
        </div>

        {scope === 'pools' && (
          <div className="mt-2 flex flex-wrap gap-1.5 rounded border border-border-secondary bg-bg-secondary/30 p-2">
            {pools.map((pool) => {
              const active = poolIds.includes(pool.pool_id);
              return (
                <button
                  key={pool.pool_id}
                  type="button"
                  onClick={() => setPoolIds((current) => active
                    ? current.filter((value) => value !== pool.pool_id)
                    : [...current, pool.pool_id])}
                  className={clsx(
                    'rounded border px-2 py-1 text-[10px]',
                    active
                      ? 'border-brand-blue/55 bg-brand-blue/15 text-brand-blue-light'
                      : 'border-border-primary text-text-tertiary',
                  )}
                >
                  {pool.name} · {Number(pool.stock_count || 0)}
                </button>
              );
            })}
          </div>
        )}

        {(notice || error) && (
          <div className={clsx(
            'mt-2 rounded border px-2 py-1.5 text-[10px]',
            error ? 'border-danger/40 bg-danger/10 text-danger' : 'border-brand-blue/35 bg-brand-blue/10 text-brand-blue-light',
          )}>
            {error ? messageFromError(error) : notice}
          </div>
        )}

        {scan && (
          <div className="mt-3 rounded-md border border-border-secondary bg-bg-secondary/40 p-2">
            <div className="flex items-center gap-2 text-[10px]">
              <span className="font-black text-white">扫描 {scan.status}</span>
              <span className="font-mono text-text-tertiary">规则 v{scan.rule_version}</span>
              <span className="ml-auto font-mono text-text-secondary">
                {scan.processed_cells}/{scan.total_cells} · {scan.progress_pct.toFixed(1)}%
              </span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-bg-primary">
              <div className="h-full bg-brand-blue transition-all" style={{ width: `${Math.min(100, scan.progress_pct)}%` }} />
            </div>
            {scan.status === 'FAILED' && <div className="mt-1.5 text-[10px] text-danger">{scan.error || '遍历失败'}</div>}
          </div>
        )}
      </section>

      {scan?.status === 'COMPLETED' && (
        <section className="rounded-lg border border-border-primary bg-bg-primary/30 p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="text-[12px] font-black text-white">历史机会结果</h3>
            <span className="font-mono text-[9px] text-text-tertiary">
              机会状态 {scan.opportunity_count} 条 · 当前筛选 {resultsQuery.data?.count || 0} 条
            </span>
            <select value={resultDate} onChange={(event) => setResultDate(event.target.value)} className="ml-auto h-7 rounded border border-border-primary bg-bg-secondary px-2 text-[10px] text-white">
              <option value="">全部日期</option>
              {selectedDates.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
            <select value={resultPool} onChange={(event) => setResultPool(event.target.value)} className="h-7 rounded border border-border-primary bg-bg-secondary px-2 text-[10px] text-white">
              <option value="">全部分组</option>
              {selectedPools.map((pool) => <option key={pool.pool_id} value={pool.pool_id}>{pool.name}</option>)}
            </select>
            <select value={resultState} onChange={(event) => setResultState(event.target.value)} className="h-7 rounded border border-border-primary bg-bg-secondary px-2 text-[10px] text-white">
              <option value="">全部状态</option>
              {Object.entries(stateLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <div className="max-h-[360px] overflow-auto rounded border border-border-secondary custom-scrollbar">
            <table className="w-full min-w-[1120px] border-collapse text-[10px]">
              <thead className="sticky top-0 z-10 bg-bg-secondary text-text-tertiary">
                <tr>
                  <th className="px-2 py-2 text-left">日期</th>
                  <th className="px-2 py-2 text-left">股票</th>
                  <th className="px-2 py-2 text-left">状态</th>
                  <th className="px-2 py-2 text-left">路径 / 置信</th>
                  <th className="px-2 py-2 text-right">现价</th>
                  <th className="px-2 py-2 text-right">回调区</th>
                  <th className="px-2 py-2 text-right">触发 / 失效</th>
                  <th className="px-2 py-2 text-left">为什么</th>
                </tr>
              </thead>
              <tbody>
                {resultRows.map((row) => {
                  const factorLabels = describeWatchFactors(row.factors);
                  const setupPath = row.factors?.setup_path;
                  return (
                    <tr
                      key={`${row.trade_date}-${row.symbol}`}
                      onClick={() => { onSelectSymbol(row.symbol); onClose(); }}
                      className="cursor-pointer border-t border-border-secondary/70 hover:bg-brand-blue/10"
                    >
                      <td className="px-2 py-1.5 font-mono text-text-secondary">{row.trade_date}</td>
                      <td className="px-2 py-1.5"><b className="text-white">{row.name || row.symbol}</b> <span className="font-mono text-brand-blue-light">{row.symbol}</span></td>
                      <td className="px-2 py-1.5"><span className={clsx(
                        'rounded border px-1.5 py-0.5 font-bold',
                        row.watch_state === 'TRIGGERED' && 'border-success/45 text-success',
                        row.watch_state === 'IN_ZONE' && 'border-warning/45 text-warning',
                        row.watch_state === 'UNKNOWN' && 'border-danger/45 text-danger',
                        !['TRIGGERED', 'IN_ZONE', 'UNKNOWN'].includes(row.watch_state) && 'border-border-primary text-text-secondary',
                      )}>{stateLabels[row.watch_state] || row.watch_state}</span></td>
                      <td className="px-2 py-1.5">
                        <div className={clsx(
                          'font-bold',
                          setupPath === 'REPAIR_REVERSAL' ? 'text-warning' : 'text-brand-blue-light',
                        )}>
                          {setupPathLabel(setupPath)}
                        </div>
                        {factorLabels[1] && (
                          <div className="mt-0.5 font-mono text-[9px] text-text-tertiary">{factorLabels[1]}</div>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono text-white">{formatPrice(row.current_price)}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-text-secondary">{formatPrice(row.zone_low)}–{formatPrice(row.zone_high)}</td>
                      <td className="px-2 py-1.5 text-right font-mono"><span className="text-success">{formatPrice(row.trigger_price)}</span> / <span className="text-danger">{formatPrice(row.invalidation_price)}</span></td>
                      <td
                        className="max-w-[360px] truncate px-2 py-1.5 text-text-secondary"
                        title={[row.reason, ...factorLabels].filter(Boolean).join(' · ')}
                      >
                        {row.reason || '-'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!resultsQuery.isLoading && resultRows.length === 0 && (
              <div className="flex h-24 items-center justify-center text-[10px] text-text-tertiary">当前筛选没有结果</div>
            )}
            {resultsQuery.isLoading && (
              <div className="flex h-24 items-center justify-center text-[10px] text-text-tertiary"><RefreshCw size={12} className="mr-1 animate-spin" />读取结果</div>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

export default function WatchRuleScannerPanel({
  pools,
  dates,
  selectedPoolId,
  onSelectSymbol,
}: {
  pools: PoolOption[];
  dates: string[];
  selectedPoolId: string;
  onSelectSymbol: (symbol: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const configQuery = useQuery<WatchConfigResponse>({
    queryKey: ['watchConfig'],
    queryFn: () => watchApi.getConfig() as Promise<WatchConfigResponse>,
    enabled: open,
  });

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-brand-blue/45 bg-brand-blue/10 px-2.5 text-[10px] font-bold text-brand-blue-light transition-colors hover:bg-brand-blue/20"
        title="调整低吸规则并重新遍历历史机会"
      >
        <Settings2 size={12} /> 规则与遍历
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title="低吸规则与历史机会遍历" size="full">
        {configQuery.isLoading ? (
          <div className="flex h-48 items-center justify-center text-[11px] text-text-tertiary">
            <RefreshCw size={13} className="mr-1.5 animate-spin" /> 正在读取当前规则
          </div>
        ) : configQuery.isError || !configQuery.data ? (
          <div className="flex h-48 flex-col items-center justify-center gap-2 text-[11px] text-danger">
            规则配置读取失败
            <button type="button" onClick={() => configQuery.refetch()} className="rounded border border-danger/40 px-2 py-1">重试</button>
          </div>
        ) : (
          <RuleScannerWorkbench
            configData={configQuery.data}
            pools={pools}
            dates={dates}
            selectedPoolId={selectedPoolId}
            onSelectSymbol={onSelectSymbol}
            onClose={() => setOpen(false)}
          />
        )}
      </Modal>
    </>
  );
}
