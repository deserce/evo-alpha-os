'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import MrgcDailyReportPanel, { loadReport } from './MrgcDailyReportPanel';
import { parseReportLink, reportLinkedStocks, reportContextPool, REPORT_LINKED_POOL_ID } from '@/lib/mrgcReportSelection';
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Bell,
  BellOff,
  BriefcaseBusiness,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Crosshair,
  Database,
  ExternalLink,
  FileText,
  Folder,
  FolderPlus,
  Landmark,
  ListPlus,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  RefreshCw,
  Search,
  ShieldCheck,
  Star,
  Trash2,
  X,
} from 'lucide-react';
import { quantApi, stockApi, watchApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import { useSidebarScroll } from './useSidebarScroll';
import StockBuyPointPanel from '@/components/stock/StockBuyPointPanel';
import StockChart from '@/components/stock/StockChart';
import DailyHintsPanel, { type DailyHintsContext } from '@/components/stock/DailyHintsPanel';
import type { DailyHintSignal } from '@/lib/dailyHints';
import StockPerformanceChart from '@/components/stock/StockPerformanceChart';
import WatchRuleScannerPanel from '@/components/stock/WatchRuleScannerPanel';
import { setupPathCode } from '@/components/stock/watchRulePresentation';
import { nextStockIndex } from '@/components/stock/stockTableNavigation';
import {
  ProgressLine,
  Row,
  StageBadge,
  clamp,
  int,
  money,
  num,
  pct,
  rows,
  stageLabels,
  text,
  toNumber,
} from '@/components/workspace/CockpitPrimitives';

type StockPool = {
  pool_id: string;
  name: string;
  description?: string | null;
  sort_order?: number | null;
  is_default?: number | boolean | null;
  monitor_enabled?: number | boolean | null;
  stock_count?: number | null;
  is_smart?: boolean;
  is_report?: boolean;
  smart_state?: { status: string; target_trade_date: string; window_start?: string;
    window_end?: string; reason?: string; updated_at?: string } | null;
};

type WatchSnapshot = Row & {
  symbol?: string;
  name?: string;
  pool_id?: string;
  watch_state?: string;
  state?: string;
  data_status?: string;
  current_price?: number | null;
  zone_low?: number | null;
  zone_high?: number | null;
  trigger_price?: number | null;
  invalidation_price?: number | null;
  distance_to_trigger_pct?: number | null;
  reason?: string | null;
  why_now?: string | null;
  tracking_days?: number | null;
};

type WatchEvent = Row & {
  event_id?: string | number;
  symbol?: string;
  event_type?: string;
  severity?: string;
  status?: string;
  title?: string;
  message?: string;
  trade_date?: string;
  created_at?: string;
  snoozed_until?: string | null;
  payload?: Row | null;
};

type PoolMember = Row & {
  symbol?: string;
  name?: string;
  industry?: string;
  sector_name?: string;
  close?: number | null;
  pct_chg?: number | null;
  rps_5?: number | null;
  rps_10?: number | null;
  rps_20?: number | null;
  rps_50?: number | null;
  rps_120?: number | null;
  rps_250?: number | null;
  amount?: number | null;
  turnover_rate?: number | null;
};

type SortKey = 'symbol' | 'name' | 'close' | 'pct_chg' | 'rps_5' | 'rps_10' | 'rps_20' | 'rps_50' | 'rps_120' | 'rps_250';
type SortDir = 'asc' | 'desc';

function compareStocks(a: PoolMember, b: PoolMember, key: SortKey, dir: SortDir) {
  let av: string | number;
  let bv: string | number;
  if (key === 'symbol' || key === 'name') {
    av = String(a[key] || '');
    bv = String(b[key] || '');
  } else {
    av = toNumber(a[key]);
    bv = toNumber(b[key]);
  }
  if (av < bv) return dir === 'asc' ? -1 : 1;
  if (av > bv) return dir === 'asc' ? 1 : -1;
  return 0;
}

const DEFAULT_POOL_ID = 'watchlist';
const LOCAL_POOL_KEY = 'evoalpha.stock.pool';
const LOCAL_SYMBOL_KEY = 'evoalpha.stock.symbol';
const LOCAL_SIDEBAR_KEY = 'evoalpha.watchlist.sidebar';

function readLocalValue(key: string) {
  if (typeof window === 'undefined') return '';
  return window.localStorage.getItem(key) || '';
}

function writeLocalValue(key: string, value: string) {
  if (typeof window === 'undefined') return;
  if (value) {
    window.localStorage.setItem(key, value);
  } else {
    window.localStorage.removeItem(key);
  }
}

function normalizeSymbol(value?: string) {
  return String(value || '').trim();
}

function isBlankValue(value: unknown) {
  return value === null || value === undefined || value === '';
}

function isDefaultPool(pool?: StockPool) {
  return Boolean(pool?.is_default) || pool?.pool_id === DEFAULT_POOL_ID;
}

function isPoolMonitored(pool?: StockPool) {
  if (!pool) return false;
  if (pool.monitor_enabled === null || pool.monitor_enabled === undefined) {
    return isDefaultPool(pool);
  }
  return Boolean(pool.monitor_enabled);
}

function watchResponse(value: unknown) {
  const root = asRow(value);
  const nested = asRow(root.data);
  return Object.keys(nested).length ? nested : root;
}

function nextWeekday(value?: string) {
  const source = /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? `${value}T00:00:00` : undefined;
  const next = source ? new Date(source) : new Date();
  do {
    next.setDate(next.getDate() + 1);
  } while (next.getDay() === 0 || next.getDay() === 6);
  const year = next.getFullYear();
  const month = String(next.getMonth() + 1).padStart(2, '0');
  const day = String(next.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function compactPercent(value: unknown) {
  if (isBlankValue(value)) return '-';
  return pct(value, 2);
}

function compactRps(value: unknown) {
  if (isBlankValue(value)) return '-';
  return num(value, 0);
}

function memberIndustry(stock: PoolMember) {
  return text(stock.industry || stock.sector_name, '-');
}

type EnrichmentTab = 'profile' | 'finance' | 'f10' | 'announcements' | 'funds' | 'risk';
type EnrichmentRefreshTarget = EnrichmentTab;

const enrichmentTabs: Array<{ id: EnrichmentTab; label: string }> = [
  { id: 'profile', label: '资料' },
  { id: 'finance', label: '业绩' },
  { id: 'f10', label: 'F10' },
  { id: 'announcements', label: '公告' },
  { id: 'funds', label: '资金' },
  { id: 'risk', label: '风险' },
];

const enrichmentRefreshModules: Record<EnrichmentTab, string[]> = {
  profile: ['profile', 'finance', 'concepts', 'research'],
  finance: ['finance_summary'],
  f10: ['f10'],
  announcements: ['announcements'],
  funds: ['funds'],
  risk: ['risk'],
};

const enrichmentRefreshActions: Record<EnrichmentTab, string> = {
  profile: '同步资料',
  finance: '补齐业绩',
  f10: '同步F10',
  announcements: '同步公告',
  funds: '同步资金',
  risk: '同步风险',
};

const enrichmentRefreshTitles: Record<EnrichmentTab, string> = {
  profile: '同步当前股票基本资料',
  finance: '更新并补齐当前股票业绩数据',
  f10: '同步当前股票F10资料',
  announcements: '同步当前股票公告',
  funds: '同步当前股票资金数据',
  risk: '同步当前股票风险数据',
};

const enrichmentModuleLabels: Record<string, string> = {
  profile: '基本资料',
  finance_snapshot: '财务快照',
  financial_reports: '财务报表',
  finance_summary: '业绩',
  f10: 'F10',
  announcements: '公告',
  fund_flow: '资金流',
  concepts: '概念',
  research: '研报',
  dragon_tiger: '龙虎榜',
  margin: '融资融券',
  block_trades: '大宗交易',
  holder_num: '股东户数',
  dividends: '分红',
  lockups: '解禁',
  northbound_flow: '北向资金',
};

function asRow(value: unknown): Row {
  return (value && typeof value === 'object' ? value : {}) as Row;
}

function textRows(value: unknown): Row[] {
  return rows<Row>(value);
}

function EmptyCache() {
  return (
    <div className="flex min-h-[140px] flex-col items-center justify-center rounded-lg border border-dashed border-border-secondary/70 bg-bg-primary/30 p-4 text-center">
      <Database size={18} className="text-text-tertiary" />
      <div className="mt-2 text-[12px] font-bold text-text-secondary">数据库暂无缓存</div>
      <div className="mt-1 text-[10px] leading-4 text-text-tertiary">
        使用资料面板右上角的当前页签同步按钮按股票补齐；失败时保留真实数据源状态，不使用模拟数据。
      </div>
    </div>
  );
}

function KeyValueGrid({ items }: { items: Array<[string, unknown, ('money' | 'pct' | 'number' | 'text')?]> }) {
  return (
    <div className="grid grid-cols-2 gap-1.5">
      {items.map(([label, value, format = 'text']) => {
        const display =
          format === 'money' ? compactMoney(value) :
          format === 'pct' ? compactPercent(value) :
          format === 'number' ? compactNumber(value, 2) :
          text(value, '-');
        return (
          <div key={label} className="rounded-md border border-border-secondary/60 bg-bg-primary/45 px-2 py-1.5">
            <div className="text-[9px] text-text-tertiary">{label}</div>
            <div className="mt-0.5 truncate font-mono text-[11px] font-bold text-text-primary">{display}</div>
          </div>
        );
      })}
    </div>
  );
}

function finiteNumber(value: unknown) {
  if (isBlankValue(value)) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function compactNumber(value: unknown, digits = 2) {
  return finiteNumber(value) === null ? '-' : num(value, digits);
}

function compactMoney(value: unknown) {
  return finiteNumber(value) === null ? '-' : money(value);
}

function positivePrice(value: unknown) {
  const parsed = finiteNumber(value);
  return parsed !== null && parsed > 0 ? num(parsed, 2) : '-';
}

function firstPositive(...values: unknown[]) {
  for (const value of values) {
    const parsed = finiteNumber(value);
    if (parsed !== null && parsed > 0) return parsed;
  }
  return null;
}

function maxFinite(values: unknown[]) {
  const parsed = values
    .map((value) => finiteNumber(value))
    .filter((value): value is number => value !== null);
  return parsed.length ? Math.max(...parsed) : null;
}

function compactSignedPercent(value: unknown) {
  const parsed = finiteNumber(value);
  if (parsed === null) return '-';
  return `${parsed >= 0 ? '+' : ''}${parsed.toFixed(2)}%`;
}

function compactCount(value: unknown) {
  const parsed = finiteNumber(value);
  if (parsed === null) return '-';
  const abs = Math.abs(parsed);
  if (abs >= 100000000) return `${(parsed / 100000000).toFixed(abs >= 1000000000 ? 1 : 2)}亿`;
  if (abs >= 10000) return `${(parsed / 10000).toFixed(abs >= 1000000 ? 0 : 1)}万`;
  return parsed.toFixed(0);
}

function QuoteMetric({
  label,
  value,
  tone = 'default',
  compact = false,
}: {
  label: string;
  value: string;
  tone?: 'up' | 'down' | 'hot' | 'default';
  compact?: boolean;
}) {
  return (
    <div title={`${label}：${value}`} className={clsx(
      'min-w-0 rounded-md border border-border-secondary/55 bg-bg-primary/45 py-1',
      compact ? 'px-1.5' : 'px-2',
    )}>
      <div className="truncate text-[9px] font-bold text-text-tertiary">{label}</div>
      <div
        className={clsx(
          'truncate font-mono text-[11px] font-black',
          !compact && 'mt-0.5',
          tone === 'up' && 'text-danger',
          tone === 'down' && 'text-success',
          tone === 'hot' && 'text-warning',
          tone === 'default' && 'text-text-primary',
        )}
      >
        {value}
      </div>
    </div>
  );
}

function MarketDataStrip({
  tradeDate,
  close,
  pctChg,
  turnover,
  amount,
  volume,
  totalMarketValue,
  circMarketValue,
  peTtm,
  pbValue,
  volumeRatio,
  limitText,
  compact = false,
}: {
  tradeDate: unknown;
  close: unknown;
  pctChg: unknown;
  turnover: unknown;
  amount: unknown;
  volume: unknown;
  totalMarketValue: unknown;
  circMarketValue: unknown;
  peTtm: unknown;
  pbValue: unknown;
  volumeRatio: unknown;
  limitText: string;
  compact?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const pctValue = finiteNumber(pctChg);
  const peValue = finiteNumber(peTtm);
  const metrics: Array<{ label: string; value: string; tone?: 'up' | 'down' | 'hot' | 'default' }> = [
    { label: '收盘', value: compactNumber(close, 2), tone: pctValue === null ? 'default' : pctValue >= 0 ? 'up' : 'down' },
    { label: '涨跌幅', value: compactSignedPercent(pctChg), tone: pctValue === null ? 'default' : pctValue >= 0 ? 'up' : 'down' },
    { label: '换手率', value: compactPercent(turnover) },
    { label: '成交额', value: compactMoney(amount) },
    { label: '成交量', value: compactCount(volume) },
    { label: '量比', value: compactNumber(volumeRatio, 2) },
    { label: '总/流市值', value: `${compactMoney(totalMarketValue)} / ${compactMoney(circMarketValue)}` },
    { label: 'PE(TTM)', value: compactNumber(peTtm, 2), tone: peValue !== null && peValue > 120 ? 'hot' : 'default' },
    { label: 'PB', value: compactNumber(pbValue, 2) },
    { label: '涨跌停', value: limitText },
  ];
  return (
    <div className="shrink-0 border-b border-border-secondary/70 bg-bg-primary/20 px-2 py-2">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-[10px] font-black text-text-secondary">行情数据</span>
        <span className="ml-auto font-mono text-[9px] text-text-tertiary">{text(tradeDate, '-')}</span>
        {compact && <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((previous) => !previous)}
          className="shrink-0 text-[9px] font-bold text-brand-blue-light hover:text-white"
        >
          {expanded ? '收起行情' : '更多行情 +4'}
        </button>}
      </div>
      <div className={compact ? 'grid grid-cols-4 gap-1' : 'grid grid-cols-2 gap-1.5'}>
        {(compact ? metrics.slice(0, 4) : metrics).map((item) => (
          <QuoteMetric key={item.label} {...item} compact={compact} />
        ))}
      </div>
      {compact && <div className="mt-1 grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-1">
        {metrics.slice(6, 8).map((item) => <QuoteMetric key={item.label} {...item} compact />)}
      </div>}
      {compact && expanded && <div className="mt-1.5 grid max-h-32 grid-cols-2 gap-1.5 overflow-y-auto custom-scrollbar">
        {[...metrics.slice(4, 6), ...metrics.slice(8)].map((item) => <QuoteMetric key={item.label} {...item} />)}
      </div>}
    </div>
  );
}

function yesNo(ok: boolean | null) {
  if (ok === null) return '-';
  return ok ? '是' : '否';
}

function CheckLine({
  label,
  value,
  ok,
  sub,
}: {
  label: string;
  value: string;
  ok: boolean | null;
  sub?: string;
}) {
  return (
    <div className="grid grid-cols-[82px_minmax(0,1fr)_18px] items-center gap-1 border-b border-border-secondary/50 py-1 last:border-b-0">
      <div className="text-[10px] font-bold text-text-tertiary">{label}</div>
      <div className="min-w-0">
        <div className={ok === false ? 'truncate font-mono text-[11px] font-black text-warning' : 'truncate font-mono text-[11px] font-black text-text-primary'}>
          {value}
        </div>
        {sub ? <div className="truncate text-[9px] text-text-tertiary">{sub}</div> : null}
      </div>
      <div
        className={clsx(
          'flex h-4 w-4 items-center justify-center rounded-full border text-[10px] font-black',
          ok === null
            ? 'border-border-secondary text-text-tertiary'
            : ok
              ? 'border-success/50 bg-success/15 text-success'
              : 'border-danger/50 bg-danger/15 text-danger',
        )}
      >
        {ok === null ? '-' : ok ? '✓' : '×'}
      </div>
    </div>
  );
}

function RpsScore({ value }: { value: unknown }) {
  const parsed = finiteNumber(value);
  return (
    <span
      className={clsx(
        'font-mono font-black',
        parsed === null ? 'text-text-tertiary' : parsed >= 90 ? 'text-danger' : parsed >= 70 ? 'text-warning' : 'text-success',
      )}
    >
      {parsed === null ? '-' : num(parsed, 0)}
    </span>
  );
}

function RpsStrip({
  title,
  name,
  periods,
  source,
  prefix = 'rps_',
}: {
  title: string;
  name: string;
  periods: number[];
  source: Row;
  prefix?: string;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-border-secondary/60 bg-bg-primary/35">
      <div className="grid grid-cols-[82px_repeat(6,minmax(34px,1fr))] border-b border-border-secondary/60 bg-bg-tertiary/35 text-center text-[10px] font-bold text-text-tertiary">
        <div className="px-1.5 py-1 text-left text-text-secondary">{title}</div>
        {periods.map((period) => (
          <div key={period} className="border-l border-border-secondary/50 px-1 py-1">{period}</div>
        ))}
      </div>
      <div className="grid grid-cols-[82px_repeat(6,minmax(34px,1fr))] text-center text-[11px]">
        <div className="truncate px-1.5 py-1 text-left font-bold text-brand-blue-light">{name}</div>
        {periods.map((period) => (
          <div key={period} className="border-l border-border-secondary/50 px-1 py-1">
            <RpsScore value={source[`${prefix}${period}`]} />
          </div>
        ))}
      </div>
    </div>
  );
}

function ResearchLinkList({
  stockReports,
  industryReports,
}: {
  stockReports: Row[];
  industryReports: Row[];
}) {
  const reports = [...stockReports.slice(0, 2), ...industryReports.slice(0, 2)];
  if (!reports.length) {
    return <span className="text-[10px] text-text-tertiary">研报暂无缓存</span>;
  }
  return (
    <div className="flex min-w-0 flex-wrap gap-1">
      {reports.slice(0, 3).map((report, index) => {
        const url = text(report.pdf_url || report.url, '');
        const label = stockReports.includes(report) ? `研报${index + 1}` : `行业${index + 1}`;
        return url ? (
          <a
            key={`${text(report.report_id)}-${index}`}
            href={url}
            target="_blank"
            rel="noreferrer"
            className="rounded border border-brand-blue/30 bg-brand-blue/10 px-1.5 py-0.5 text-[10px] font-bold text-brand-blue-light hover:border-brand-blue-light"
            title={text(report.title, label)}
          >
            {label}
          </a>
        ) : (
          <span key={`${text(report.report_id)}-${index}`} className="rounded border border-border-secondary px-1.5 py-0.5 text-[10px] text-text-secondary">
            {label}
          </span>
        );
      })}
    </div>
  );
}

function QuickLinks({ symbol, name }: { symbol: string; name: string }) {
  const queries = [
    ['超预期', `${symbol} ${name} 超预期`],
    ['北向资金', `${symbol} ${name} 北向资金`],
    ['成长性', `${symbol} ${name} 成长性`],
    ['财务诊断', `${symbol} ${name} 财务诊断`],
  ] as const;
  return (
    <div className="flex flex-wrap gap-1">
      {queries.map(([label, query]) => (
        <a
          key={label}
          href={`https://www.iwencai.com/unifiedwap/result?w=${encodeURIComponent(query)}`}
          target="_blank"
          rel="noreferrer"
          className="rounded border border-brand-blue/25 bg-brand-blue/10 px-1.5 py-0.5 text-[10px] font-bold text-brand-blue-light hover:border-brand-blue-light"
        >
          {label}
        </a>
      ))}
    </div>
  );
}

function ConceptBlockTable({ concepts }: { concepts: Row[] }) {
  if (!concepts.length) {
    return (
      <div className="rounded-md border border-dashed border-border-secondary/70 bg-bg-primary/25 p-2 text-[10px] text-text-tertiary">
        概念板块暂无缓存；概念 RPS 当前数据库未生成。
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-md border border-border-secondary/60 bg-bg-primary/35">
      <div className="grid grid-cols-[minmax(78px,1fr)_48px_minmax(62px,1fr)] border-b border-border-secondary/60 bg-bg-tertiary/35 text-[10px] font-bold text-text-tertiary">
        <div className="px-1.5 py-1">概念</div>
        <div className="border-l border-border-secondary/50 px-1 py-1 text-right">涨跌</div>
        <div className="border-l border-border-secondary/50 px-1.5 py-1">龙头</div>
      </div>
      {concepts.slice(0, 5).map((item, index) => (
        <div key={`${text(item.block_code)}-${index}`} className="grid grid-cols-[minmax(78px,1fr)_48px_minmax(62px,1fr)] border-b border-border-secondary/50 text-[10px] last:border-b-0">
          <div className="truncate px-1.5 py-1 font-bold text-brand-blue-light">{text(item.block_name || item.concept_name, '-')}</div>
          <div className={toNumber(item.pct_chg, Number.NaN) >= 0 ? 'border-l border-border-secondary/50 px-1 py-1 text-right font-mono text-danger' : 'border-l border-border-secondary/50 px-1 py-1 text-right font-mono text-success'}>
            {compactSignedPercent(item.pct_chg)}
          </div>
          <div className="truncate border-l border-border-secondary/50 px-1.5 py-1 text-text-secondary">{text(item.leading_stock, '-')}</div>
        </div>
      ))}
    </div>
  );
}

function FinancialGrowthTable({ reports }: { reports: Row[] }) {
  if (!reports.length) {
    return (
      <div className="rounded-md border border-dashed border-border-secondary/70 bg-bg-primary/25 p-2 text-[10px] text-text-tertiary">
        财报增长率暂无缓存
      </div>
    );
  }
  const displayRows = reports.slice(0, 4);
  const growthRows: Array<[string, string[]]> = [
    ['营收增速', ['revenue_yoy', 'revenue_up', 'revenue_growth']],
    ['利润增速', ['net_profit_yoy', 'net_profit_up', 'profit_growth']],
  ];
  return (
    <div className="overflow-hidden rounded-md border border-border-secondary/60 bg-bg-primary/35">
      <div className="grid grid-cols-[76px_repeat(4,minmax(48px,1fr))] border-b border-border-secondary/60 bg-bg-tertiary/35 text-center text-[10px] font-bold text-text-tertiary">
        <div className="px-1.5 py-1 text-left">基本面</div>
        {displayRows.map((report, index) => (
          <div key={`${text(report.report_date)}-${index}`} className="border-l border-border-secondary/50 px-1 py-1">
            {text(report.report_date, '-').slice(2)}
          </div>
        ))}
      </div>
      {growthRows.map(([label, keys]) => (
        <div key={label} className="grid grid-cols-[76px_repeat(4,minmax(48px,1fr))] text-center text-[10px]">
          <div className="truncate px-1.5 py-1 text-left text-text-secondary">{label}</div>
          {displayRows.map((report, index) => {
            const payload = asRow(report.payload);
            const value = keys.reduce<unknown>(
              (current, itemKey) => current ?? report[itemKey] ?? payload[itemKey],
              undefined,
            );
            return (
              <div key={`${keys[0]}-${index}`} className="border-l border-border-secondary/50 px-1 py-1 font-mono text-text-primary">
                {compactSignedPercent(value)}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function FinanceSummaryTable({ reports }: { reports: Row[] }) {
  if (!reports.length) {
    return (
      <div className="rounded-md border border-dashed border-border-secondary/70 bg-bg-primary/25 p-2 text-[10px] text-text-tertiary">
        财务摘要暂无缓存
      </div>
    );
  }
  const displayRows = reports.slice(0, 8);
  return (
    <div className="overflow-hidden rounded-md border border-border-secondary/60 bg-bg-primary/35">
      <div className="grid grid-cols-[58px_minmax(54px,1fr)_minmax(54px,1fr)_50px] border-b border-border-secondary/60 bg-bg-tertiary/35 text-[10px] font-bold text-text-tertiary">
        <div className="px-1.5 py-1">报告期</div>
        <div className="border-l border-border-secondary/50 px-1 py-1 text-right">单季净利</div>
        <div className="border-l border-border-secondary/50 px-1 py-1 text-right">累计净利</div>
        <div className="border-l border-border-secondary/50 px-1 py-1 text-right">增速</div>
      </div>
      {displayRows.map((report, index) => {
        const profitUp = finiteNumber(report.net_profit_up ?? report.profit_growth);
        return (
          <div
            key={`${text(report.report_date)}-${index}`}
            className="grid grid-cols-[58px_minmax(54px,1fr)_minmax(54px,1fr)_50px] border-b border-border-secondary/45 text-[10px] last:border-b-0"
          >
            <div className="truncate px-1.5 py-1 font-mono text-text-tertiary">{quarterLabel(report.report_date)}</div>
            <div className="truncate border-l border-border-secondary/45 px-1 py-1 text-right font-mono text-text-primary">
              {compactMoney(report.net_profit_single)}
            </div>
            <div className="truncate border-l border-border-secondary/45 px-1 py-1 text-right font-mono text-text-primary">
              {compactMoney(report.net_profit)}
            </div>
            <div
              className={clsx(
                'truncate border-l border-border-secondary/45 px-1 py-1 text-right font-mono font-bold',
                profitUp === null ? 'text-text-tertiary' : profitUp >= 0 ? 'text-danger' : 'text-success',
              )}
            >
              {compactSignedPercent(profitUp)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function quarterLabel(reportDate: unknown) {
  const value = text(reportDate, '');
  if (!value) return '-';
  const quarter = ({ '03': 'Q1', '06': 'Q2', '09': 'Q3', '12': 'Q4' } as Record<string, string>)[value.slice(5, 7)] || '';
  return `${value.slice(2, 4)}${quarter}`;
}

export function StockPoolAddMenu({
  symbol,
  label = '加入池',
  className,
}: {
  symbol: string;
  label?: string;
  className?: string;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [feedback, setFeedback] = useState('');
  const poolsQuery = useQuery({
    queryKey: ['stockPoolsAddMenu'],
    queryFn: () => stockApi.getPools(),
    staleTime: 60_000,
  });
  const pools = rows<StockPool>(poolsQuery.data?.pools);
  const addMutation = useMutation({
    mutationFn: ({ poolId }: { poolId: string; poolName: string }) =>
      stockApi.addPoolStock(poolId, { symbol }),
    onSuccess: (_data, variables) => {
      setFeedback(`已加入 ${variables.poolName}`);
      queryClient.invalidateQueries({ queryKey: ['stockPools'] });
      queryClient.invalidateQueries({ queryKey: ['stockPoolsAddMenu'] });
      queryClient.invalidateQueries({ queryKey: ['strategy-stock-pools'] });
      queryClient.invalidateQueries({ queryKey: ['stockPoolStocks'] });
      window.setTimeout(() => setFeedback(''), 2200);
    },
    onError: () => {
      setFeedback('加入失败');
      window.setTimeout(() => setFeedback(''), 2200);
    },
  });

  return (
    <div className={clsx('relative inline-flex flex-col items-end', className)}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={!symbol}
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-brand-blue/40 bg-brand-blue/12 px-2 text-[11px] font-black text-brand-blue-light transition hover:border-brand-blue-light/70 disabled:cursor-not-allowed disabled:opacity-45"
      >
        <ListPlus className="h-3.5 w-3.5" />
        {label}
      </button>
      {feedback ? (
        <div className="mt-1 flex items-center gap-1 whitespace-nowrap text-[10px] text-success">
          <Check className="h-3 w-3" />
          <span>{feedback}</span>
        </div>
      ) : null}
      {open ? (
        <div className="absolute right-0 top-8 z-50 max-h-60 w-48 overflow-y-auto rounded-lg border border-border-primary bg-bg-secondary p-1.5 text-left shadow-xl custom-scrollbar">
          {pools.length ? (
            pools.filter((pool) => !pool.is_smart).map((pool) => (
              <button
                key={pool.pool_id}
                type="button"
                disabled={pool.is_smart || pool.is_report || (addMutation.isPending && addMutation.variables?.poolId === pool.pool_id)}
                onClick={() => {
                  addMutation.mutate({ poolId: pool.pool_id, poolName: text(pool.name, pool.pool_id) });
                  setOpen(false);
                }}
                className="flex h-7 w-full items-center justify-between gap-2 rounded px-2 text-[11px] text-text-secondary hover:bg-brand-blue/10 hover:text-brand-blue-light disabled:cursor-wait disabled:opacity-60"
              >
                <span className="truncate">{text(pool.name, pool.pool_id)}</span>
                <span className="font-mono text-[10px] text-text-tertiary">{int(pool.stock_count)}</span>
              </button>
            ))
          ) : (
            <div className="px-2 py-2 text-[11px] text-text-tertiary">
              {poolsQuery.isLoading ? '正在读取分组' : '暂无自选分组'}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

export function StockEnrichmentPanel({
  symbol,
  selectedDate,
  hidden = false,
  compact = false,
  onHiddenChange,
  className,
}: {
  symbol: string;
  selectedDate: string;
  hidden?: boolean;
  compact?: boolean;
  onHiddenChange?: (hidden: boolean) => void;
  className?: string;
}) {
  const queryClient = useQueryClient();
  const activeTab = useGlobalStore((state) => state.stockSidebarUi.infoTab) as EnrichmentTab;
  const setSidebarUi = useGlobalStore((state) => state.setStockSidebarUi);
  const setActiveTab = (infoTab: EnrichmentTab) => setSidebarUi({ infoTab });
  const sidebarExpanded = useGlobalStore((state) => state.stockSidebarUi.expanded);
  const setSidebarExpanded = useGlobalStore((state) => state.setSidebarExpanded);
  const [refreshFeedback, setRefreshFeedback] = useState('');
  const [refreshError, setRefreshError] = useState('');
  const enrichmentQuery = useQuery({
    queryKey: ['stockEnrichment', symbol],
    queryFn: () => stockApi.getEnrichment(symbol),
    enabled: Boolean(symbol),
    staleTime: 30_000,
  });
  const financialPerformanceQuery = useQuery({
    queryKey: ['stockFinancialPerformance', symbol, 'latest'],
    queryFn: () => stockApi.getFinance(symbol, 6),
    enabled: Boolean(symbol && !hidden && activeTab === 'finance'),
    staleTime: 30_000,
    refetchOnMount: true,
    refetchOnWindowFocus: true,
  });
  const chartQuery = useQuery({
    queryKey: ['stockChartRows', symbol, selectedDate || 'latest'],
    queryFn: () => stockApi.getChartData(symbol, 'daily', selectedDate || undefined, 260),
    enabled: Boolean(symbol && !hidden),
    staleTime: 30_000,
  });
  const lifecycleQuery = useQuery({
    queryKey: ['warRoomLifecycle', selectedDate],
    queryFn: () => quantApi.getLifecycle({ tradeDate: selectedDate, limit: 1000 }),
    enabled: Boolean(symbol && selectedDate && !hidden),
    staleTime: 30_000,
  });
  const signalsQuery = useQuery({
    queryKey: ['warRoomSignals', selectedDate],
    queryFn: () => quantApi.getSignals({ tradeDate: selectedDate, limit: 1000 }),
    enabled: Boolean(symbol && selectedDate && !hidden),
    staleTime: 30_000,
  });
  const industryQuery = useQuery({
    queryKey: ['stockEnrichmentIndustry', selectedDate],
    queryFn: () => quantApi.getIndustryStrength(selectedDate, 200, 'sector_rps_20', 'desc'),
    enabled: Boolean(symbol && selectedDate && !hidden),
    staleTime: 30_000,
  });
  const refreshMutation = useMutation({
    mutationFn: (target: EnrichmentRefreshTarget) => stockApi.refreshEnrichment(symbol, {
      modules: enrichmentRefreshModules[target],
      trade_date: selectedDate || undefined,
    }),
    onMutate: () => {
      setRefreshFeedback('');
      setRefreshError('');
    },
    onSuccess: (result: unknown, target) => {
      if (target === 'finance') {
        queryClient.invalidateQueries({ queryKey: ['stockFinancialPerformance', symbol] });
      }
      queryClient.invalidateQueries({ queryKey: ['stockEnrichment', symbol] });
      queryClient.invalidateQueries({ queryKey: ['stockInfo', symbol] });
      queryClient.invalidateQueries({ queryKey: ['stockChartRows', symbol] });

      const responseRoot = asRow(result);
      const responseData = asRow(responseRoot.data);
      const responsePayload = Object.keys(responseData).length ? responseData : responseRoot;
      const refresh = asRow(responsePayload.refresh);
      const statusEntries = Object.entries(refresh).map(
        ([module, value]) => [module, asRow(value)] as const,
      );
      const failedModules = statusEntries.filter(
        ([, value]) => text(value.status, '').toLowerCase() === 'failed',
      );
      const successfulModules = statusEntries.filter(
        ([, value]) => text(value.status, '').toLowerCase() === 'ok',
      );
      const financeSummaryStatus = asRow(refresh.finance_summary);
      const moduleStatus = text(financeSummaryStatus.status, '').toLowerCase();
      if (target === 'finance' && moduleStatus === 'failed') {
        setRefreshError(
          `业绩数据更新失败：${text(financeSummaryStatus.error, '数据源返回失败')}`,
        );
        return;
      }

      if (target === 'finance') {
        const updated = finiteNumber(financeSummaryStatus.updated);
        const checked = finiteNumber(financeSummaryStatus.checked);
        const rowsUpdated = finiteNumber(financeSummaryStatus.rows);
        if (updated !== null) {
          setRefreshFeedback(`业绩数据已补齐 ${updated} 个报告期`);
        } else if (checked !== null) {
          setRefreshFeedback(`业绩数据已检查 ${checked} 个报告期`);
        } else if (rowsUpdated !== null) {
          setRefreshFeedback(`业绩数据已同步 ${rowsUpdated} 个报告期`);
        } else {
          setRefreshFeedback('业绩数据更新完成');
        }
        return;
      }

      const targetLabel = enrichmentTabs.find((tab) => tab.id === target)?.label ?? '资料';
      if (!statusEntries.length) {
        setRefreshError(`${targetLabel}资料同步未返回模块结果，请检查后端服务`);
        return;
      }
      if (failedModules.length) {
        const failedLabels = failedModules.map(
          ([module]) => enrichmentModuleLabels[module] ?? module,
        );
        const failedSummary = failedLabels.length > 3
          ? `${failedLabels.slice(0, 3).join('、')}等${failedLabels.length}项`
          : failedLabels.join('、');
        setRefreshError(
          `${targetLabel}同步部分失败：${successfulModules.length} 项成功，`
          + `${failedModules.length} 项失败（${failedSummary}）`,
        );
        return;
      }
      setRefreshFeedback(`${targetLabel}同步完成：${successfulModules.length} 个模块成功`);
    },
    onError: (error: unknown, target) => {
      const message = error instanceof Error && error.message
        ? error.message
        : '请检查后端服务和数据源状态';
      setRefreshFeedback('');
      setRefreshError(
        target === 'finance'
          ? `业绩数据更新失败：${message}`
          : `资料更新失败：${message}`,
      );
    },
  });
  const requestRefresh = () => {
    refreshMutation.mutate(activeTab);
  };

  const payload = asRow(enrichmentQuery.data);
  const profile = asRow(payload.profile);
  const valuation = asRow(profile.valuation);
  const info = asRow(profile.info);
  const concepts = textRows(profile.concepts);
  const finance = asRow(payload.finance);
  const snapshot = asRow(finance.snapshot);
  const reports = textRows(finance.reports);
  const financeSummary = textRows(finance.summary);
  const research = asRow(payload.research);
  const stockReports = textRows(research.stock_reports);
  const industryReports = textRows(research.industry_reports);
  const f10Sections = textRows(payload.f10_sections);
  const announcements = textRows(payload.announcements);
  const funds = asRow(payload.funds);
  const risk = asRow(payload.risk);
  const fundFlow = textRows(funds.fund_flow);
  const dragonTiger = textRows(funds.dragon_tiger);
  const margin = textRows(funds.margin);
  const blockTrades = textRows(funds.block_trades);
  const lockups = textRows(risk.lockups);
  const holders = textRows(risk.holder_num);
  const dividends = textRows(risk.dividends);
  const freshness = textRows(payload.freshness);
  const refreshing = refreshMutation.isPending || enrichmentQuery.isFetching;
  const financeRefreshing = refreshMutation.isPending || financialPerformanceQuery.isFetching;
  const activeRefreshing = activeTab === 'finance' ? financeRefreshing : refreshing;
  const activeRefreshAction = enrichmentRefreshActions[activeTab];
  const activeRefreshTitle = enrichmentRefreshTitles[activeTab];
  const activeRefreshLabel = refreshMutation.isPending
    ? (refreshMutation.variables === 'finance' ? '补齐中…' : '同步中…')
    : activeRefreshing ? '读取中…' : activeRefreshAction;
  const financeQueryError = financialPerformanceQuery.error instanceof Error
    ? financialPerformanceQuery.error.message
    : financialPerformanceQuery.isError ? '读取业绩数据失败' : '';
  const financeError = refreshError || financeQueryError;
  const chartRows = rows<Row>(chartQuery.data?.rows);
  const latestChart = chartRows[chartRows.length - 1] ?? {};
  const last50Rows = chartRows.slice(-50);
  const base50Row = chartRows.length > 50 ? chartRows[chartRows.length - 51] : null;
  const latestClose = finiteNumber(latestChart.close);
  const latestTurnover = finiteNumber(latestChart.turnover_rate);
  const latestAmount = latestChart.amount;
  const latestVolume = latestChart.volume;
  const base50Close = finiteNumber(base50Row?.close);
  const oneYearHigh = maxFinite(chartRows.map((row) => row.high));
  const distanceFromHigh = latestClose !== null && oneYearHigh ? ((oneYearHigh - latestClose) / oneYearHigh) * 100 : null;
  const gain50 = latestClose !== null && base50Close
    ? ((latestClose / base50Close) - 1) * 100
    : null;
  const maxPct50 = maxFinite(last50Rows.map((row) => row.pct_chg));
  const maxTurnover50 = maxFinite(last50Rows.map((row) => row.turnover_rate));
  const lifecycle = rows<Row>(lifecycleQuery.data?.rows).find((row) => text(row.symbol, '') === symbol) ?? {};
  const symbolSignals = rows<Row>(signalsQuery.data?.rows).filter((row) => text(row.symbol, '') === symbol);
  const hasPocketPivot = symbolSignals.some((row) => text(row.signal_type, '') === 'pocket_pivot');
  const sectorName = text(info.industry || info.sector_name || lifecycle.sector_name, '');
  const sectorStrength = rows<Row>(industryQuery.data?.rows).find((row) => text(row.sector_name, '') === sectorName) ?? {};
  const currentRps = {
    rps_5: latestChart.rps_5 ?? lifecycle.rps_5,
    rps_10: latestChart.rps_10 ?? lifecycle.rps_10,
    rps_20: latestChart.rps_20 ?? lifecycle.rps_20,
    rps_50: latestChart.rps_50 ?? lifecycle.rps_50,
    rps_120: latestChart.rps_120 ?? lifecycle.rps_120,
    rps_250: latestChart.rps_250 ?? lifecycle.rps_250,
  } as Row;
  const sectorRps = {
    rps_5: sectorStrength.sector_rps_5,
    rps_10: sectorStrength.sector_rps_10,
    rps_20: sectorStrength.sector_rps_20,
    rps_50: sectorStrength.sector_rps_50,
    rps_120: sectorStrength.sector_rps_120,
    rps_250: sectorStrength.sector_rps_250,
  } as Row;
  const fundRatio = latestChart.fund_hold_ratio;
  const northRatio = latestChart.northbound_hold_ratio;
  const hasFundOrNorth = finiteNumber(fundRatio) !== null || finiteNumber(northRatio) !== null;
  const stageOk = ['S4', 'S5'].includes(text(lifecycle.lifecycle_state, '')) || toNumber(lifecycle.structure_score, 0) >= 70;
  const infoMarketCap = finiteNumber(info.market_cap);
  const totalMarketValue = firstPositive(valuation.total_mv, infoMarketCap === null ? null : infoMarketCap * 100000000);
  const circMarketValue = firstPositive(valuation.circ_mv);
  const marketValueText = `${compactMoney(totalMarketValue)} / ${compactMoney(circMarketValue)}`;
  const peTtm = firstPositive(valuation.pe_ttm, info.pe);
  const pbValue = firstPositive(valuation.pb, info.pb);
  const displayName = text(info.name, symbol);
  const financialRows = reports.length ? reports : financeSummary;
  const quoteClose = latestClose ?? firstPositive(valuation.price, valuation.close);
  const quotePctChg = latestChart.pct_chg ?? valuation.pct_chg;
  const quoteTurnover = latestTurnover ?? valuation.turnover;
  const quoteAmount = latestAmount ?? valuation.amount;
  const quoteVolume = latestVolume ?? valuation.volume;
  const limitText = `${positivePrice(valuation.limit_up_price)} / ${positivePrice(valuation.limit_down_price)}`;
  const hasSnapshotMetrics = Boolean(
    text(snapshot.report_date, '') ||
    ['eps', 'roe', 'bvps', 'profit', 'income'].some((key) => finiteNumber(snapshot[key]) !== null),
  );
  const hasFinanceCache = hasSnapshotMetrics || financeSummary.length > 0 || reports.length > 0;

  const hasAnyCache = freshness.some((item) => toNumber(item.row_count) > 0);

  const infoScroll = useSidebarScroll(`info:${symbol}:${selectedDate}:${activeTab}`,
    !hidden && !enrichmentQuery.isLoading
      && (activeTab !== 'finance' || !financialPerformanceQuery.isLoading));

  if (hidden) {
    return (
      <button
        type="button"
        onClick={() => onHiddenChange?.(false)}
        className={clsx(
          'flex min-h-[120px] items-center justify-center rounded-lg border border-border-primary bg-bg-secondary/45 text-text-tertiary transition hover:border-brand-blue/60 hover:text-brand-blue-light',
          className,
        )}
        title="展开资料面板"
      >
        <div className="flex flex-col items-center gap-2">
          <PanelRightOpen size={16} />
          <span className="text-[10px] font-bold [writing-mode:vertical-rl]">资料</span>
        </div>
      </button>
    );
  }

  return (
    <aside className={clsx('flex min-h-0 flex-col overflow-hidden rounded-lg border border-border-primary bg-bg-secondary/45', className)}>
      <div data-sidebar-heading className="flex shrink-0 items-center justify-between border-b border-border-primary">
        <div className="flex items-center gap-1.5">
          <FileText size={13} className="text-brand-blue-light" />
          <span className="text-[11px] font-black text-text-primary">资料面板</span>
        </div>
        <div className="flex items-center gap-1">
          {onHiddenChange ? (
            <button
              type="button"
              onClick={() => onHiddenChange(true)}
              className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-border-secondary bg-bg-primary/60 text-text-tertiary hover:text-brand-blue-light"
              title="隐藏资料面板"
            >
              <PanelRightClose size={12} />
            </button>
          ) : null}
          <button
            type="button"
            onClick={requestRefresh}
            disabled={activeRefreshing}
            aria-label={activeRefreshTitle}
            aria-busy={activeRefreshing}
            title={activeRefreshTitle}
            className="inline-flex items-center gap-1 rounded-md border border-border-secondary bg-bg-primary/60 px-2 py-1 text-[10px] font-bold text-text-secondary hover:text-brand-blue-light disabled:opacity-50"
          >
            <RefreshCw size={11} className={activeRefreshing ? 'animate-spin' : ''} />
            {activeRefreshLabel}
          </button>
        </div>
      </div>

      <MarketDataStrip
        compact
        tradeDate={latestChart.trade_date || valuation.trade_date || selectedDate}
        close={quoteClose}
        pctChg={quotePctChg}
        turnover={quoteTurnover}
        amount={quoteAmount}
        volume={quoteVolume}
        totalMarketValue={totalMarketValue}
        circMarketValue={circMarketValue}
        peTtm={peTtm}
        pbValue={pbValue}
        volumeRatio={valuation.volume_ratio}
        limitText={limitText}
      />

      <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-border-secondary/70 px-2 py-1 custom-scrollbar">
        {enrichmentTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            disabled={refreshMutation.isPending}
            onClick={() => {
              setActiveTab(tab.id);
              setRefreshFeedback('');
              setRefreshError('');
            }}
            className={clsx(
              'shrink-0 rounded-md px-2 py-1 text-[10px] font-bold transition-colors',
              activeTab === tab.id
                ? 'bg-brand-blue/18 text-brand-blue-light ring-1 ring-brand-blue/40'
                : 'text-text-tertiary hover:bg-bg-tertiary/50 hover:text-text-primary',
              'disabled:cursor-wait disabled:opacity-50',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab !== 'finance' && (refreshFeedback || refreshError) ? (
        <div
          role={refreshError ? 'alert' : 'status'}
          aria-live="polite"
          className={clsx(
            'shrink-0 border-b px-2 py-1.5 text-[10px]',
            refreshError
              ? 'border-warning/20 bg-warning/5 text-warning'
              : 'border-success/20 bg-success/5 text-success',
          )}
        >
          {refreshError || refreshFeedback}
        </div>
      ) : null}

      <div data-sidebar-info-scroll ref={infoScroll.ref} onScroll={infoScroll.onScroll} className="min-h-0 flex-1 overflow-y-auto p-2 custom-scrollbar">
        {activeTab !== 'finance' && !hasAnyCache && !enrichmentQuery.isLoading && (
          <EmptyCache />
        )}

        {activeTab === 'profile' && (
          <div className="space-y-2">
            <FinancialGrowthTable reports={financialRows} />

            <div className="rounded-md border border-border-secondary/60 bg-bg-primary/35 p-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <div className="text-[10px] font-black text-text-secondary">作战检查</div>
                <div className="font-mono text-[9px] text-text-tertiary">{text(latestChart.trade_date || selectedDate, '-')}</div>
              </div>
              <CheckLine
                label="基金+北向"
                value={yesNo(hasFundOrNorth)}
                ok={hasFundOrNorth}
                sub={`基金 ${compactPercent(fundRatio)} / 北向 ${compactPercent(northRatio)}`}
              />
              <CheckLine
                label="收盘/年高"
                value={`${compactNumber(latestClose, 2)} / ${compactNumber(oneYearHigh, 2)}`}
                ok={distanceFromHigh === null ? null : distanceFromHigh <= 5}
                sub={distanceFromHigh === null ? '数据库暂无一年高点' : `距离 ${num(distanceFromHigh, 1)}%`}
              />
              <CheckLine
                label="涨幅/Max50"
                value={`${compactSignedPercent(gain50)} / ${compactSignedPercent(maxPct50)}`}
                ok={gain50 === null ? null : gain50 > 0}
              />
              <CheckLine
                label="换手/Max50"
                value={`${compactPercent(latestTurnover)} / ${compactPercent(maxTurnover50)}`}
                ok={latestTurnover === null || maxTurnover50 === null ? null : latestTurnover >= maxTurnover50 * 0.55}
              />
              <CheckLine
                label="口袋量能"
                value={yesNo(hasPocketPivot)}
                ok={symbolSignals.length ? hasPocketPivot : null}
                sub={symbolSignals.length ? `${symbolSignals.length} 个当日买点信号` : '当日未触发买点信号'}
              />
              <CheckLine
                label="股魔二阶段"
                value={yesNo(stageOk)}
                ok={Object.keys(lifecycle).length ? stageOk : null}
                sub={`生命周期 ${text(lifecycle.lifecycle_state, '-')} / 结构 ${num(lifecycle.structure_score, 0)}`}
              />
            </div>

            <RpsStrip
              title="个股/RPS"
              name={displayName}
              periods={[5, 10, 20, 50, 120, 250]}
              source={currentRps}
            />
            <RpsStrip
              title="行业/RPS"
              name={sectorName || '-'}
              periods={[5, 10, 20, 50, 120, 250]}
              source={sectorRps}
            />

            <div className="rounded-md border border-border-secondary/60 bg-bg-primary/35 p-2">
              <div className="grid gap-1.5">
                <div className="grid grid-cols-[54px_minmax(0,1fr)] items-center gap-1.5">
                  <span className="text-[10px] font-bold text-text-tertiary">研报</span>
                  <ResearchLinkList stockReports={stockReports} industryReports={industryReports} />
                </div>
                <div className="grid grid-cols-[54px_minmax(0,1fr)] items-start gap-1.5">
                  <span className="pt-0.5 text-[10px] font-bold text-text-tertiary">问财</span>
                  <QuickLinks symbol={symbol} name={displayName} />
                </div>
                <div className="grid grid-cols-[54px_minmax(0,1fr)] items-center gap-1.5">
                  <span className="text-[10px] font-bold text-text-tertiary">上市日期</span>
                  <span className="font-mono text-[11px] font-bold text-text-primary">{text(info.list_date, '-')}</span>
                </div>
              </div>
            </div>

            <ConceptBlockTable concepts={concepts} />
          </div>
        )}

        {activeTab === 'finance' && (
          <div className="h-full space-y-2">
            <StockPerformanceChart
              data={financialPerformanceQuery.data}
              fitHeight
              className="h-full min-h-[340px]"
              compact={compact}
              error={financeError}
              feedback={refreshFeedback}
            />
            {hasFinanceCache && (
              <>
                {hasSnapshotMetrics ? (
                  <KeyValueGrid
                    items={[
                      ['报告期', snapshot.report_date, 'text'],
                      ['EPS', snapshot.eps, 'number'],
                      ['ROE', snapshot.roe, 'pct'],
                      ['BVPS', snapshot.bvps, 'number'],
                      ['利润', snapshot.profit, 'money'],
                      ['收入', snapshot.income, 'money'],
                    ]}
                  />
                ) : (
                  <div className="rounded-md border border-dashed border-border-secondary/70 bg-bg-primary/25 p-2 text-[10px] leading-4 text-text-tertiary">
                    财务快照暂无缓存，已优先展示财务摘要。
                  </div>
                )}
                <FinanceSummaryTable reports={financeSummary} />
                <FinancialGrowthTable reports={financialRows} />
                <div className="space-y-1">
                  {reports.length > 0 ? reports.slice(0, 12).map((item, index) => (
                    <div key={`${text(item.report_type)}-${text(item.report_date)}-${index}`} className="rounded-md border border-border-secondary/60 bg-bg-primary/35 px-2 py-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] font-bold text-text-primary">{text(item.report_type).toUpperCase()}</span>
                        <span className="font-mono text-[10px] text-text-tertiary">{text(item.report_date)}</span>
                      </div>
                    </div>
                  )) : (
                    <div className="rounded-md border border-dashed border-border-secondary/70 bg-bg-primary/25 p-2 text-[10px] leading-4 text-text-tertiary">
                      三表明细暂无缓存；刷新资料会尝试补齐资产负债表、利润表、现金流量表。
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        )}

        {activeTab === 'f10' && hasAnyCache && (
          <div className="space-y-2">
            {f10Sections.length > 0 ? f10Sections.map((item, index) => (
              <details key={`${text(item.category)}-${index}`} data-sidebar-f10-section={text(item.category)}
                open={Boolean(sidebarExpanded[`f10:${symbol}:${selectedDate}:${text(item.category)}`])}
                onToggle={(event) => {
                  const key = `f10:${symbol}:${selectedDate}:${text(item.category)}`;
                  const open = event.currentTarget.open;
                  if (Boolean(sidebarExpanded[key]) !== open) setSidebarExpanded(key, open);
                }}
                className="rounded-md border border-border-secondary/60 bg-bg-primary/35 p-2">
                <summary className="cursor-pointer text-[11px] font-bold text-text-primary">{text(item.category)}</summary>
                <p className="mt-2 whitespace-pre-wrap text-[10px] leading-4 text-text-secondary">
                  {text(item.content_digest || item.content, '数据库暂无缓存')}
                </p>
              </details>
            )) : <EmptyCache />}
          </div>
        )}

        {activeTab === 'announcements' && hasAnyCache && (
          <div className="space-y-1.5">
            {announcements.length > 0 ? announcements.map((item, index) => (
              <a
                key={`${text(item.announcement_id)}-${index}`}
                href={text(item.url, '#')}
                target="_blank"
                rel="noreferrer"
                className="block rounded-md border border-border-secondary/60 bg-bg-primary/35 px-2 py-1.5 hover:border-brand-blue/40"
              >
                <div className="line-clamp-2 text-[11px] font-bold text-text-primary">{text(item.title, '-')}</div>
                <div className="mt-1 text-[10px] text-text-tertiary">{text(item.publish_date, '-')} · {text(item.category, '公告')}</div>
              </a>
            )) : <EmptyCache />}
          </div>
        )}

        {activeTab === 'funds' && hasAnyCache && (
          <div className="space-y-2">
            <div className="rounded-md border border-border-secondary/60 bg-bg-primary/35 p-2">
              <div className="mb-1 flex items-center gap-1 text-[10px] font-bold text-text-secondary">
                <Landmark size={11} className="text-brand-blue-light" />
                资金流
              </div>
              {fundFlow.length > 0 ? fundFlow.slice(0, 8).map((item, index) => (
                <div key={`${text(item.trade_date)}-${index}`} className="flex justify-between border-t border-border-secondary/50 py-1 text-[10px]">
                  <span className="font-mono text-text-tertiary">{text(item.trade_date)}</span>
                  <span className={toNumber(item.main_net) >= 0 ? 'font-mono text-danger' : 'font-mono text-success'}>{money(item.main_net)}</span>
                </div>
              )) : <div className="text-[10px] text-text-tertiary">数据库暂无缓存</div>}
            </div>
            <KeyValueGrid
              items={[
                ['龙虎榜', dragonTiger.length, 'number'],
                ['融资融券', margin.length, 'number'],
                ['大宗交易', blockTrades.length, 'number'],
                ['北向聚合', textRows(funds.northbound_flow).length, 'number'],
              ]}
            />
          </div>
        )}

        {activeTab === 'risk' && hasAnyCache && (
          <div className="space-y-2">
            <KeyValueGrid
              items={[
                ['限售解禁', lockups.length, 'number'],
                ['股东户数', holders.length, 'number'],
                ['分红送转', dividends.length, 'number'],
                ['缓存模块', freshness.filter((item) => toNumber(item.row_count) > 0).length, 'number'],
              ]}
            />
            <div className="rounded-md border border-border-secondary/60 bg-bg-primary/35 p-2">
              <div className="mb-1 text-[10px] font-bold text-text-secondary">缓存新鲜度</div>
              <div className="space-y-1">
                {freshness.map((item, index) => (
                  <div key={`${text(item.table)}-${index}`} className="flex items-center justify-between gap-2 text-[10px]">
                    <span className="truncate font-mono text-text-tertiary">{text(item.table)}</span>
                    <span className={toNumber(item.row_count) > 0 ? 'text-success' : 'text-warning'}>
                      {text(item.status)} · {int(item.row_count)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

function EmptySelection() {
  return (
    <div className="flex h-full min-h-[400px] items-center justify-center text-center">
      <div className="max-w-sm">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-xl border border-border-primary bg-bg-secondary text-brand-blue-light">
          <ListPlus size={24} />
        </div>
        <h2 className="mt-4 text-sm font-black text-white">选择股票查看行情</h2>
        <p className="mt-2 text-[12px] leading-5 text-text-secondary">
          从左侧选择池内股票，或搜索添加新股票后，这里会展示 K 线、RPS、MACD、KDJ 与交易计划。
        </p>
      </div>
    </div>
  );
}

export function StockDetailPanel({
  symbol,
  selectedDate,
  showDetailLink = false,
  onSelectSymbol,
  unifiedHints,
  dailyHintsPanel,
  sidebarScopeKey,
}: {
  symbol: string;
  selectedDate: string;
  showDetailLink?: boolean;
  onSelectSymbol?: (symbol: string) => void;
  unifiedHints?: DailyHintsContext;
  dailyHintsPanel?: ReactNode;
  sidebarScopeKey?: string;
}) {
  const originPath = usePathname();
  const sidebarUi = useGlobalStore((state) => state.stockSidebarUi);
  const setSidebarUi = useGlobalStore((state) => state.setStockSidebarUi);
  const infoPanelHidden = sidebarUi.infoHidden;
  const setInfoPanelHidden = (infoHidden: boolean) => setSidebarUi({ infoHidden });
  const reportOpen = useGlobalStore((state) => state.reportOpen);
  const setReportOpen = useGlobalStore((state) => state.setReportOpen);
  const sidebarTab = sidebarUi.tab;
  const setSidebarTab = (tab: 'info' | 'hints') => setSidebarUi({ tab });
  const assistantOpen = reportOpen;
  const hintsOpen = Boolean(dailyHintsPanel) && !assistantOpen && sidebarTab === 'hints';
  const analysisOpen = assistantOpen || hintsOpen;
  const hintsScroll = useSidebarScroll(`hints:${sidebarScopeKey || '__single__'}:${selectedDate}`, hintsOpen && !infoPanelHidden);
  const [compactView, setCompactView] = useState<'chart' | 'analysis'>('analysis');
  useEffect(() => { if (analysisOpen) setCompactView('analysis'); }, [analysisOpen]);
  const infoQuery = useQuery({
    queryKey: ['stockInfo', symbol],
    queryFn: () => stockApi.getInfo(symbol),
    enabled: Boolean(symbol),
    staleTime: 30_000,
  });
  const klineQuery = useQuery({
    queryKey: ['stockChartRows', symbol, selectedDate || 'latest'],
    queryFn: () => stockApi.getChartData(symbol, 'daily', selectedDate || undefined, 260),
    enabled: Boolean(symbol),
    staleTime: 30_000,
  });
  const lifecycleQuery = useQuery({
    queryKey: ['warRoomLifecycle', selectedDate],
    queryFn: () => quantApi.getLifecycle({ tradeDate: selectedDate, state: undefined, limit: 1000 }),
    enabled: Boolean(selectedDate),
    staleTime: 30_000,
  });
  const signalsQuery = useQuery({
    queryKey: ['warRoomSignals', selectedDate],
    queryFn: () => quantApi.getSignals({ tradeDate: selectedDate, limit: 1000 }),
    enabled: Boolean(selectedDate),
    staleTime: 30_000,
  });
  const plansQuery = useQuery({
    queryKey: ['warRoomPlans', selectedDate],
    queryFn: () => quantApi.getTradePlans({ tradeDate: selectedDate, limit: 1000 }),
    enabled: Boolean(selectedDate),
    staleTime: 30_000,
  });

  const info = infoQuery.data ?? {};
  const kline = rows<Row>(klineQuery.data?.rows);
  const lifecycle = rows<Row>(lifecycleQuery.data?.rows).find((row) => row.symbol === symbol) ?? {};
  const signals = rows<Row>(signalsQuery.data?.rows).filter((row) => row.symbol === symbol);
  const plans = rows<Row>(plansQuery.data?.rows).filter((row) => row.symbol === symbol);
  const latest = kline[kline.length - 1] ?? {};
  const displayName = text(info.name || lifecycle.name || symbol, symbol);
  const price = latest.close ?? plans[0]?.latest_close ?? signals[0]?.entry_price;
  const portfolioPrice = finiteNumber(price);
  const portfolioPriceMicros =
    portfolioPrice !== null && portfolioPrice > 0
      ? Math.round(portfolioPrice * 1_000_000)
      : null;
  const portfolioParams = new URLSearchParams({
    symbol,
    side: 'BUY',
  });
  if (
    portfolioPriceMicros !== null
    && Number.isSafeInteger(portfolioPriceMicros)
    && portfolioPriceMicros > 0
  ) {
    portfolioParams.set('price', String(portfolioPriceMicros / 1_000_000));
  }
  const portfolioHref = `/a/portfolio?${portfolioParams.toString()}`;
  const pctChg = latest.pct_chg;
  const isUp = toNumber(pctChg) >= 0;

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-1.5">
      {/* 顶部股票信息条 */}
      <div className="relative z-30 flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 overflow-visible rounded-lg border border-border-primary bg-bg-secondary/60 px-3 py-2">
        <div className="flex items-center gap-2">
          <h2 data-selected-symbol={symbol} className="text-lg font-black text-white">{displayName}</h2>
          <span className="font-mono text-[11px] text-text-secondary">{symbol}</span>
          <StageBadge value={lifecycle.lifecycle_state} />
        </div>
        <div className="flex items-end gap-2">
          <span className={isUp ? 'font-mono text-xl font-black text-danger' : 'font-mono text-xl font-black text-success'}>
            {num(price, 2)}
          </span>
          <span className={isUp ? 'font-mono text-[11px] text-danger' : 'font-mono text-[11px] text-success'}>
            {compactPercent(pctChg)}
          </span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-1">
          <Link
            href={portfolioHref}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border-primary bg-bg-primary px-2 text-[11px] font-black text-text-secondary transition-colors hover:border-brand-blue/60 hover:text-brand-blue-light"
            title={`将 ${displayName} 加入 Portfolio`}
          >
            <BriefcaseBusiness size={12} />
            <span>加入 Portfolio</span>
          </Link>
          {showDetailLink && (
            <Link
              href={`/a/stock/${symbol}?returnTo=${encodeURIComponent(originPath === '/a/strategy' ? '/a/strategy' : '/a/watchlist')}`}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border-primary bg-bg-primary px-2 text-[11px] font-black text-text-secondary transition-colors hover:border-brand-blue/60 hover:text-brand-blue-light"
              title={`打开 ${displayName} 个股详情`}
            >
              <ExternalLink size={12} />
              <span>个股详情</span>
            </Link>
          )}
          <StockPoolAddMenu symbol={symbol} />
          {[lifecycle.market_state, lifecycle.industry_state, lifecycle.structure_state].map((item, index) => (
            <StageBadge key={`${text(item)}-${index}`} value={item || ['M?', 'I?', '结构'][index]} />
          ))}
        </div>
      </div>

      {analysisOpen && <div className="flex shrink-0 gap-2 lg:hidden" role="tablist" aria-label="个股与 AI 分析视图">
        <button type="button" role="tab" aria-selected={compactView === 'chart'} onClick={() => setCompactView('chart')} className="rounded border border-border-primary px-3 py-1 text-xs">个股行情</button>
        <button type="button" role="tab" aria-selected={compactView === 'analysis'} onClick={() => setCompactView('analysis')} className="rounded border border-border-primary px-3 py-1 text-xs">{hintsOpen ? '当日提示' : 'AI 分析'}</button>
      </div>}
      <div
        className={clsx(
          'grid min-h-0 flex-1 gap-2 overflow-y-auto lg:overflow-hidden custom-scrollbar',
          infoPanelHidden
            ? 'lg:grid-cols-[minmax(0,1fr)_44px]'
            : 'lg:grid-cols-[minmax(0,1fr)_clamp(320px,25vw,360px)]',
        )}
      >
        <div className={clsx('min-h-0 min-w-0 flex-col gap-2 overflow-y-auto overscroll-contain custom-scrollbar', analysisOpen && compactView === 'analysis' ? 'hidden lg:flex' : 'flex')}>
          {/* K线图主区域 */}
          <div data-workspace-chart className="min-h-[420px] flex-1 shrink-0 lg:min-h-[300px] rounded-lg border border-border-primary bg-bg-secondary/40 p-2">
            <StockChart symbol={symbol} endDate={selectedDate} limit={260} hintContext={unifiedHints} />
          </div>

          {/* 底部信息栏：交易计划 + 生命周期。RPS 多周期统一放在右侧资料面板。 */}
          <details className="group shrink-0 rounded-lg border border-border-primary bg-bg-secondary/40">
            <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-[11px] text-text-secondary">
              <span className="font-bold text-text-primary">买点与生命周期</span>
              <span>阶段 <b>{text(lifecycle.lifecycle_state, '待评估')}</b></span>
              <span>结构 <b className="font-mono">{num(lifecycle.structure_score, 0)}</b></span>
              <span className="ml-auto text-brand-blue-light group-open:hidden">展开详情 ▾</span>
              <span className="ml-auto hidden text-brand-blue-light group-open:inline">收起 ▴</span>
            </summary>
          <div className="grid max-h-[240px] shrink-0 gap-2 overflow-y-auto border-t border-border-secondary p-2 custom-scrollbar lg:grid-cols-[minmax(0,1fr)_220px]">
            <StockBuyPointPanel
              key={`${symbol}-${selectedDate}`}
              symbol={symbol}
              selectedDate={selectedDate}
              signals={signals}
              plans={plans}
              latest={latest}
              compact
            />

            {/* 生命周期开关 */}
            <div className="rounded-lg border border-border-primary bg-bg-secondary/60 p-2">
          <div className="mb-2 text-[10px] font-black text-text-tertiary">Evo 生命周期</div>
          <div className="grid grid-cols-2 gap-1">
            {stageLabels.map((label) => {
              const code = label.split(' ')[0];
              const active = lifecycle.lifecycle_state === code;
              return (
                <div
                  key={label}
                  className={clsx(
                    'rounded border px-1 py-1 text-center',
                    active ? 'border-brand-blue/60 bg-brand-blue/15' : 'border-border-secondary bg-bg-primary/40',
                  )}
                >
                  <div className={active ? 'font-mono text-[11px] font-black text-brand-blue-light' : 'font-mono text-[11px] font-bold text-text-tertiary'}>
                    {code}
                  </div>
                  <div className="text-[8px] text-text-tertiary">{label.split(' ')[1]}</div>
                </div>
              );
            })}
          </div>
          <div className="mt-2 space-y-1">
            <ProgressLine label="结构" value={clamp(toNumber(lifecycle.structure_score))} tone="blue" right={num(lifecycle.structure_score, 0)} />
          </div>
            </div>
          </div>
          </details>
      </div>

        <div data-stock-sidebar data-sidebar-collapsed={infoPanelHidden} className={clsx('stock-sidebar-panel min-h-0 min-w-0 flex-col overflow-hidden', analysisOpen && compactView === 'chart' ? 'hidden lg:flex' : 'flex')}>
          {infoPanelHidden && <button type="button" aria-label="展开右侧栏" aria-expanded={false}
            onClick={() => setInfoPanelHidden(false)} className="flex min-h-0 flex-1 flex-col items-center justify-start gap-2 py-3 text-text-secondary hover:text-cyan-300" title="展开右侧栏">
            <PanelRightOpen size={16} /><span className="text-[11px] [writing-mode:vertical-rl]">展开侧栏</span>
          </button>}
          <div className={infoPanelHidden ? 'hidden' : 'contents'}>
          <div className="stock-sidebar-tabs grid shrink-0 grid-cols-[repeat(3,minmax(0,1fr))_28px] gap-1 border-b border-border-primary p-1.5 text-xs" role="tablist" aria-label="个股资料与提示">
            <button type="button" role="tab" aria-selected={!analysisOpen} onClick={() => { setSidebarTab('info'); setReportOpen(false); setInfoPanelHidden(false); }} className={!analysisOpen ? 'font-bold text-brand-blue-light' : 'text-text-secondary'}>个股资料</button>
            <button type="button" role="tab" aria-selected={assistantOpen} onClick={() => setReportOpen(true)} className={assistantOpen ? 'font-bold text-brand-blue-light' : 'text-text-secondary'}>AI 助手</button>
            {dailyHintsPanel && <button type="button" role="tab" aria-selected={hintsOpen} onClick={() => { setSidebarTab('hints'); setReportOpen(false); }} className={hintsOpen ? 'font-bold text-brand-blue-light' : 'text-text-secondary'}>当日提示</button>}
            <button type="button" aria-label="收起右侧栏" aria-expanded={true} onClick={() => setInfoPanelHidden(true)} title="收起右侧栏"><PanelRightClose size={14} className="mx-auto" /></button>
          </div>
          {assistantOpen && <div className="min-h-0 flex-1"><MrgcDailyReportPanel date={selectedDate} currentSymbol={symbol} onSelectSymbol={onSelectSymbol} allowGroupFilter={Boolean(sidebarScopeKey && sidebarScopeKey !== '__single__')} unifiedHints={unifiedHints} expectUnifiedHints={Boolean(dailyHintsPanel)} onReturnHints={dailyHintsPanel ? () => setSidebarUi({ tab: 'hints', infoHidden: false }) : undefined} /></div>}
          {!analysisOpen && <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <StockEnrichmentPanel symbol={symbol} selectedDate={selectedDate} hidden={false} compact={showDetailLink} className="min-h-0 flex-1" />
          </div>}
          {dailyHintsPanel && <div ref={hintsScroll.ref} onScroll={hintsScroll.onScroll} data-sidebar-scroll hidden={!hintsOpen} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain custom-scrollbar">{dailyHintsPanel}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

const watchQueueConfig = [
  { type: 'IN_ZONE', label: '进入回调区', empty: '暂无新进入回调区的股票', tone: 'border-warning/35 bg-warning/5', badge: 'border-warning/45 bg-warning/10 text-warning' },
  { type: 'TRIGGERED', label: '确认触发', empty: '暂无确认触发', tone: 'border-success/35 bg-success/5', badge: 'border-success/45 bg-success/10 text-success' },
  { type: 'INVALIDATED', label: '已经失效', empty: '暂无失效事件', tone: 'border-danger/35 bg-danger/5', badge: 'border-danger/45 bg-danger/10 text-danger' },
  { type: 'DATA_BLOCKED', label: '数据异常', empty: '提醒链数据正常', tone: 'border-brand-blue/35 bg-brand-blue/5', badge: 'border-brand-blue/45 bg-brand-blue/10 text-brand-blue-light' },
] as const;

function normalizedWatchEventType(event: WatchEvent) {
  const value = text(event.event_type || event.to_state, '').toUpperCase();
  if (value === 'ENTER_ZONE' || value === 'ENTERED_ZONE') return 'IN_ZONE';
  if (value === 'TRIGGER') return 'TRIGGERED';
  if (value === 'INVALIDATE') return 'INVALIDATED';
  return value;
}

function TodayActionBox({
  tradeDate,
  poolName,
  summary,
  compact = false,
  scopeKey,
  snapshots,
  events,
  loading,
  failed,
  updatingEventId,
  onAcknowledge,
  onSnooze,
  onSelectSymbol,
}: {
  tradeDate?: string;
  poolName?: string;
  compact?: boolean;
  scopeKey?: string;
  summary: Row;
  snapshots: WatchSnapshot[];
  events: WatchEvent[];
  loading: boolean;
  failed: boolean;
  updatingEventId?: string | number;
  onAcknowledge: (eventId: string | number) => void;
  onSnooze: (eventId: string | number) => void;
  onSelectSymbol: (symbol: string) => void;
}) {
  const expandedKey = `actions:${scopeKey || poolName}:${tradeDate}`;
  const expanded = useGlobalStore((state) => Boolean(state.stockSidebarUi.expanded[expandedKey]));
  const setSidebarExpanded = useGlobalStore((state) => state.setSidebarExpanded);
  const setExpanded = (update: (previous: boolean) => boolean) => setSidebarExpanded(expandedKey, update(expanded));
  const snapshotsBySymbol = new Map(
    snapshots.filter((item) => item.symbol).map((item) => [text(item.symbol), item]),
  );
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const activeEvents = events.filter((event) => {
    const status = text(event.status, 'pending').toLowerCase();
    if (status === 'acknowledged' || status === 'resolved') return false;
    if (status === 'snoozed' && text(event.snoozed_until, '') > today) return false;
    return watchQueueConfig.some((queue) => queue.type === normalizedWatchEventType(event));
  });
  const dataCounts = asRow(summary.data_status_counts);
  const blockedDataCount = Object.entries(dataCounts).reduce((total, [key, value]) => (
    ['OK', 'READY', 'TRUSTED', 'VALID'].includes(key.toUpperCase()) ? total : total + toNumber(value)
  ), 0);
  const trusted = typeof summary.data_trusted === 'boolean'
    ? summary.data_trusted
    : blockedDataCount === 0 && !activeEvents.some((event) => normalizedWatchEventType(event) === 'DATA_BLOCKED');
  const unresolved = Math.max(
    finiteNumber(summary.unresolved_events)
      ?? finiteNumber(summary.unprocessed_count)
      ?? 0,
    activeEvents.length,
  );

  return (
    <section className="shrink-0 border-b border-border-primary bg-bg-primary/45 px-3 py-2" aria-label={compact ? '提醒处理' : '今日行动箱'}>
      <div className="flex flex-wrap items-center gap-2">
        <Bell size={13} className="text-brand-blue-light" />
        <h2 className="text-[11px] font-black text-white">{compact ? '提醒处理' : '今日行动箱'}</h2>
        <span className="rounded border border-border-primary bg-bg-primary px-1.5 py-0.5 font-mono text-[9px] text-text-tertiary">
          {text(summary.trade_date || tradeDate, '最新')}
        </span>
        <span className="truncate text-[9px] text-text-tertiary">{poolName || '当前分组'}</span>
        <div className="flex flex-wrap gap-1">
          {watchQueueConfig.map((queue) => (
            <button key={queue.type} type="button" onClick={() => setExpanded((previous) => !previous)}
              aria-expanded={expanded} aria-controls="watch-action-details"
              className={clsx('rounded border px-2 py-0.5 text-[10px] font-bold', queue.badge)}>
              {queue.label} <span className="font-mono">{loading ? '…' : failed ? '—' : activeEvents.filter((event) => normalizedWatchEventType(event) === queue.type).length}</span>
            </button>
          ))}
        </div>
        <span className={clsx(
          'ml-auto inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9px] font-bold',
          trusted ? 'border-success/40 bg-success/10 text-success' : 'border-danger/40 bg-danger/10 text-danger',
        )}>
          {trusted ? <ShieldCheck size={10} /> : <AlertTriangle size={10} />}
          {failed ? '读取失败' : loading ? '读取中' : trusted ? '数据可信' : '数据受阻'}
        </span>
        <span className="font-mono text-[9px] text-text-tertiary">待处理 {int(unresolved)}</span>
        <button type="button" onClick={() => setExpanded((previous) => !previous)}
          aria-expanded={expanded} aria-controls="watch-action-details"
          className="text-[10px] font-bold text-brand-blue-light">{expanded ? '收起 ▴' : compact ? '展开处理 ▾' : '展开行动箱 ▾'}</button>
      </div>

      <div id="watch-action-details" hidden={!expanded} className={clsx("mt-2", !compact && "max-h-[220px] overflow-y-auto custom-scrollbar")}>
      {failed ? (
        <div className="flex h-20 items-center justify-center rounded-md border border-danger/35 bg-danger/5 text-[10px] text-danger">
          行动箱暂不可用，请确认 Watch Engine 已启动后刷新。
        </div>
      ) : loading ? (
        <div className="flex h-20 items-center justify-center rounded-md border border-border-primary bg-bg-primary/30 text-[10px] text-text-tertiary">
          <RefreshCw size={12} className="mr-1.5 animate-spin" /> 正在读取长期跟踪状态
        </div>
      ) : (
        <div className={clsx("grid grid-cols-1 gap-2", !compact && "md:grid-cols-2 lg:grid-cols-4")}>
          {watchQueueConfig.map((queue) => {
            const queueEvents = activeEvents.filter((event) => normalizedWatchEventType(event) === queue.type);
            return (
              <div key={queue.type} className={clsx('min-w-0 rounded-md border p-1.5', queue.tone)}>
                <div className="mb-1 flex items-center gap-1.5 px-0.5">
                  <span className={clsx('rounded border px-1.5 py-0.5 text-[9px] font-black', queue.badge)}>{queue.label}</span>
                  <span className="ml-auto font-mono text-[10px] font-black text-text-secondary">{queueEvents.length}</span>
                </div>
                <div className={clsx("space-y-1", !compact && "max-h-[clamp(76px,12vh,118px)] overflow-y-auto custom-scrollbar")}>
                  {queueEvents.length === 0 ? (
                    <div className="flex h-[68px] items-center justify-center rounded border border-dashed border-border-secondary/60 px-2 text-center text-[9px] text-text-tertiary">
                      {queue.empty}
                    </div>
                  ) : queueEvents.map((event) => {
                    const snapshot = snapshotsBySymbol.get(text(event.symbol, '')) || {};
                    const item = { ...snapshot, ...asRow(event.payload) } as WatchSnapshot;
                    const eventId = event.event_id;
                    const busy = eventId !== undefined && String(updatingEventId) === String(eventId);
                    const symbol = text(event.symbol || item.symbol, '');
                    const reason = text(item.reason || item.why_now || event.message || event.title, '等待人工确认');
                    const pathCode = setupPathCode(item.setup_path || asRow(item.factors).setup_path);
                    const isHistorical = text(event.status, '').toUpperCase() === 'HISTORICAL';
                    const isCooling = queue.type === 'TRIGGERED' && isHistorical && item.is_transition === false;
                    return (
                      <div
                        key={String(eventId ?? `${queue.type}-${symbol}-${event.created_at}`)}
                        role={symbol ? 'button' : undefined}
                        tabIndex={symbol ? 0 : undefined}
                        onClick={() => symbol && onSelectSymbol(symbol)}
                        onKeyDown={(keyboardEvent) => {
                          if (symbol && (keyboardEvent.key === 'Enter' || keyboardEvent.key === ' ')) onSelectSymbol(symbol);
                        }}
                        className={clsx('rounded border border-border-secondary/70 bg-bg-primary/70 p-1.5', symbol && 'cursor-pointer hover:border-brand-blue/55')}
                      >
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-[10px] font-black text-white">{text(item.name || event.title, symbol || queue.label)}</span>
                          {symbol && <span className="font-mono text-[9px] text-brand-blue-light">{symbol}</span>}
                          {isHistorical && (
                            <span className="whitespace-nowrap rounded border border-border-primary bg-bg-tertiary px-1 py-0.5 text-[8px] font-bold text-text-tertiary">
                              {isCooling ? '冷却中' : '历史遍历'}
                            </span>
                          )}
                          {pathCode !== '-' && (
                            <span className={clsx(
                              'whitespace-nowrap rounded border px-1 py-0.5 text-[8px] font-black',
                              pathCode === 'A'
                                ? 'border-brand-blue/45 bg-brand-blue/10 text-brand-blue-light'
                                : 'border-warning/45 bg-warning/10 text-warning',
                            )}>
                              路径 {pathCode}
                            </span>
                          )}
                          {!isBlankValue(item.tracking_days) && <span className="ml-auto whitespace-nowrap text-[8px] text-text-tertiary">跟踪 {int(item.tracking_days)} 天</span>}
                        </div>
                        {queue.type !== 'DATA_BLOCKED' && (
                          <div className="mt-1 grid grid-cols-2 gap-x-2 gap-y-0.5 font-mono text-[8px] text-text-tertiary">
                            <span>现价 <b className="text-text-primary">{positivePrice(item.current_price)}</b></span>
                            <span>回调区 <b className="text-text-primary">{positivePrice(item.zone_low)}–{positivePrice(item.zone_high)}</b></span>
                            <span>触发 <b className="text-success">{positivePrice(item.trigger_price)}</b></span>
                            <span>失效 <b className="text-danger">{positivePrice(item.invalidation_price)}</b></span>
                            <span className="col-span-2">距触发 <b className="text-warning">{compactSignedPercent(item.distance_to_trigger_pct)}</b></span>
                          </div>
                        )}
                        <div className="mt-1 line-clamp-2 text-[9px] leading-3.5 text-text-secondary" title={reason}>{reason}</div>
                        {eventId !== undefined && !isHistorical && (
                          <div className="mt-1 flex justify-end gap-1 border-t border-border-secondary/50 pt-1">
                            <button
                              type="button"
                              onClick={(clickEvent) => { clickEvent.stopPropagation(); onSnooze(eventId); }}
                              disabled={busy}
                              className="inline-flex items-center gap-1 rounded border border-border-primary px-1.5 py-0.5 text-[8px] font-bold text-text-tertiary hover:border-warning/50 hover:text-warning disabled:opacity-40"
                            >
                              <Clock3 size={9} /> 延后
                            </button>
                            <button
                              type="button"
                              onClick={(clickEvent) => { clickEvent.stopPropagation(); onAcknowledge(eventId); }}
                              disabled={busy}
                              className="inline-flex items-center gap-1 rounded border border-success/40 bg-success/10 px-1.5 py-0.5 text-[8px] font-bold text-success disabled:opacity-40"
                            >
                              <Check size={9} /> 确认
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
      </div>
    </section>
  );
}

function PoolSidebar({
  pools,
  selectedPoolId,
  selectedSymbol,
  members,
  keyword,
  searchRows,
  newPoolName,
  editingPoolId,
  editingPoolName,
  isBusy,
  monitoringPoolId,
  sortKey,
  sortDir,
  onKeywordChange,
  onNewPoolNameChange,
  onCreatePool,
  onSelectPool,
  onStartEditPool,
  onEditingPoolNameChange,
  onSavePool,
  onCancelEditPool,
  onDeletePool,
  onToggleMonitor,
  onAddStock,
  onSelectStock,
  onRemoveStock,
  onSort,
}: {
  pools: StockPool[];
  selectedPoolId: string;
  selectedSymbol: string;
  members: PoolMember[];
  keyword: string;
  searchRows: Row[];
  newPoolName: string;
  editingPoolId: string;
  editingPoolName: string;
  isBusy: boolean;
  monitoringPoolId?: string;
  sortKey: SortKey;
  sortDir: 'asc' | 'desc';
  onKeywordChange: (value: string) => void;
  onNewPoolNameChange: (value: string) => void;
  onCreatePool: () => void;
  onSelectPool: (poolId: string) => void;
  onStartEditPool: (pool: StockPool) => void;
  onEditingPoolNameChange: (value: string) => void;
  onSavePool: () => void;
  onCancelEditPool: () => void;
  onDeletePool: (poolId: string) => void;
  onToggleMonitor: (pool: StockPool) => void;
  onAddStock: (symbol: string) => void;
  onSelectStock: (symbol: string) => void;
  onRemoveStock: (symbol: string) => void;
  onSort: (key: SortKey) => void;
}) {
  const selectedPool = pools.find((pool) => pool.pool_id === selectedPoolId);

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border-secondary px-2 py-2">
        <Star size={13} className="shrink-0 text-brand-blue-light" />
        <select value={selectedPoolId} onChange={(event) => onSelectPool(event.target.value)}
          aria-label="选择自选分组"
          className="h-7 min-w-0 flex-1 rounded border border-border-primary bg-bg-primary px-1 text-[11px] font-bold text-text-primary">
          {pools.map((pool) => <option key={pool.pool_id} value={pool.pool_id}>{text(pool.name)} · {int(pool.stock_count)} 只</option>)}
        </select>
      </div>
      {selectedPool?.is_smart && (
        <div className="shrink-0 border-b border-border-secondary px-2 py-1.5 text-[10px] leading-4 text-text-secondary">
          <div className="font-bold text-brand-blue-light">智能分组 · C 类盘后邮件监控</div>
          <div>{selectedPool.smart_state?.status === 'READY' ? '历史窗口已校验' : '所选日期票池待校验'} · {selectedPool.smart_state?.target_trade_date || '等待首次更新'}</div>
          <div>{selectedPool.smart_state?.window_start} ～ {selectedPool.smart_state?.window_end} · 20 个交易日</div>
          {selectedPool.smart_state?.reason && <div className="text-warning">{({mrgc_dates_missing: '窗口内 MRGC 尚未完成', source_dates_missing: '窗口内行情缺失', source_window_incomplete: '交易日窗口不足', c_monitor_capacity_exceeded: '监控总数超过 300 只上限'} as Record<string, string>)[selectedPool.smart_state.reason] || '数据待校验'}</div>}
        </div>
      )}
      <details className="shrink-0 border-b border-border-secondary">
        <summary className="cursor-pointer px-2 py-1.5 text-[10px] font-bold text-text-tertiary">管理分组</summary>
      {/* 头部：标题 + 新建池 */}
      <div className="shrink-0 border-b border-border-secondary p-2">
        <div className="mb-2 flex items-center gap-1.5">
          <Star size={13} className="text-brand-blue-light" />
          <span className="text-[11px] font-black text-white">自选分组</span>
          <span className="ml-auto font-mono text-[10px] text-text-tertiary">{members.length} 只</span>
        </div>
        <div className="grid grid-cols-[1fr_24px] gap-1">
          <input
            value={newPoolName}
            onChange={(event) => onNewPoolNameChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') onCreatePool();
            }}
            placeholder="新建分组"
            className="h-7 min-w-0 rounded border border-border-primary bg-bg-primary px-2 text-[11px] text-text-primary outline-none focus:border-brand-blue/70"
          />
          <button
            type="button"
            onClick={onCreatePool}
            disabled={!newPoolName.trim() || isBusy}
            className="h-7 rounded border border-border-primary bg-bg-primary text-text-secondary transition-colors hover:border-brand-blue/60 hover:text-brand-blue-light disabled:cursor-not-allowed disabled:opacity-40"
            title="创建分组"
          >
            <FolderPlus size={12} className="mx-auto" />
          </button>
        </div>
      </div>

      {/* 分组列表 */}
      <div className="max-h-[clamp(100px,18vh,180px)] shrink-0 space-y-0.5 overflow-y-auto border-b border-border-secondary p-2 custom-scrollbar">
        {pools.map((pool) => {
          const active = pool.pool_id === selectedPoolId;
          const editing = pool.pool_id === editingPoolId;
          return (
            <div
              key={pool.pool_id}
              className={clsx(
                'rounded border px-1.5 py-1',
                active ? 'border-brand-blue/60 bg-brand-blue/10' : 'border-border-primary bg-bg-primary/40',
              )}
            >
              {editing ? (
                <div className="grid grid-cols-[1fr_20px_20px] gap-1">
                  <input
                    value={editingPoolName}
                    onChange={(event) => onEditingPoolNameChange(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') onSavePool();
                      if (event.key === 'Escape') onCancelEditPool();
                    }}
                    className="h-6 min-w-0 rounded border border-border-primary bg-bg-primary px-1 text-[10px] text-text-primary outline-none"
                  />
                  <button type="button" onClick={onSavePool} className="rounded border border-success/40 text-success" title="保存">
                    <Check size={10} className="mx-auto" />
                  </button>
                  <button type="button" onClick={onCancelEditPool} className="rounded border border-border-primary text-text-secondary" title="取消">
                    <X size={10} className="mx-auto" />
                  </button>
                </div>
              ) : (
                <div className="flex min-w-0 items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => onSelectPool(pool.pool_id)}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                    title={pool.name}
                  >
                    <Folder size={11} className={active ? 'shrink-0 text-brand-blue-light' : 'shrink-0 text-text-tertiary'} />
                    <span className={active ? 'truncate text-[11px] font-black text-white' : 'truncate text-[11px] font-bold text-text-secondary'}>
                      {text(pool.name)}
                    </span>
                    <span className="ml-auto font-mono text-[10px] text-text-tertiary">{int(pool.stock_count)}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => onToggleMonitor(pool)}
                    disabled={pool.is_smart || pool.is_report || monitoringPoolId === pool.pool_id}
                    className={clsx(
                      'shrink-0 rounded p-0.5 transition-colors disabled:opacity-40',
                      isPoolMonitored(pool)
                        ? 'bg-success/10 text-success hover:bg-success/20'
                        : 'text-text-tertiary hover:bg-bg-tertiary hover:text-warning',
                    )}
                    title={pool.is_report ? '日报联动只读列表' : pool.is_smart ? '自动监控 C 类信号，盘后邮件提醒' : isPoolMonitored(pool) ? '已持续跟踪，点击关闭' : '开启持续跟踪'}
                    aria-label={`${text(pool.name)}${isPoolMonitored(pool) ? '关闭' : '开启'}持续跟踪`}
                  >
                    {isPoolMonitored(pool) ? <Bell size={10} /> : <BellOff size={10} />}
                  </button>
                  {!isDefaultPool(pool) && !pool.is_smart && !pool.is_report && (
                    <div className="flex shrink-0 items-center">
                      <button type="button" onClick={() => onStartEditPool(pool)} className="text-text-tertiary hover:text-brand-blue-light" title="重命名">
                        <Pencil size={10} />
                      </button>
                      <button type="button" onClick={() => onDeletePool(pool.pool_id)} className="ml-1 text-text-tertiary hover:text-danger" title="删除">
                        <Trash2 size={10} />
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      </details>

      {/* 搜索框 */}
      <div className="shrink-0 border-b border-border-secondary p-2">
        <div className="flex items-center gap-1.5">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-border-primary bg-bg-primary text-text-tertiary">
            <Search size={12} />
          </span>
          <input
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            disabled={(selectedPool?.is_smart || selectedPool?.is_report)}
            placeholder={selectedPool?.is_report ? '日报联动列表只读' : selectedPool?.is_smart ? '智能分组自动维护' : '代码 / 拼音 / 名称'}
            className="h-7 min-w-0 flex-1 rounded border border-border-primary bg-bg-primary px-2 text-[11px] text-text-primary outline-none focus:border-brand-blue/70"
          />
        </div>
        <div className="mt-1 text-[9px] text-text-tertiary">
          {selectedPool?.is_smart || selectedPool?.is_report ? '不支持手工添加个股' : `添加到 ${text(selectedPool?.name, '当前池')}`}
        </div>
        {!selectedPool?.is_smart && !selectedPool?.is_report && keyword.trim() && (
          <div className="mt-1 max-h-32 overflow-y-auto rounded border border-border-primary bg-bg-primary custom-scrollbar">
            {searchRows.length === 0 ? (
              <div className="px-2 py-2 text-center text-[10px] text-text-tertiary">暂无匹配</div>
            ) : searchRows.map((row) => (
              <button
                key={text(row.symbol)}
                type="button"
                onClick={() => onAddStock(text(row.symbol, ''))}
                className="flex w-full items-center gap-1.5 border-b border-border-secondary px-2 py-1 text-left last:border-b-0 hover:bg-brand-blue/10"
              >
                <ListPlus size={11} className="shrink-0 text-brand-blue-light" />
                <span className="min-w-[44px] font-mono text-[10px] text-text-primary">{text(row.symbol)}</span>
                <span className="truncate text-[10px] text-text-secondary">{text(row.name)}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 池内股票列表 - 固定代码/名称/行业，其余指标横向滚动 */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="mb-1 flex items-center px-1 text-[10px] font-black text-text-tertiary">
          <span>{text(selectedPool?.name, '未选择')} · {members.length} 只</span>
          <span className="ml-auto font-mono text-[9px] font-normal">↑↓ 选择</span>
        </div>
        {members.length === 0 ? (
          <div className="rounded border border-dashed border-border-primary p-3 text-center text-[10px] leading-4 text-text-tertiary">
            {selectedPool?.is_report ? '当前日报筛选没有匹配股票' : selectedPool?.is_smart ? '等待盘后自动收集 MRGC 股票' : '暂无股票，先搜索添加'}
          </div>
        ) : (
          <StockTable
            members={members}
            selectedSymbol={selectedSymbol}
            sortKey={sortKey}
            sortDir={sortDir}
            onSort={onSort}
            onSelectStock={onSelectStock}
            onRemoveStock={onRemoveStock}
            readOnly={(selectedPool?.is_smart || selectedPool?.is_report)}
          />
        )}
      </div>
    </div>
  );
}

function StockTable({
  readOnly = false,
  members,
  selectedSymbol,
  sortKey,
  sortDir,
  onSort,
  onSelectStock,
  onRemoveStock,
}: {
  readOnly?: boolean;
  members: PoolMember[];
  selectedSymbol: string;
  sortKey: SortKey;
  sortDir: 'asc' | 'desc';
  onSort: (key: SortKey) => void;
  onSelectStock: (symbol: string) => void;
  onRemoveStock: (symbol: string) => void;
}) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const sorted = useMemo(() => [...members].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    if (typeof av === 'number' && typeof bv === 'number') {
      return sortDir === 'asc' ? av - bv : bv - av;
    }
    const as = String(av ?? '');
    const bs = String(bv ?? '');
    return sortDir === 'asc' ? as.localeCompare(bs) : bs.localeCompare(as);
  }), [members, sortDir, sortKey]);
  const selectedIndex = sorted.findIndex((stock) => text(stock.symbol, '') === selectedSymbol);

  const selectStock = (stock: PoolMember) => {
    const symbol = text(stock.symbol, '');
    if (!symbol) return;
    onSelectStock(symbol);
    requestAnimationFrame(() => {
      listRef.current
        ?.querySelector<HTMLElement>(`[data-symbol="${symbol}"]`)
        ?.scrollIntoView({ block: 'nearest' });
    });
  };

  const handleListKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const direction = event.key === 'ArrowDown' ? 1 : -1;
    const nextIndex = nextStockIndex(selectedIndex, sorted.length, direction);
    if (nextIndex < 0) return;
    event.preventDefault();
    selectStock(sorted[nextIndex]);
  };

  const arrow = (key: SortKey) => {
    if (sortKey !== key) return '';
    return sortDir === 'asc' ? ' ↑' : ' ↓';
  };

  const rpsCell = (value: unknown) => {
    const missing = value === null || value === undefined || value === '';
    const numeric = missing ? Number.NaN : toNumber(value, Number.NaN);
    const cls = Number.isFinite(numeric) && numeric >= 90
      ? 'text-success font-mono font-black'
      : Number.isFinite(numeric) && numeric >= 80
        ? 'text-warning font-mono'
        : 'font-mono text-text-secondary';
    return <td className={`${cls} px-1.5 py-[3px] text-right leading-5 whitespace-nowrap`}>{compactRps(value)}</td>;
  };

  const stickyHeader = "sticky top-0 z-20 border-b border-border-secondary bg-[#07111f] px-1.5 py-1 text-[9px] font-black text-text-tertiary whitespace-nowrap";
  const stickyCell = "sticky z-10 border-b border-border-secondary/70 bg-[#07111f] px-1.5 py-[3px] leading-5 whitespace-nowrap group-hover:bg-[#0a1b2b]";
  const thRightCls = "sticky top-0 z-10 border-b border-border-secondary bg-[#07111f] px-1.5 py-1 text-[9px] font-black text-text-tertiary whitespace-nowrap cursor-pointer hover:text-brand-blue-light text-right";

  return (
    <div
      ref={listRef}
      tabIndex={0}
      onKeyDown={handleListKeyDown}
      onClick={() => listRef.current?.focus({ preventScroll: true })}
      aria-label="自选股列表，使用上下方向键选择股票"
      className="min-h-0 flex-1 overflow-auto rounded border border-border-primary outline-none custom-scrollbar focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-brand-blue-light/70"
    >
      <table className="w-[564px] table-fixed border-separate border-spacing-0 text-[11px]">
        <colgroup>
          <col className="w-[62px]" />
          <col className="w-[84px]" />
          <col className="hidden" />
          <col className="w-[58px]" />
          <col className="w-[60px]" />
          <col className="w-[44px]" />
          <col className="w-[44px]" />
          <col className="w-[44px]" />
          <col className="w-[44px]" />
          <col className="w-[50px]" />
          <col className="w-[50px]" />
          <col className="w-[24px]" />
        </colgroup>
        <thead className="sticky top-0 z-20">
          <tr>
            <th className={`${stickyHeader} left-0 w-[62px] min-w-[62px] text-left`}>代码</th>
            <th className={`${stickyHeader} left-[62px] w-[84px] min-w-[84px] text-left`}>
              <button type="button" onClick={() => onSort('name')} className="text-left">
                名称{arrow('name')}
              </button>
            </th>
            <th className={`hidden ${stickyHeader} left-[146px] w-[104px] min-w-[104px] border-r border-border-secondary text-left shadow-[8px_0_14px_rgba(0,5,14,0.65)]`}>
              行业
            </th>
            <th className={thRightCls}>
              <button type="button" onClick={() => onSort('close')}>现价{arrow('close')}</button>
            </th>
            <th className={thRightCls}>
              <button type="button" onClick={() => onSort('pct_chg')}>涨跌{arrow('pct_chg')}</button>
            </th>
            <th className={thRightCls}>
              <button type="button" onClick={() => onSort('rps_5')}>R5{arrow('rps_5')}</button>
            </th>
            <th className={thRightCls}>
              <button type="button" onClick={() => onSort('rps_10')}>R10{arrow('rps_10')}</button>
            </th>
            <th className={thRightCls}>
              <button type="button" onClick={() => onSort('rps_20')}>R20{arrow('rps_20')}</button>
            </th>
            <th className={thRightCls}>
              <button type="button" onClick={() => onSort('rps_50')}>R50{arrow('rps_50')}</button>
            </th>
            <th className={thRightCls}>
              <button type="button" onClick={() => onSort('rps_120')}>R120{arrow('rps_120')}</button>
            </th>
            <th className={thRightCls}>
              <button type="button" onClick={() => onSort('rps_250')}>R250{arrow('rps_250')}</button>
            </th>
            <th className="sticky top-0 z-10 bg-bg-secondary px-1 py-1 text-[9px] text-text-tertiary" style={{ minWidth: 24 }}></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((stock) => {
            const active = stock.symbol === selectedSymbol;
            const up = toNumber(stock.pct_chg) >= 0;
            return (
              <tr
                key={text(stock.symbol)}
                data-symbol={text(stock.symbol)}
                className={active ? 'group bg-brand-blue/15' : 'group hover:bg-brand-blue/5'}
              >
                <td className={`${stickyCell} left-0 w-[62px] min-w-[62px] font-mono font-black text-brand-blue-light ${active ? 'bg-[#102844]' : ''}`}>
                  <button type="button" onClick={() => selectStock(stock)} className="text-left">
                    {text(stock.symbol)}
                  </button>
                </td>
                <td className={`${stickyCell} left-[62px] w-[84px] min-w-[84px] text-text-secondary ${active ? 'bg-[#102844]' : ''}`}>
                  <button type="button" onClick={() => selectStock(stock)} title={`${text(stock.name)} · ${memberIndustry(stock)}${stock.note ? ` · ${text(stock.note)}` : ''}`} className="block w-full truncate text-left">
                    {text(stock.name)}
                  </button>
                </td>
                <td className={`hidden ${stickyCell} left-[146px] w-[104px] min-w-[104px] border-r border-border-secondary text-text-secondary shadow-[8px_0_14px_rgba(0,5,14,0.65)] ${active ? 'bg-[#102844]' : ''}`}>
                  <span className="block truncate">{memberIndustry(stock)}</span>
                </td>
                <td className="px-1.5 py-[3px] text-right font-mono leading-5 text-text-primary whitespace-nowrap">{num(stock.close, 2)}</td>
                <td className={'px-1.5 py-[3px] text-right font-mono leading-5 whitespace-nowrap ' + (up ? 'text-danger' : 'text-success')}>{compactPercent(stock.pct_chg)}</td>
                {rpsCell(stock.rps_5)}
                {rpsCell(stock.rps_10)}
                {rpsCell(stock.rps_20)}
                {rpsCell(stock.rps_50)}
                {rpsCell(stock.rps_120)}
                {rpsCell(stock.rps_250)}
                <td className="px-0.5 py-[3px] text-center leading-5">
                  <button type="button" disabled={readOnly} onClick={() => onRemoveStock(text(stock.symbol, ''))} className="text-text-tertiary hover:text-danger" title="移出">
                    <Trash2 size={10} />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function StockWorkspace({ initialSymbol }: { initialSymbol?: string }) {
  const [unifiedHints, setUnifiedHints] = useState<DailyHintsContext>();
  const queryClient = useQueryClient();
  const { globalDate, setGlobalDate, setSelectedStock, reportContext, setReportContext, reportFocus, setReportFocus, reportOpen } = useGlobalStore();
  const routeSymbol = normalizeSymbol(initialSymbol);
  const [selectedPoolId, setSelectedPoolId] = useState('');
  const [reportLinkError, setReportLinkError] = useState('');
  const [selectedSymbol, setSelectedSymbolState] = useState(routeSymbol);
  const [newPoolName, setNewPoolName] = useState('');
  const [editingPoolId, setEditingPoolId] = useState('');
  const [editingPoolName, setEditingPoolName] = useState('');
  const [keyword, setKeyword] = useState('');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => readLocalValue(LOCAL_SIDEBAR_KEY) === '1');
  const [sortKey, setSortKey] = useState<SortKey>('rps_250');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  useEffect(() => {
    const link = parseReportLink(window.location.search);
    if (!link) return;
    let active = true;
    queryClient.fetchQuery({ queryKey: ['mrgcDailyReport', link.date], queryFn: () => loadReport(link.date) })
      .then(({ report, job }) => {
        if (!active) return;
        if (!report) {
          setReportLinkError(job.reason || '该日期尚无日报存档，请打开 AI 助手生成策略日报。');
          return;
        }
        if (!report.stocks.some((stock) => stock.symbol === link.symbol)) {
          setReportLinkError('日报中没有该股票，请核对链接日期。');
          return;
        }
        setGlobalDate(report.date);
        setReportContext({ date: report.date, stocks: reportLinkedStocks(report.stocks), label: '邮件 / 日报详情' });
        setReportFocus({ date: report.date, symbol: link.symbol });
      }).catch(() => { if (active) setReportLinkError('日报详情链接读取失败，请从 AI 助手重试。'); });
    return () => { active = false; };
  }, [queryClient, setGlobalDate, setReportContext, setReportFocus]);

  const handleSort = useCallback((key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }, [sortKey]);

  const datesQuery = useQuery({ queryKey: ['quantDates'], queryFn: () => quantApi.getDates(160) });
  const dates = rows<string>(datesQuery.data?.dates);
  const selectedDate = globalDate || datesQuery.data?.latest || '';

  const poolsQuery = useQuery({
    queryKey: ['stockPools', selectedDate],
    queryFn: () => stockApi.getPools(selectedDate),
    enabled: Boolean(selectedDate),
    staleTime: 30_000,
  });
  const realPools = rows<StockPool>(poolsQuery.data?.pools);
  const linkedPool = reportContextPool(reportContext, selectedDate);
  const pools: StockPool[] = linkedPool ? [...realPools, linkedPool] : realPools;
  const defaultPoolId = text(poolsQuery.data?.default_pool_id, DEFAULT_POOL_ID);

  const poolStocksQuery = useQuery({
    queryKey: ['stockPoolStocks', selectedPoolId, selectedDate],
    queryFn: () => stockApi.getPoolStocks(selectedPoolId, selectedDate),
    enabled: Boolean(selectedPoolId) && selectedPoolId !== REPORT_LINKED_POOL_ID,
    staleTime: 30_000,
  });
  const reportLinked = selectedPoolId === REPORT_LINKED_POOL_ID && reportContext?.date === selectedDate;
  const members = reportLinked && reportContext
    ? reportContext.stocks.map((stock) => ({ ...stock }))
    : rows<PoolMember>(poolStocksQuery.data?.stocks);

  const watchSummaryQuery = useQuery({
    queryKey: ['watchSummary', selectedDate, selectedPoolId],
    queryFn: () => watchApi.getSummary({ tradeDate: selectedDate, poolId: selectedPoolId }),
    enabled: Boolean(selectedPoolId) && selectedPoolId !== REPORT_LINKED_POOL_ID,
    staleTime: 30_000,
  });
  const watchSnapshotsQuery = useQuery({
    queryKey: ['watchSnapshots', selectedDate, selectedPoolId],
    queryFn: () => watchApi.getSnapshots({ tradeDate: selectedDate, poolId: selectedPoolId }),
    enabled: Boolean(selectedPoolId) && selectedPoolId !== REPORT_LINKED_POOL_ID,
    staleTime: 30_000,
  });
  const watchEventsQuery = useQuery({
    queryKey: ['watchEvents', selectedDate, selectedPoolId],
    queryFn: () => watchApi.getEvents({ tradeDate: selectedDate, poolId: selectedPoolId }),
    enabled: Boolean(selectedPoolId) && selectedPoolId !== REPORT_LINKED_POOL_ID,
    staleTime: 30_000,
  });
  const searchKeyword = keyword.trim();
  const searchQuery = useQuery({
    queryKey: ['stockSearch', searchKeyword],
    queryFn: () => stockApi.searchStock(searchKeyword),
    enabled: searchKeyword.length > 0,
  });
  const searchRows = rows<Row>(searchQuery.data?.stocks);

  const selectedPool = useMemo(
    () => pools.find((pool) => pool.pool_id === selectedPoolId),
    [pools, selectedPoolId],
  );
  const selectedMember = useMemo(
    () => members.find((stock) => stock.symbol === selectedSymbol),
    [members, selectedSymbol],
  );

  const selectSymbol = useCallback((symbol: string, _updateRoute: boolean) => {
    const nextSymbol = normalizeSymbol(symbol);
    setSelectedSymbolState(nextSymbol);
    setSelectedStock(nextSymbol);
    writeLocalValue(LOCAL_SYMBOL_KEY, nextSymbol);
  }, [setSelectedStock]);

  const selectPool = useCallback((poolId: string) => {
    if (poolId === REPORT_LINKED_POOL_ID) { setSelectedPoolId(poolId); return; }
    setReportContext(null);
    setReportFocus(null);
    setSelectedPoolId(poolId);
    writeLocalValue(LOCAL_POOL_KEY, poolId);
  }, [setReportContext, setReportFocus]);

  useEffect(() => {
    if (!reportFocus || reportFocus.date !== selectedDate) return;
    const contextHasTarget = reportContext && reportContext.date === selectedDate
      && reportContext.stocks.some((stock) => stock.symbol === reportFocus.symbol);
    const cached = queryClient.getQueryData<Awaited<ReturnType<typeof loadReport>>>(['mrgcDailyReport', selectedDate]);
    const fullReport = cached?.report;
    // Keep the current list when possible; restore the whole report, never a singleton filter.
    const currentHasTarget = selectedPoolId === REPORT_LINKED_POOL_ID
      && members.some((stock) => stock.symbol === reportFocus.symbol);
    if (!contextHasTarget && !currentHasTarget && !fullReport?.stocks.some((stock) => stock.symbol === reportFocus.symbol)) return;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      if (!contextHasTarget && !currentHasTarget && fullReport) {
        setReportContext({ date: selectedDate, stocks: reportLinkedStocks(fullReport.stocks), label: '全部日报成员' });
      }
      setSelectedPoolId(REPORT_LINKED_POOL_ID);
      selectSymbol(reportFocus.symbol, false);
      setReportFocus(null);
    });
    return () => { active = false; };
  }, [reportFocus, reportContext, selectedDate, selectedPoolId, members, queryClient, selectSymbol, setReportFocus, setReportContext]);

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      writeLocalValue(LOCAL_SIDEBAR_KEY, next ? '1' : '0');
      return next;
    });
  }, []);

  const refreshAll = () => {
    datesQuery.refetch();
    poolsQuery.refetch();
    if (selectedPoolId !== REPORT_LINKED_POOL_ID) {
      poolStocksQuery.refetch();
      watchSummaryQuery.refetch();
      watchSnapshotsQuery.refetch();
      watchEventsQuery.refetch();
    } else {
      queryClient.invalidateQueries({ queryKey: ['mrgcDailyReport', selectedDate] });
    }
    queryClient.invalidateQueries({ queryKey: ['structureMonitor'] });
    queryClient.invalidateQueries({ queryKey: ['stockInfo', selectedSymbol] });
    queryClient.invalidateQueries({ queryKey: ['stockChartRows', selectedSymbol, 'latest'] });
    queryClient.invalidateQueries({ queryKey: ['stockChartData', selectedSymbol] });
  };

  const createPoolMutation = useMutation({
    mutationFn: () => stockApi.createPool({ name: newPoolName.trim() }),
    onSuccess: (data) => {
      const created = data as { pool?: { pool_id?: string } };
      const poolId = text(created.pool?.pool_id, '');
      setNewPoolName('');
      if (poolId) selectPool(poolId);
      queryClient.invalidateQueries({ queryKey: ['stockPools'] });
    },
  });

  const updatePoolMutation = useMutation({
    mutationFn: () => stockApi.updatePool(editingPoolId, { name: editingPoolName.trim() }),
    onSuccess: () => {
      setEditingPoolId('');
      setEditingPoolName('');
      queryClient.invalidateQueries({ queryKey: ['stockPools'] });
    },
  });

  const deletePoolMutation = useMutation({
    mutationFn: (poolId: string) => stockApi.deletePool(poolId),
    onSuccess: (_data, poolId) => {
      const fallbackPool = defaultPoolId || pools.find((pool) => pool.pool_id !== poolId)?.pool_id || '';
      selectPool(fallbackPool);
      queryClient.invalidateQueries({ queryKey: ['stockPools'] });
      queryClient.invalidateQueries({ queryKey: ['stockPoolStocks'] });
    },
  });

  const addStockMutation = useMutation({
    mutationFn: (symbol: string) => stockApi.addPoolStock(selectedPoolId, { symbol }),
    onSuccess: (_data, symbol) => {
      setKeyword('');
      selectSymbol(symbol, false);
      queryClient.invalidateQueries({ queryKey: ['stockPools'] });
      queryClient.invalidateQueries({ queryKey: ['stockPoolStocks', selectedPoolId] });
    },
  });

  const removeStockMutation = useMutation({
    mutationFn: (symbol: string) => stockApi.removePoolStock(selectedPoolId, symbol),
    onSuccess: (_data, symbol) => {
      if (selectedSymbol === symbol) selectSymbol('', false);
      queryClient.invalidateQueries({ queryKey: ['stockPools'] });
      queryClient.invalidateQueries({ queryKey: ['stockPoolStocks', selectedPoolId] });
    },
  });

  const monitorPoolMutation = useMutation({
    mutationFn: ({ poolId, enabled }: { poolId: string; enabled: boolean }) =>
      stockApi.updatePool(poolId, { monitor_enabled: enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stockPools'] });
      queryClient.invalidateQueries({ queryKey: ['watchSummary'] });
      queryClient.invalidateQueries({ queryKey: ['watchSnapshots'] });
      queryClient.invalidateQueries({ queryKey: ['watchEvents'] });
    },
  });



  useEffect(() => {
    if (!routeSymbol || routeSymbol === selectedSymbol) return;
    queueMicrotask(() => selectSymbol(routeSymbol, false));
  }, [routeSymbol, selectedSymbol, selectSymbol]);

  useEffect(() => {
    if (linkedPool && selectedPoolId !== REPORT_LINKED_POOL_ID) {
      queueMicrotask(() => setSelectedPoolId(REPORT_LINKED_POOL_ID));
      return;
    }
    if (!pools.length || (selectedPoolId && (selectedPoolId !== REPORT_LINKED_POOL_ID || linkedPool))) return;
    const savedPoolId = readLocalValue(LOCAL_POOL_KEY);
    const nextPoolId = pools.some((pool) => pool.pool_id === savedPoolId)
      ? savedPoolId
      : pools.some((pool) => pool.pool_id === defaultPoolId)
        ? defaultPoolId
        : pools[0].pool_id;
    queueMicrotask(() => selectPool(nextPoolId));
  }, [defaultPoolId, pools, linkedPool, selectedPoolId, selectPool]);

  useEffect(() => {
    if (routeSymbol || poolStocksQuery.isLoading || reportFocus) return;
    // Global hints may select a stock outside the left browsing group.
    const sidebarState = useGlobalStore.getState();
    if (['hints', 'ai'].includes(sidebarState.stockSidebarUi.tab)) {
      if (selectedSymbol) return;
      const savedHintSymbol = normalizeSymbol(sidebarState.selectedStock);
      if (savedHintSymbol) {
        queueMicrotask(() => selectSymbol(savedHintSymbol, false));
        return;
      }
    }
    const currentInPool = members.some((stock) => stock.symbol === selectedSymbol);
    if (currentInPool) return;

    const savedSymbol = useGlobalStore.getState().selectedStock || readLocalValue(LOCAL_SYMBOL_KEY);
    const savedInPool = members.find((stock) => stock.symbol === savedSymbol)?.symbol || '';
    const nextSymbol = savedInPool || members[0]?.symbol || '';
    if (nextSymbol !== selectedSymbol) {
      queueMicrotask(() => selectSymbol(nextSymbol, false));
    }
  }, [members, poolStocksQuery.isLoading, routeSymbol, selectedSymbol, selectSymbol, reportFocus]);

  const isBusy = createPoolMutation.isPending
    || updatePoolMutation.isPending
    || deletePoolMutation.isPending
    || addStockMutation.isPending
    || removeStockMutation.isPending
    || monitorPoolMutation.isPending;

  const setSidebarUi = useGlobalStore((state) => state.setStockSidebarUi);
  useEffect(() => {
    if (reportLinked || !selectedPoolId || !selectedDate || !poolStocksQuery.isSuccess) return;
    const scope = { poolId: selectedPoolId, poolName: selectedPool?.name || '当前分组',
      members: members.filter((member) => member.symbol).map(({ symbol, name }) => ({ symbol: symbol!, name })), date: selectedDate };
    if (JSON.stringify(useGlobalStore.getState().stockSidebarUi.scope) !== JSON.stringify(scope)) {
      setSidebarUi({ scope });
    }
  }, [reportLinked, selectedPoolId, selectedDate, selectedPool?.name, members, poolStocksQuery.isSuccess, setSidebarUi]);
  const dailyHintsNode = <AllWatchlistDailyHints date={selectedDate}
    onSelectSymbol={(symbol) => selectSymbol(symbol, false)} onContext={setUnifiedHints} />;

  return (
    <div className="flex h-[calc(100dvh-var(--evo-nav-height,64px))] min-h-0 flex-col">
      {/* 顶部工具栏 */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border-primary bg-bg-secondary/40 px-3 py-2">
        <button
          type="button"
          onClick={toggleSidebar}
          className="h-8 w-8 rounded-md border border-border-primary bg-bg-primary text-text-secondary transition-colors hover:border-brand-blue/60 hover:text-brand-blue-light"
          title={sidebarCollapsed ? '展开分组' : '收起分组'}
        >
          {sidebarCollapsed ? <ChevronRight size={14} className="mx-auto" /> : <ChevronLeft size={14} className="mx-auto" />}
        </button>
        <div className="min-w-0">
          <h1 className="truncate text-[14px] font-black text-white">
            Watchlist <span className="text-text-tertiary">自选股</span>
          </h1>
          <div className="truncate text-[10px] text-text-secondary">
            {text(selectedPool?.name, '未选择')}
            {selectedSymbol ? ` · ${text(selectedMember?.name, selectedSymbol)} ${selectedSymbol}` : ' · 选择股票查看行情'}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {!reportLinked && <WatchRuleScannerPanel
            pools={realPools}
            dates={dates}
            selectedPoolId={selectedPoolId}
            onSelectSymbol={(symbol) => selectSymbol(symbol, false)}
          />}
          {dates.length > 0 && (
            <select
              value={selectedDate}
              onChange={(event) => setGlobalDate(event.target.value)}
              className="h-8 rounded-md border border-border-primary bg-bg-primary px-2 text-[11px] font-mono text-text-primary outline-none"
            >
              {dates.map((date) => (
                <option key={date} value={date}>{date}</option>
              ))}
            </select>
          )}
          <button
            type="button"
            onClick={refreshAll}
            className="h-8 w-8 rounded-md border border-border-primary bg-bg-primary text-text-secondary transition-colors hover:border-brand-blue/60 hover:text-brand-blue-light"
            title="刷新"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mx-auto">
              <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
              <path d="M21 3v5h-5" />
              <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
              <path d="M8 16H3v5" />
            </svg>
          </button>
        </div>
      </div>

      {reportLinked ? (
        <div className="shrink-0 border-b border-border-secondary px-3 py-2 text-[11px] text-text-secondary">
          日报联动为只读筛选，买卖与风险见右侧统一日报。
        </div>
      ) : null}

      {/* 主体：左侧边栏 + 右侧行情 */}
      <div className="flex min-h-0 flex-1">
        {/* 左侧分组边栏 */}
        {!sidebarCollapsed && (
          <aside
            className="flex w-[clamp(280px,20vw,300px)] min-h-0 shrink-0 flex-col overflow-hidden border-r border-border-primary bg-bg-primary/60 transition-all duration-200"
          >
            <PoolSidebar
              pools={pools}
              selectedPoolId={selectedPoolId}
              selectedSymbol={selectedSymbol}
              members={members}
              keyword={keyword}
              searchRows={searchRows}
              newPoolName={newPoolName}
              editingPoolId={editingPoolId}
              editingPoolName={editingPoolName}
              isBusy={isBusy}
              monitoringPoolId={monitorPoolMutation.isPending ? monitorPoolMutation.variables?.poolId : undefined}
              sortKey={sortKey}
              sortDir={sortDir}
              onKeywordChange={setKeyword}
              onNewPoolNameChange={setNewPoolName}
              onCreatePool={() => {
                if (newPoolName.trim()) createPoolMutation.mutate();
              }}
              onSelectPool={selectPool}
              onStartEditPool={(pool) => {
                setEditingPoolId(pool.pool_id);
                setEditingPoolName(text(pool.name, ''));
              }}
              onEditingPoolNameChange={setEditingPoolName}
              onSavePool={() => {
                if (editingPoolId && editingPoolName.trim()) updatePoolMutation.mutate();
              }}
              onCancelEditPool={() => {
                setEditingPoolId('');
                setEditingPoolName('');
              }}
              onDeletePool={(poolId) => deletePoolMutation.mutate(poolId)}
              onToggleMonitor={(pool) => monitorPoolMutation.mutate({
                poolId: pool.pool_id,
                enabled: !isPoolMonitored(pool),
              })}
              onAddStock={(symbol) => {
                if (selectedPoolId && symbol) addStockMutation.mutate(symbol);
              }}
              onSelectStock={(symbol) => selectSymbol(symbol, false)}
              onRemoveStock={(symbol) => removeStockMutation.mutate(symbol)}
              onSort={handleSort}
            />
          </aside>
        )}

        {/* 右侧行情区 */}
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden p-2">
          {reportLinkError && <div role="alert" className="mb-1 shrink-0 text-xs text-warning">{reportLinkError}</div>}
          {reportLinked && reportContext && <div className="mb-1 flex shrink-0 items-center justify-between gap-2 rounded border border-brand-blue/30 bg-brand-blue/5 px-2 py-1 text-[10px] text-brand-blue-light">
            <span>日报联动 · {reportContext.date} · {reportContext.label} · {members.length} 只</span>
            <button type="button" onClick={() => selectPool(defaultPoolId)} className="shrink-0 underline">返回自选分组</button>
          </div>}
          <div className="min-h-0 flex-1">
          {selectedSymbol ? (
            <StockDetailPanel symbol={selectedSymbol} selectedDate={selectedDate} showDetailLink dailyHintsPanel={dailyHintsNode} sidebarScopeKey="__all_watchlist__"
              unifiedHints={unifiedHints?.date === selectedDate && unifiedHints?.poolName === 'Watchlist全部' ? unifiedHints : undefined}
              onSelectSymbol={(symbol) => selectSymbol(symbol, false)} />
          ) : (
            <div className="flex h-full min-h-0 gap-2"><div className="min-w-0 flex-1"><EmptySelection /></div>{dailyHintsNode ? <div className="w-[clamp(320px,25vw,360px)] min-h-0 overflow-y-auto custom-scrollbar">{dailyHintsNode}</div> : reportOpen && <MrgcDailyReportPanel date={selectedDate} />}</div>
          )}
          </div>
        </main>
      </div>
    </div>
  );
}

export function SharedDailyHints({ date, poolId, poolName, members, onSelectSymbol, onContext, coverageStatus = '' }: {
  date: string; poolId: string; poolName: string; members: { symbol: string; name?: string }[];
  onSelectSymbol: (symbol: string) => void; onContext: (context: DailyHintsContext) => void; coverageStatus?: string;
}) {
  const queryClient = useQueryClient();
  const requestPoolId = ['__single__', '__all_watchlist__'].includes(poolId) ? undefined : poolId;
  const watchSummaryQuery = useQuery({
    queryKey: ['watchSummary', date, poolId],
    queryFn: () => watchApi.getSummary({ tradeDate: date, poolId: requestPoolId }),
    enabled: Boolean(date && poolId) && poolId !== REPORT_LINKED_POOL_ID,
    staleTime: 30_000,
  });
  const watchSnapshotsQuery = useQuery({
    queryKey: ['watchSnapshots', date, poolId],
    queryFn: () => watchApi.getSnapshots({ tradeDate: date, poolId: requestPoolId }),
    enabled: Boolean(date && poolId) && poolId !== REPORT_LINKED_POOL_ID,
    staleTime: 30_000,
  });
  const watchEventsQuery = useQuery({
    queryKey: ['watchEvents', date, poolId],
    queryFn: () => watchApi.getEvents({ tradeDate: date, poolId: requestPoolId }),
    enabled: Boolean(date && poolId) && poolId !== REPORT_LINKED_POOL_ID,
    staleTime: 30_000,
  });
  const watchSummary = watchResponse(watchSummaryQuery.data);
  const watchSnapshotsPayload = watchResponse(watchSnapshotsQuery.data);
  const watchEventsPayload = watchResponse(watchEventsQuery.data);
  const watchSnapshots = rows<WatchSnapshot>(
    watchSnapshotsPayload.rows
      || watchSnapshotsPayload.snapshots
      || watchSnapshotsPayload.items
      || watchSnapshotsQuery.data,
  );
  const watchEvents = rows<WatchEvent>(
    watchEventsPayload.rows
      || watchEventsPayload.events
      || watchEventsPayload.items
      || watchEventsQuery.data,
  );


  const watchHintSignals: DailyHintSignal[] = [];
  const snapshotSymbols = new Set<string>();
  const watchHint = (item: WatchSnapshot, state: string): DailyHintSignal | null => {
    const direction = state === 'IN_ZONE' ? 'wait' : state === 'TRIGGERED' ? 'buy'
      : state === 'INVALIDATED' ? 'risk' : ['UNKNOWN', 'DATA_BLOCKED'].includes(state) ? 'blocked' : null;
    if (!direction || !item.symbol) return null;
    const path = setupPathCode(item.setup_path || asRow(item.factors).setup_path);
    const cooling = state === 'TRIGGERED' && (item.is_transition === false
      || item.previous_state === 'TRIGGERED');
    return { symbol: item.symbol, name: text(item.name, item.symbol), source: path !== '-' ? `低吸 ${path}` : 'A/B 低吸',
      label: state === 'IN_ZONE' ? '进入回调区' : state === 'TRIGGERED' ? cooling ? '确认状态 · 冷却中' : '确认触发'
        : state === 'INVALIDATED' ? '低吸失效' : '数据待校验', direction,
      reason: text(item.reason || item.why_now, ''),
      family: 'watch', confirmed: state === 'TRIGGERED',
      period: '日线',
      currentPrice: finiteNumber(item.current_price) ?? undefined,
      triggerPrice: finiteNumber(item.trigger_price) ?? undefined,
      zoneLow: finiteNumber(item.zone_low) ?? undefined,
      zoneHigh: finiteNumber(item.zone_high) ?? undefined,
      stopPrice: finiteNumber(item.invalidation_price) ?? undefined };
  };
  for (const snapshot of watchSnapshots.slice(0, 1000)) {
    if (text(snapshot.trade_date, '') !== date || !snapshot.symbol) continue;
    snapshotSymbols.add(snapshot.symbol);
    const state = text(snapshot.watch_state || snapshot.state, '').toUpperCase();
    const signal = watchHint(snapshot, snapshot.data_status && snapshot.data_status !== 'OK' ? 'UNKNOWN' : state);
    if (signal) watchHintSignals.push(signal);
  }
  for (const event of watchEvents.slice(0, 1000)) {
    if (event.trade_date !== date || !event.symbol || snapshotSymbols.has(event.symbol)) continue;
    if (['acknowledged', 'resolved'].includes(text(event.status, '').toLowerCase())) continue;
    const payload = asRow(event.payload);
    const signal = watchHint({ ...payload, symbol: event.symbol,
      name: text(payload.name, event.symbol), reason: text(payload.reason || event.message, '') }, normalizedWatchEventType(event));
    if (signal) watchHintSignals.push(signal);
  }
  const updateWatchEventMutation = useMutation({
    mutationFn: ({
      eventId,
      status,
      snoozedUntil,
    }: {
      eventId: string | number;
      status: 'acknowledged' | 'snoozed';
      snoozedUntil?: string;
    }) => watchApi.updateEvent(eventId, {
      status,
      snoozed_until: snoozedUntil,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchSummary'] });
      queryClient.invalidateQueries({ queryKey: ['watchEvents'] });
    },
  });
  const memberSymbols = new Set(members.map((member) => member.symbol));
  const scopedSnapshots = watchSnapshots.filter((snapshot) => memberSymbols.has(snapshot.symbol || ''));
  const scopedEvents = watchEvents.filter((event) => !event.symbol || memberSymbols.has(event.symbol));
  return <DailyHintsPanel date={date} poolId={poolId} poolName={poolName} members={members}
    watchSignals={watchHintSignals.filter((signal) => memberSymbols.has(signal.symbol))}
    onSelectSymbol={onSelectSymbol} onContext={onContext}
    watchStatus={coverageStatus || (watchSnapshotsQuery.isError ? '低吸状态读取失败' : watchSnapshotsQuery.isLoading ? '低吸状态读取中'
      : !scopedSnapshots.length ? '低吸所选日期暂无状态' : scopedEvents.some((event) => !event.symbol && event.trade_date === date && !['resolved', 'acknowledged'].includes(text(event.status, '').toLowerCase()) && !event.resolved_at && normalizedWatchEventType(event) === 'DATA_BLOCKED') ? '存在系统数据阻断，请查看历史提醒' : '')}
    legacyActions={<TodayActionBox compact scopeKey={poolId} tradeDate={date} poolName={poolId === '__single__' ? `${poolName} · 含系统提醒` : poolName}
      summary={['__single__', '__all_watchlist__'].includes(poolId) ? {} : watchSummary} snapshots={scopedSnapshots} events={scopedEvents}
      loading={watchSummaryQuery.isLoading || watchSnapshotsQuery.isLoading || watchEventsQuery.isLoading}
      failed={watchSummaryQuery.isError || watchSnapshotsQuery.isError || watchEventsQuery.isError}
      updatingEventId={updateWatchEventMutation.variables?.eventId}
      onAcknowledge={(eventId) => updateWatchEventMutation.mutate({ eventId, status: 'acknowledged' })}
      onSnooze={(eventId) => updateWatchEventMutation.mutate({ eventId, status: 'snoozed', snoozedUntil: text(watchSummary.next_trade_date, '') || nextWeekday(date) })}
      onSelectSymbol={onSelectSymbol} />} />;
}

/** All real Watchlist pools share one deduplicated opportunity scope. */
export function AllWatchlistDailyHints({ date, onSelectSymbol, onContext }: {
  date: string; onSelectSymbol: (symbol: string) => void;
  onContext: (context: DailyHintsContext) => void;
}) {
  const poolsQuery = useQuery({ queryKey: ['stockPools', date],
    queryFn: () => stockApi.getPools(date), enabled: Boolean(date), staleTime: 30_000 });
  const pools = rows<StockPool>(poolsQuery.data?.pools)
    .filter((pool) => pool.pool_id && !pool.is_report && pool.pool_id !== REPORT_LINKED_POOL_ID);
  const boundedPools = pools.slice(0, 32);
  const memberQueries = useQueries({ queries: boundedPools.map((pool) => ({
    queryKey: ['stockPoolStocks', pool.pool_id, date],
    queryFn: () => stockApi.getPoolStocks(pool.pool_id, date),
    enabled: Boolean(date), staleTime: 30_000,
  })) });
  const bySymbol = new Map<string, { symbol: string; name?: string }>();
  for (const query of memberQueries) {
    for (const stock of rows<PoolMember>(query.data?.stocks).slice(0, 2000)) {
      if (stock.symbol && !bySymbol.has(stock.symbol)) bySymbol.set(stock.symbol, { symbol: stock.symbol, name: stock.name });
      if (bySymbol.size >= 6000) break;
    }
  }
  const pending = poolsQuery.isLoading || memberQueries.some((query) => query.isLoading);
  const failed = poolsQuery.isError || memberQueries.some((query) => query.isError);
  const coverageStatus = pending ? '正在读取 Watchlist 全部分组，覆盖尚未完成'
    : failed ? '部分分组读取失败，当前仅展示已读取成员机会'
    : pools.length > 32 || bySymbol.size >= 6000 ? '已达到覆盖上限，当前机会可能不完整' : '';
  const forwardContext = useCallback((context: DailyHintsContext) => {
    if (!pending) onContext(context);
  }, [pending, onContext]);
  return <SharedDailyHints date={date} poolId="__all_watchlist__" poolName="Watchlist全部"
    members={Array.from(bySymbol.values())} coverageStatus={coverageStatus}
    onSelectSymbol={onSelectSymbol} onContext={forwardContext} />;
}
