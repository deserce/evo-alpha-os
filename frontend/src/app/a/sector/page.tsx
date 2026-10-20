'use client';

import { Fragment, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { ArrowDownUp, CandlestickChart, ExternalLink, Flame, Layers, Network, Search } from 'lucide-react';
import { quantApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import SectorChart from '@/components/sector/SectorChart';
import StockChart from '@/components/stock/StockChart';
import {
  CockpitPanel,
  ProgressLine,
  Row,
  StageBadge,
  StatCard,
  WorkbenchPage,
  clamp,
  int,
  money,
  num,
  pct,
  ratioPct,
  rows,
  text,
  toNumber,
} from '@/components/workspace/CockpitPrimitives';

const RPS_PERIODS = [5, 10, 20, 50, 120, 250];
type SortDirection = 'asc' | 'desc';
const STOCK_SORT_DEFAULT_DIRECTIONS: Record<string, SortDirection> = {
  symbol: 'asc',
  name: 'asc',
  stock_info_industry: 'asc',
  lifecycle_state: 'desc',
  market_state: 'desc',
  industry_state: 'desc',
  signal_type: 'asc',
};
const NUMERIC_STOCK_SORT_KEYS = new Set([
  'close',
  'pct_chg',
  'amount',
  'volume',
  'state_rank',
  'structure_score',
  'rps_5',
  'rps_10',
  'rps_20',
  'rps_50',
  'rps_120',
  'rps_250',
]);

function RpsSelect({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(Number(event.target.value))}
      className="h-8 rounded-lg border border-border-primary bg-bg-primary px-2 text-xs font-mono text-text-primary outline-none"
    >
      {RPS_PERIODS.map((period) => (
        <option key={period} value={period}>
          RPS{period}
        </option>
      ))}
    </select>
  );
}

function dateLabel(value: string) {
  return value.length >= 10 ? value.slice(5, 10) : value;
}

function stockIndustry(row: Row) {
  return text(row.stock_info_industry || row.lifecycle_sector_name || row.sector_name);
}

function heatmapStyle(value: unknown, active: boolean) {
  const score = toNumber(value, Number.NaN);
  if (!Number.isFinite(score)) {
    return {
      borderColor: active ? 'rgba(86,216,255,0.46)' : 'rgba(255,255,255,0.06)',
      background: 'rgba(255,255,255,0.035)',
    };
  }

  const strong = score >= 50;
  const intensity = strong ? clamp(score, 50, 100) : clamp(100 - score, 50, 100);
  const alpha = 0.1 + intensity / 165;
  return {
    borderColor: active ? 'rgba(86,216,255,0.7)' : 'rgba(255,255,255,0.06)',
    background: strong
      ? `linear-gradient(135deg, rgba(255,79,109,${alpha}), rgba(45,18,28,0.92))`
      : `linear-gradient(135deg, rgba(40,224,112,${alpha}), rgba(10,35,31,0.92))`,
  };
}

function heatmapValue(row: Row, date: string) {
  const values = typeof row.values === 'object' && row.values
    ? row.values as Record<string, unknown>
    : {};
  return toNumber(values[date], Number.NaN);
}

function sortHeatmapSectors(sectors: Row[], date: string, direction: SortDirection) {
  if (!date) return sectors;
  const multiplier = direction === 'desc' ? -1 : 1;
  return [...sectors].sort((left, right) => {
    const leftValue = heatmapValue(left, date);
    const rightValue = heatmapValue(right, date);
    const leftMissing = !Number.isFinite(leftValue);
    const rightMissing = !Number.isFinite(rightValue);

    if (leftMissing && rightMissing) {
      return toNumber(left.sort_order) - toNumber(right.sort_order);
    }
    if (leftMissing) return 1;
    if (rightMissing) return -1;
    if (leftValue !== rightValue) return (leftValue - rightValue) * multiplier;
    return toNumber(left.sort_order) - toNumber(right.sort_order);
  });
}

function sortIndustryStocks(stocks: Row[], sortKey: string, direction: SortDirection) {
  const multiplier = direction === 'desc' ? -1 : 1;
  const numeric = NUMERIC_STOCK_SORT_KEYS.has(sortKey);

  return [...stocks].sort((left, right) => {
    const leftRaw = left[sortKey];
    const rightRaw = right[sortKey];
    const leftMissing = leftRaw === null || leftRaw === undefined || leftRaw === '';
    const rightMissing = rightRaw === null || rightRaw === undefined || rightRaw === '';

    if (leftMissing && rightMissing) {
      return text(left.symbol, '').localeCompare(text(right.symbol, ''), 'zh-Hans-CN');
    }
    if (leftMissing) return 1;
    if (rightMissing) return -1;

    if (numeric) {
      const leftValue = toNumber(leftRaw, Number.NaN);
      const rightValue = toNumber(rightRaw, Number.NaN);
      const leftInvalid = !Number.isFinite(leftValue);
      const rightInvalid = !Number.isFinite(rightValue);

      if (leftInvalid && rightInvalid) {
        return text(left.symbol, '').localeCompare(text(right.symbol, ''), 'zh-Hans-CN');
      }
      if (leftInvalid) return 1;
      if (rightInvalid) return -1;
      if (leftValue !== rightValue) return (leftValue - rightValue) * multiplier;
      return text(left.symbol, '').localeCompare(text(right.symbol, ''), 'zh-Hans-CN');
    }

    const compared = text(leftRaw, '').localeCompare(text(rightRaw, ''), 'zh-Hans-CN');
    if (compared !== 0) return compared * multiplier;
    return text(left.symbol, '').localeCompare(text(right.symbol, ''), 'zh-Hans-CN');
  });
}

function SectorRpsHeatmap({
  dates,
  sectors,
  rpsPeriod,
  currentName,
  sortDate,
  sortDirection,
  onSort,
  onSelect,
}: {
  dates: string[];
  sectors: Row[];
  rpsPeriod: number;
  currentName: string;
  sortDate: string;
  sortDirection: SortDirection;
  onSort: (date: string) => void;
  onSelect: (sectorName: string) => void;
}) {
  if (!dates.length || !sectors.length) {
    return (
      <div className="flex h-[180px] items-center justify-center rounded-lg border border-border-secondary bg-bg-primary/60 text-[11px] text-text-tertiary">
        暂无行业 RPS 热图数据
      </div>
    );
  }

  return (
    <div className="max-h-[510px] overflow-auto custom-scrollbar">
      <div className="min-w-[920px]">
        <div
          className="grid gap-1"
          style={{ gridTemplateColumns: `minmax(148px, 168px) repeat(${dates.length}, minmax(32px, 1fr))` }}
        >
          <div
            className="sticky top-0 z-20 h-7 rounded border border-border-secondary px-2 py-1 text-[10px] font-bold text-text-tertiary shadow-[0_2px_0_rgba(0,0,0,0.55)]"
            style={{ backgroundColor: '#07111f' }}
          >
            行业
          </div>
          {dates.map((date) => {
            const activeSort = sortDate === date;
            return (
              <button
                key={date}
                type="button"
                onClick={() => onSort(date)}
                className={`sticky top-0 z-20 h-7 rounded border px-1 py-1 text-center font-mono text-[9px] font-bold shadow-[0_2px_0_rgba(0,0,0,0.55)] transition-colors ${
                  activeSort
                    ? 'border-brand-blue/70 text-brand-blue-light'
                    : 'border-border-secondary text-text-tertiary hover:border-brand-blue/45 hover:text-white'
                }`}
                style={{ backgroundColor: activeSort ? '#0a2539' : '#07111f' }}
                title={`${date} RPS${rpsPeriod} ${activeSort && sortDirection === 'asc' ? '升序' : '降序'}排序`}
              >
                {dateLabel(date)}
                {activeSort && (
                  <span className="ml-0.5 text-[8px]">{sortDirection === 'desc' ? '↓' : '↑'}</span>
                )}
              </button>
            );
          })}

          {sectors.map((row, rowIndex) => {
            const sectorName = text(row.sector_name, '');
            const values = typeof row.values === 'object' && row.values
              ? row.values as Record<string, unknown>
              : {};
            const active = sectorName === currentName;
            const rank = String(rowIndex + 1).padStart(2, '0');

            return (
              <Fragment key={`${sectorName}-${rowIndex}`}>
                <button
                  type="button"
                  onClick={() => onSelect(sectorName)}
                  className={`grid h-7 min-w-0 grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-1.5 rounded border px-2 text-left text-[10px] transition-colors ${
                    active
                      ? 'border-brand-blue/70 bg-brand-blue/15 text-white'
                      : 'border-border-secondary bg-bg-primary/55 text-text-secondary hover:border-brand-blue/45 hover:text-white'
                  }`}
                  title={`${sectorName} · ${int(row.stock_count)} 只`}
                >
                  <span className="text-right font-mono text-[9px] text-text-tertiary tabular-nums">{rank}</span>
                  <span className="min-w-0 truncate font-black">{sectorName}</span>
                  <span className="shrink-0 font-mono text-[9px] text-text-tertiary">{int(row.stock_count)}</span>
                </button>
                {dates.map((date) => {
                  const value = values[date];
                  const score = toNumber(value, Number.NaN);
                  return (
                    <button
                      key={`${sectorName}-${date}`}
                      type="button"
                      onClick={() => onSelect(sectorName)}
                      className="h-7 rounded border px-0.5 text-center font-mono text-[9px] font-black text-white/90 transition-transform hover:-translate-y-px"
                      style={heatmapStyle(value, active)}
                      title={`${sectorName} ${date} RPS${rpsPeriod}: ${num(value, 1)}`}
                    >
                      {Number.isFinite(score) ? score.toFixed(0) : '-'}
                    </button>
                  );
                })}
              </Fragment>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function IndustryStockWorkbench({
  sectorName,
  tradeDate,
  stocks,
  total,
  rpsPeriod,
  sortKey,
  sortDirection,
  onSort,
  onOpenStock,
}: {
  sectorName: string;
  tradeDate: string;
  stocks: Row[];
  total: number;
  rpsPeriod: number;
  sortKey: string;
  sortDirection: SortDirection;
  onSort: (key: string) => void;
  onOpenStock: (symbol: string) => void;
}) {
  const [chartMode, setChartMode] = useState<'sector' | 'stock'>('sector');
  const [selectedSymbol, setSelectedSymbol] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const listRef = useRef<HTMLDivElement | null>(null);
  const rpsKey = `rps_${rpsPeriod}`;

  const filteredStocks = useMemo(() => {
    const keyword = searchQuery.trim().toLocaleLowerCase('zh-CN');
    if (!keyword) return stocks;
    return stocks.filter((row) => [row.symbol, row.name, stockIndustry(row)]
      .some((value) => text(value, '').toLocaleLowerCase('zh-CN').includes(keyword)));
  }, [searchQuery, stocks]);

  const selectedStock = stocks.find((row) => text(row.symbol, '') === selectedSymbol);
  const selectedIndex = filteredStocks.findIndex((row) => text(row.symbol, '') === selectedSymbol);

  const selectStock = (row: Row) => {
    const symbol = text(row.symbol, '');
    if (!symbol || symbol === '-') return;
    setSelectedSymbol(symbol);
    setChartMode('stock');
    requestAnimationFrame(() => {
      listRef.current
        ?.querySelector<HTMLElement>(`[data-symbol="${symbol}"]`)
        ?.scrollIntoView({ block: 'nearest' });
    });
  };

  const handleListKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowDown', 'ArrowUp'].includes(event.key) || !filteredStocks.length) return;
    event.preventDefault();
    const direction = event.key === 'ArrowDown' ? 1 : -1;
    const baseIndex = selectedIndex >= 0 ? selectedIndex : direction > 0 ? -1 : 0;
    const nextIndex = Math.min(filteredStocks.length - 1, Math.max(0, baseIndex + direction));
    selectStock(filteredStocks[nextIndex]);
  };

  const sortButton = (key: string, label: string, align: 'left' | 'right' = 'right') => {
    const active = sortKey === key;
    return (
      <button
        type="button"
        onClick={() => onSort(key)}
        className={`inline-flex w-full items-center gap-1 ${align === 'right' ? 'justify-end' : 'justify-start'} transition-colors hover:text-brand-blue-light`}
      >
        <span>{label}</span>
        <ArrowDownUp className={`h-2.5 w-2.5 ${active ? 'text-brand-blue-light' : 'text-text-disabled'}`} />
        {active ? <span className="font-mono text-[8px]">{sortDirection === 'desc' ? '↓' : '↑'}</span> : null}
      </button>
    );
  };

  const selectedName = text(selectedStock?.name, selectedSymbol || '个股');
  const selectedPct = toNumber(selectedStock?.pct_chg, Number.NaN);

  return (
    <div className="grid gap-2 xl:grid-cols-[minmax(0,1fr)_430px]">
      <section className="flex h-[620px] min-w-0 flex-col overflow-hidden rounded-lg border border-border-secondary bg-bg-primary/35">
        <div className="flex min-h-9 items-center justify-between gap-2 border-b border-border-secondary bg-bg-secondary/45 px-2">
          <div className="flex min-w-0 items-center gap-1">
            <button
              type="button"
              onClick={() => setChartMode('sector')}
              className={`inline-flex h-7 items-center gap-1.5 rounded border px-2 text-[11px] font-black transition-colors ${
                chartMode === 'sector'
                  ? 'border-brand-blue/70 bg-brand-blue/15 text-brand-blue-light'
                  : 'border-border-primary bg-bg-primary/50 text-text-secondary hover:border-brand-blue/45 hover:text-white'
              }`}
            >
              <Layers className="h-3.5 w-3.5" />
              <span className="max-w-[180px] truncate">{sectorName}</span>
            </button>
            <button
              type="button"
              disabled={!selectedStock}
              onClick={() => selectedStock && setChartMode('stock')}
              className={`inline-flex h-7 min-w-0 items-center gap-1.5 rounded border px-2 text-[11px] font-black transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                chartMode === 'stock'
                  ? 'border-brand-blue/70 bg-brand-blue/15 text-brand-blue-light'
                  : 'border-border-primary bg-bg-primary/50 text-text-secondary hover:border-brand-blue/45 hover:text-white'
              }`}
            >
              <CandlestickChart className="h-3.5 w-3.5 shrink-0" />
              <span className="max-w-[160px] truncate">{selectedName}</span>
            </button>
          </div>

          {selectedStock ? (
            <div className="flex shrink-0 items-center gap-2 text-[10px]">
              <span className="font-mono text-text-tertiary">{selectedSymbol}</span>
              <span className={`font-mono font-black ${selectedPct >= 0 ? 'text-danger' : 'text-success'}`}>
                {pct(selectedStock.pct_chg)}
              </span>
              <button
                type="button"
                onClick={() => onOpenStock(selectedSymbol)}
                className="flex h-6 w-6 items-center justify-center rounded border border-border-primary text-text-secondary transition hover:border-brand-blue/55 hover:text-brand-blue-light"
                title="打开完整个股详情"
                aria-label="打开完整个股详情"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 p-1.5">
          {chartMode === 'stock' && selectedStock ? (
            <StockChart symbol={selectedSymbol} limit={260} />
          ) : (
            <SectorChart sectorName={sectorName} tradeDate={tradeDate} limit={260} />
          )}
        </div>
      </section>

      <aside className="flex h-[620px] min-w-0 flex-col overflow-hidden rounded-lg border border-border-secondary bg-bg-primary/35">
        <div className="flex min-h-9 items-center justify-between gap-2 border-b border-border-secondary bg-bg-secondary/45 px-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-xs font-black text-text-primary">成分股</span>
            <span className="font-mono text-[10px] text-text-tertiary">
              {selectedIndex >= 0 ? `${selectedIndex + 1} / ${filteredStocks.length}` : `${filteredStocks.length} / ${int(total)}`}
            </span>
          </div>
          <div className="relative w-[190px] max-w-[52%]">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-tertiary" />
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              onKeyDown={(event) => event.stopPropagation()}
              placeholder="代码 / 名称"
              className="h-7 w-full rounded border border-border-primary bg-bg-primary pl-7 pr-2 text-[10px] text-text-primary outline-none placeholder:text-text-disabled focus:border-brand-blue/60"
            />
          </div>
        </div>

        <div
          ref={listRef}
          tabIndex={0}
          onKeyDown={handleListKeyDown}
          onClick={() => listRef.current?.focus({ preventScroll: true })}
          className="min-h-0 flex-1 overflow-auto outline-none custom-scrollbar focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-brand-blue-light/70"
        >
          <table className="w-full min-w-[410px] table-fixed border-separate border-spacing-0 text-left text-[10px]">
            <thead className="sticky top-0 z-10 bg-[#07111f] text-text-tertiary shadow-[0_1px_0_#1d2a3a]">
              <tr>
                <th className="w-8 px-1 py-1.5 text-center font-bold">#</th>
                <th className="w-[138px] px-1.5 py-1.5 font-bold">{sortButton('name', '股票', 'left')}</th>
                <th className="w-[46px] px-1 py-1.5 text-center font-bold">阶段</th>
                <th className="w-[58px] px-1 py-1.5 text-right font-bold">{sortButton('pct_chg', '涨跌')}</th>
                <th className="w-[52px] px-1 py-1.5 text-right font-bold">{sortButton(rpsKey, `R${rpsPeriod}`)}</th>
                <th className="w-[68px] px-1 py-1.5 text-right font-bold">{sortButton('amount', '成交')}</th>
              </tr>
            </thead>
            <tbody>
              {filteredStocks.length ? filteredStocks.map((row, index) => {
                const symbol = text(row.symbol, '');
                const active = symbol === selectedSymbol;
                const change = toNumber(row.pct_chg, Number.NaN);
                return (
                  <tr
                    key={`${symbol}-${index}`}
                    data-symbol={symbol}
                    onClick={() => selectStock(row)}
                    onDoubleClick={() => onOpenStock(symbol)}
                    className={`cursor-pointer transition-colors ${
                      active
                        ? 'bg-brand-blue/16 text-text-primary shadow-[inset_2px_0_0_rgba(86,216,255,0.95)]'
                        : 'text-text-secondary hover:bg-bg-hover/45 hover:text-text-primary'
                    }`}
                  >
                    <td className="border-b border-border-secondary/65 px-1 py-1.5 text-center font-mono text-[9px] text-text-tertiary">
                      {index + 1}
                    </td>
                    <td className="border-b border-border-secondary/65 px-1.5 py-1">
                      <div className="truncate text-[11px] font-black">{text(row.name, symbol)}</div>
                      <div className="font-mono text-[9px] text-text-tertiary">{symbol}</div>
                    </td>
                    <td className="border-b border-border-secondary/65 px-1 py-1 text-center">
                      <StageBadge value={row.lifecycle_state || '未入池'} />
                    </td>
                    <td className={`border-b border-border-secondary/65 px-1 py-1 text-right font-mono font-black ${
                      Number.isFinite(change) ? (change >= 0 ? 'text-danger' : 'text-success') : 'text-text-tertiary'
                    }`}>
                      {pct(row.pct_chg)}
                    </td>
                    <td className="border-b border-border-secondary/65 px-1 py-1 text-right font-mono font-black text-text-primary">
                      {num(row[rpsKey], 0)}
                    </td>
                    <td className="border-b border-border-secondary/65 px-1 py-1 text-right font-mono text-text-secondary">
                      {money(row.amount)}
                    </td>
                  </tr>
                );
              }) : (
                <tr>
                  <td colSpan={6} className="px-3 py-12 text-center text-[11px] text-text-tertiary">
                    {stocks.length ? '没有匹配的成分股' : '暂无成分股数据'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </aside>
    </div>
  );
}

export default function IndustriesPage() {
  const router = useRouter();
  const { globalDate, setGlobalDate, selectedSector: globalSector, setSelectedSector } = useGlobalStore();
  const [localSector, setLocalSector] = useState(globalSector);
  const [rpsPeriod, setRpsPeriod] = useState(20);
  const [heatmapSortDate, setHeatmapSortDate] = useState('');
  const [heatmapSortDirection, setHeatmapSortDirection] = useState<SortDirection>('desc');
  const [stockSortBy, setStockSortBy] = useState('');
  const [stockSortDirection, setStockSortDirection] = useState<SortDirection>('desc');
  const sectorRpsKey = `sector_rps_${rpsPeriod}`;
  const stockRpsKey = `rps_${rpsPeriod}`;
  const activeStockSortBy = !stockSortBy || stockSortBy.startsWith('rps_')
    ? stockRpsKey
    : stockSortBy;

  const datesQuery = useQuery({ queryKey: ['quantDates'], queryFn: () => quantApi.getDates(160) });
  const dates = rows<string>(datesQuery.data?.dates);
  const selectedDate = dates.includes(globalDate) ? globalDate : datesQuery.data?.latest || '';

  const industryQuery = useQuery({
    queryKey: ['industriesStrength', selectedDate, sectorRpsKey],
    queryFn: () => quantApi.getIndustryStrength(selectedDate, 120, sectorRpsKey),
    enabled: Boolean(selectedDate),
    staleTime: 60_000,
  });
  const heatmapQuery = useQuery({
    queryKey: ['sectorRpsHeatmap', selectedDate, rpsPeriod],
    queryFn: () => quantApi.getSectorRpsHeatmap({
      tradeDate: selectedDate,
      rpsPeriod,
      days: 20,
      sectors: 100,
    }),
    enabled: Boolean(selectedDate),
    staleTime: 60_000,
  });

  const industries = rows<Row>(industryQuery.data?.rows);
  const heatmapDates = rows<string>(heatmapQuery.data?.dates);
  const heatmapSectors = rows<Row>(heatmapQuery.data?.sectors);
  const activeHeatmapSortDate = heatmapDates.includes(heatmapSortDate)
    ? heatmapSortDate
    : heatmapDates[0] || '';
  const sortedHeatmapSectors = useMemo(
    () => sortHeatmapSectors(heatmapSectors, activeHeatmapSortDate, heatmapSortDirection),
    [heatmapSectors, activeHeatmapSortDate, heatmapSortDirection],
  );
  const currentName = localSector || text(sortedHeatmapSectors[0]?.sector_name || industries[0]?.sector_name, '');
  const current = (
    industries.find((row) => row.sector_name === currentName)
    || sortedHeatmapSectors.find((row) => row.sector_name === currentName)
    || industries[0]
    || sortedHeatmapSectors[0]
    || {}
  );

  const stocksQuery = useQuery({
    queryKey: ['industryStocks', selectedDate, currentName, rpsPeriod],
    queryFn: () => quantApi.getIndustryStocks({
      tradeDate: selectedDate,
      sectorName: currentName,
      rpsPeriod,
      sortBy: stockRpsKey,
      sortDir: 'desc',
      limit: 500,
    }),
    enabled: Boolean(selectedDate && currentName),
    staleTime: 60_000,
  });

  const stocks = rows<Row>(stocksQuery.data?.rows);
  const sortedStocks = useMemo(
    () => sortIndustryStocks(stocks, activeStockSortBy, stockSortDirection),
    [stocks, activeStockSortBy, stockSortDirection],
  );
  const totalStocks = toNumber(stocksQuery.data?.total, stocks.length);
  const s4s5 = stocks.filter((row) => ['S4', 'S5'].includes(String(row.lifecycle_state))).length;

  const refresh = () => {
    datesQuery.refetch();
    industryQuery.refetch();
    heatmapQuery.refetch();
    stocksQuery.refetch();
  };

  const sortStocks = (key: string) => {
    if (key === activeStockSortBy) {
      setStockSortDirection((direction) => direction === 'desc' ? 'asc' : 'desc');
      return;
    }
    setStockSortBy(key);
    setStockSortDirection(STOCK_SORT_DEFAULT_DIRECTIONS[key] || 'desc');
  };

  return (
    <WorkbenchPage
      index="03"
      title="Industries  行业战役"
      subtitle="先找主战场，再看行业内 S3/S4/S5 梯队与龙头扩散。"
      date={selectedDate}
      dates={dates}
      onDateChange={setGlobalDate}
      onRefresh={refresh}
    >
      <CockpitPanel
        title="行业 RPS 热图"
        icon={Layers}
        extra={
          <div className="flex items-center gap-2">
            <span className="rounded border border-border-secondary bg-bg-primary px-2 py-1 font-mono text-[10px] text-text-tertiary">
              Top100×20 · 15行
            </span>
            <RpsSelect value={rpsPeriod} onChange={setRpsPeriod} />
          </div>
        }
      >
        <SectorRpsHeatmap
          dates={heatmapDates}
          sectors={sortedHeatmapSectors}
          rpsPeriod={rpsPeriod}
          currentName={currentName}
          sortDate={activeHeatmapSortDate}
          sortDirection={heatmapSortDirection}
          onSort={(date) => {
            if (date === activeHeatmapSortDate) {
              setHeatmapSortDirection((direction) => direction === 'desc' ? 'asc' : 'desc');
              return;
            }
            setHeatmapSortDate(date);
            setHeatmapSortDirection('desc');
          }}
          onSelect={(name) => {
            setLocalSector(name);
            setSelectedSector(name);
          }}
        />
      </CockpitPanel>

      <div className="mt-3 grid gap-3">
        <CockpitPanel title="行业作战详情" icon={Flame}>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-2xl font-black text-white">{text(current.sector_name, '行业待选')}</h2>
                <StageBadge value={current.industry_state} />
              </div>
              <div className="mt-1 text-[11px] text-text-secondary">
                领涨 {text(current.leader_symbol)} · 成交 {money(current.amount)} · RPS{rpsPeriod} {num(current[sectorRpsKey])}
              </div>
            </div>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-5">
            <StatCard label="行业强度" value={num(current.industry_strength_score, 0)} tone="good" />
            <StatCard label={`${rpsPeriod}日RPS`} value={num(current[sectorRpsKey], 0)} tone="blue" />
            <StatCard label="20日收益" value={pct(current.return_20d)} tone={toNumber(current.return_20d) >= 0 ? 'good' : 'bad'} />
            <StatCard label="RPS90占比" value={ratioPct(current.rps90_stock_ratio)} sub={`${int(current.rps90_stock_count)} 只`} tone="blue" />
            <StatCard label="S4/S5" value={s4s5} sub={`成分 ${int(totalStocks)} 只`} tone="warn" />
          </div>
          <div className="mt-3 space-y-2">
            <ProgressLine label="成交放大" value={clamp(toNumber(current.amount_ratio_20, 1) * 45)} tone="blue" right={num(current.amount_ratio_20)} />
            <ProgressLine label="新高占比" value={clamp(toNumber(current.new_high_stock_ratio) * 100)} tone="good" right={ratioPct(current.new_high_stock_ratio)} />
            <ProgressLine label="龙头强度" value={clamp(toNumber(current.leader_strength_score))} tone="warn" right={num(current.leader_strength_score, 0)} />
          </div>
          <div className="mt-3">
            <IndustryStockWorkbench
              key={currentName}
              sectorName={currentName}
              tradeDate={selectedDate}
              stocks={sortedStocks}
              total={totalStocks}
              rpsPeriod={rpsPeriod}
              sortKey={activeStockSortBy}
              sortDirection={stockSortDirection}
              onSort={sortStocks}
              onOpenStock={(symbol) => router.push(`/a/stock/${symbol}`)}
            />
          </div>
        </CockpitPanel>
      </div>

      <div className="mt-3">
        <CockpitPanel title="产业链节奏" icon={Network}>
          <div className="grid gap-2 md:grid-cols-5">
            {['主线确认', '龙头启动', '中军扩散', '补涨轮动', '风险退潮'].map((label, index) => (
              <div key={label} className="rounded-lg border border-brand-blue/35 bg-brand-blue/10 px-3 py-3 text-center">
                <div className="font-mono text-[10px] text-brand-blue-light">0{index + 1}</div>
                <div className="mt-1 text-[12px] font-black text-white">{label}</div>
              </div>
            ))}
          </div>
        </CockpitPanel>
      </div>
    </WorkbenchPage>
  );
}
