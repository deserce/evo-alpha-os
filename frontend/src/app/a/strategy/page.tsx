'use client';

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  GripVertical,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Sparkles,
  Target,
  Zap,
} from 'lucide-react';

import type { DailyHintsContext } from '@/components/stock/DailyHintsPanel';
import { StockDetailPanel as SharedStockDetailPanel, AllWatchlistDailyHints } from '@/components/stock/StockWorkspace';
import {
  type Row,
  StageBadge,
  int,
  num,
  pct,
  rows,
  text,
  toNumber,
} from '@/components/workspace/CockpitPrimitives';
import { AppShell } from '@/components/workspace/DashboardPrimitives';
import { quantApi, stockApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import { resolveStrategyDate, strategyDateOptions } from '@/lib/strategyDateSelection';

type SourceId = 'mrgc' | 'alpha' | 'pocket' | 'breakout' | 'exhaustion';
type SortKey = 'close' | 'pct_chg' | 'rps_5' | 'rps_10' | 'rps_20' | 'rps_50' | 'rps_120' | 'rps_250';
type SortState = { key: SortKey; dir: 'asc' | 'desc' };

type SourceOption = {
  id: SourceId;
  label: string;
  short: string;
  hint: string;
  icon: typeof Sparkles;
  tone: 'cyan' | 'green' | 'amber' | 'rose' | 'blue';
};

type TableColumn = {
  key: string;
  label: string;
  sortKey?: SortKey;
  align?: 'left' | 'right' | 'center';
  className?: string;
  stickyLeft?: string;
  stickyShadow?: boolean;
  render: (row: Row) => ReactNode;
};

const SOURCE_OPTIONS: SourceOption[] = [
  { id: 'mrgc', label: '陶博士 MRGC', short: 'MRGC', hint: '形态选股', icon: Sparkles, tone: 'cyan' },
  { id: 'alpha', label: 'Alpha Pool', short: 'Alpha', hint: 'S4/S5 机会', icon: Target, tone: 'blue' },
  { id: 'pocket', label: '口袋买点', short: '口袋', hint: 'Pocket Pivot', icon: Zap, tone: 'green' },
  { id: 'breakout', label: '突破买点', short: '突破', hint: 'Breakout', icon: Zap, tone: 'amber' },
  { id: 'exhaustion', label: '抛压衰竭', short: '衰竭', hint: 'Supply Exhaustion', icon: Zap, tone: 'rose' },
];

const SIGNAL_SOURCE_TYPE: Partial<Record<SourceId, string>> = {
  pocket: 'pocket_pivot',
  breakout: 'breakout',
  exhaustion: 'supply_exhaustion',
};

const RPS_SORT_KEYS: SortKey[] = ['rps_5', 'rps_10', 'rps_20', 'rps_50', 'rps_120', 'rps_250'];
const SPLIT_STORAGE_KEY = 'evoalpha.strategy.splitPercent.v2';

function clampSplit(value: number) {
  return Math.min(48, Math.max(28, value));
}

function rowSymbol(row: Row) {
  return text(row.symbol, '');
}

function numericValue(row: Row, key: SortKey | 'signal_score') {
  const rawValue = row[key];
  if (rawValue === null || rawValue === undefined || rawValue === '') return null;
  const value = toNumber(rawValue, Number.NaN);
  return Number.isFinite(value) ? value : null;
}

function compareNullableNumber(
  a: number | null,
  b: number | null,
  dir: 'asc' | 'desc',
  fallbackA: number,
  fallbackB: number,
) {
  if (a == null && b == null) return fallbackA - fallbackB;
  if (a == null) return 1;
  if (b == null) return -1;
  return dir === 'asc' ? a - b : b - a;
}

function sourceToneClass(tone: SourceOption['tone'], active: boolean) {
  if (!active) {
    return 'border-border-primary/55 bg-bg-secondary/45 text-text-secondary hover:border-brand-blue/45 hover:text-text-primary';
  }
  return {
    cyan: 'border-brand-blue-light/60 bg-brand-blue/16 text-brand-blue-light shadow-[inset_0_-2px_0_rgba(86,216,255,0.9)]',
    blue: 'border-brand-blue/70 bg-brand-blue/14 text-brand-blue-light shadow-[inset_0_-2px_0_rgba(47,125,255,0.9)]',
    green: 'border-success/55 bg-success/10 text-success shadow-[inset_0_-2px_0_rgba(40,224,112,0.85)]',
    amber: 'border-warning/55 bg-warning/10 text-warning shadow-[inset_0_-2px_0_rgba(255,176,32,0.85)]',
    rose: 'border-danger/55 bg-danger/10 text-danger shadow-[inset_0_-2px_0_rgba(255,79,109,0.85)]',
  }[tone];
}

function SourceTabs({
  activeSource,
  counts,
  onSelect,
}: {
  activeSource: SourceId;
  counts: Record<SourceId, number | null>;
  onSelect: (source: SourceId) => void;
}) {
  return (
    <div className="flex min-w-0 flex-nowrap items-center gap-1 overflow-x-auto custom-scrollbar">
      {SOURCE_OPTIONS.map((option) => {
        const Icon = option.icon;
        const active = activeSource === option.id;
        const count = counts[option.id];
        return (
          <button
            key={option.id}
            type="button"
            onClick={() => onSelect(option.id)}
            className={`group h-8 min-w-[104px] rounded-md border px-2 text-left transition ${sourceToneClass(option.tone, active)}`}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-1.5">
                <Icon className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate text-xs font-black">{option.label}</span>
              </span>
              <span className="font-mono text-[11px] font-black">{count == null ? '-' : count}</span>
            </span>
            <span className="block truncate text-[9px] leading-tight text-text-tertiary">{option.hint}</span>
          </button>
        );
      })}
    </div>
  );
}

function SortHeader({
  column,
  sortState,
  onSort,
}: {
  column: TableColumn;
  sortState: SortState | null;
  onSort: (key: SortKey) => void;
}) {
  const active = column.sortKey && sortState?.key === column.sortKey;
  const content = (
    <>
      <span>{column.label}</span>
      {column.sortKey ? (
        <span className={`ml-1 font-mono text-[9px] ${active ? 'text-brand-blue-light' : 'text-text-disabled'}`}>
          {active ? (sortState?.dir === 'desc' ? '↓' : '↑') : '↕'}
        </span>
      ) : null}
    </>
  );

  if (!column.sortKey) {
    return <span className="inline-flex items-center">{content}</span>;
  }

  return (
    <button
      type="button"
      onClick={() => onSort(column.sortKey as SortKey)}
      className="inline-flex items-center rounded px-1 py-0.5 transition hover:bg-brand-blue/12 hover:text-brand-blue-light"
    >
      {content}
    </button>
  );
}

function formatPctCell(value: unknown) {
  const parsed = value === null || value === undefined || value === ''
    ? null
    : toNumber(value, Number.NaN);
  const tone = parsed == null ? 'text-text-tertiary' : parsed >= 0 ? 'text-danger' : 'text-success';
  const label = parsed == null || !Number.isFinite(parsed)
    ? '-'
    : `${parsed >= 0 ? '+' : ''}${parsed.toFixed(2)}%`;
  return <span className={`font-mono font-black ${tone}`}>{label}</span>;
}

function formatRpsValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '-';
  const parsed = toNumber(value, Number.NaN);
  return Number.isFinite(parsed) ? Math.round(parsed).toString() : '-';
}

function stickyColumnClass(column: TableColumn, selected = false) {
  if (!column.stickyLeft) return '';
  const background = selected ? 'bg-[#102844]' : 'bg-[#07111f] group-hover:bg-[#0a1b2b]';
  const edge = column.stickyShadow
    ? 'border-r border-border-secondary shadow-[8px_0_14px_rgba(0,5,14,0.65)]'
    : '';
  return `sticky ${column.stickyLeft} z-[2] ${background} ${edge}`;
}

function stickyHeaderClass(column: TableColumn) {
  if (!column.stickyLeft) return '';
  const edge = column.stickyShadow
    ? 'border-r border-border-secondary shadow-[8px_0_14px_rgba(0,5,14,0.65)]'
    : '';
  return `sticky ${column.stickyLeft} z-20 bg-[#07111f] ${edge}`;
}

function StockDetailPanel({
  symbol,
  tradeDate,
  onSelectSymbol,
}: {
  symbol: string;
  tradeDate: string;
  onSelectSymbol: (symbol: string) => void;
}) {
  const [unifiedHints, setUnifiedHints] = useState<DailyHintsContext>();
  const setSelectedStock = useGlobalStore(state => state.setSelectedStock);
  useEffect(() => { if (symbol) setSelectedStock(symbol); }, [symbol, setSelectedStock]);
  const selectSymbol = (next: string) => { setSelectedStock(next); onSelectSymbol(next); };
  const dailyHintsPanel = <AllWatchlistDailyHints date={tradeDate} onSelectSymbol={selectSymbol} onContext={setUnifiedHints} />;
  if (!symbol) return <div className="p-4 text-xs text-text-secondary">请选择一只股票</div>;
  return <SharedStockDetailPanel symbol={symbol} selectedDate={tradeDate} showDetailLink
    onSelectSymbol={selectSymbol} dailyHintsPanel={dailyHintsPanel} sidebarScopeKey="__all_watchlist__"
    unifiedHints={unifiedHints?.date === tradeDate ? unifiedHints : undefined} />;
}

export default function StrategyPage() {
  const { globalDate, setGlobalDate, reportOpen } = useGlobalStore();
  const queryClient = useQueryClient();
  const restoredStrategy = useGlobalStore.getState().stockSidebarUi.strategy;
  const [activeSource, setActiveSource] = useState<SourceId>(restoredStrategy && ['mrgc','alpha','pocket','breakout','exhaustion'].includes(restoredStrategy.source) ? restoredStrategy.source as SourceId : 'mrgc');
  const [sortState, setSortState] = useState<SortState | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState(restoredStrategy?.symbol || '');
  const assistantSelectionRef = useRef<{ symbol: string; date: string; source: SourceId } | null>(null);
  const [detailOpen, setDetailOpen] = useState(restoredStrategy?.detailOpen || false);
  useEffect(() => { useGlobalStore.getState().setStockSidebarUi({ strategy: { source: activeSource, symbol: selectedSymbol, detailOpen } }); }, [activeSource, selectedSymbol, detailOpen]);
  useEffect(() => {
    if (!reportOpen) return;
    let active = true;
    queueMicrotask(() => { if (active) setDetailOpen(true); });
    return () => { active = false; };
  }, [reportOpen]);
  const [splitPercent, setSplitPercent] = useState(34);
  const [splitReady, setSplitReady] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [mrgcRebuildRun, setMrgcRebuildRun] = useState<{ runId: string; tradeDate: string } | null>(null);
  const [mrgcRebuildMessage, setMrgcRebuildMessage] = useState('');

  const splitAreaRef = useRef<HTMLDivElement | null>(null);
  const splitPercentRef = useRef(splitPercent);
  const listRef = useRef<HTMLDivElement | null>(null);

  const datesQuery = useQuery({
    queryKey: ['strategy-dates'],
    queryFn: () => quantApi.getDates(),
    staleTime: 5 * 60 * 1000,
  });

  const availableDates = useMemo(() => rows<string>(datesQuery.data?.dates), [datesQuery.data]);
  const latestDate = text(datesQuery.data?.latest, availableDates[0] || '');
  const effectiveDate = resolveStrategyDate(globalDate, availableDates);
  const dateOptions = useMemo(
    () => strategyDateOptions(globalDate, availableDates),
    [availableDates, globalDate],
  );

  useEffect(() => {
    if (!globalDate && latestDate) {
      queueMicrotask(() => setGlobalDate(latestDate));
    }
  }, [globalDate, latestDate, setGlobalDate]);

  const mrgcQuery = useQuery({
    queryKey: ['strategy-source-mrgc', effectiveDate],
    queryFn: () =>
      quantApi.getStrategyResults({
        tradeDate: effectiveDate,
        strategyName: 'mrgc_v1',
        limit: 1000,
      }),
    enabled: Boolean(effectiveDate) && activeSource === 'mrgc',
  });

  const mrgcRebuildMutation = useMutation({
    mutationFn: (tradeDate: string) =>
      quantApi.runDataTask({
        task_id: 'mrgc_recalculation',
        params: { trade_date: tradeDate },
      }),
    onSuccess: (data, tradeDate) => {
      const payload = data as { run_id?: unknown };
      const runId = text(payload.run_id, '');
      if (runId) {
        setMrgcRebuildRun({ runId, tradeDate });
        setMrgcRebuildMessage(`${tradeDate} MRGC 重算任务已排队`);
      }
    },
    onError: () => {
      setMrgcRebuildMessage('MRGC 重算任务提交失败');
    },
  });

  const mrgcTaskQuery = useQuery({
    queryKey: ['mrgc-rebuild-task', mrgcRebuildRun?.runId],
    queryFn: () => quantApi.getDataTasks(30),
    enabled: Boolean(mrgcRebuildRun?.runId),
    refetchInterval: mrgcRebuildRun ? 1500 : false,
  });

  const mrgcRebuildTask = useMemo(
    () =>
      rows<Row>(mrgcTaskQuery.data?.task_runs).find(
        (task) => text(task.run_id, '') === mrgcRebuildRun?.runId,
      ),
    [mrgcRebuildRun?.runId, mrgcTaskQuery.data],
  );

  useEffect(() => {
    if (!mrgcRebuildRun || !mrgcRebuildTask) {
      return;
    }
    const status = text(mrgcRebuildTask.status, '');
    if (!['success', 'failed'].includes(status)) {
      return;
    }

    const tradeDate = mrgcRebuildRun.tradeDate;
    void queryClient.invalidateQueries({ queryKey: ['strategy-source-mrgc', tradeDate] });
    queueMicrotask(() => {
      setMrgcRebuildMessage(
        status === 'success'
          ? `${tradeDate} MRGC 重算完成`
          : `${tradeDate} MRGC 重算失败：${text(mrgcRebuildTask.error, '请查看任务日志')}`,
      );
      setMrgcRebuildRun(null);
    });
  }, [mrgcRebuildRun, mrgcRebuildTask, queryClient]);

  const alphaQuery = useQuery({
    queryKey: ['strategy-source-alpha', effectiveDate],
    queryFn: () =>
      quantApi.getAlphaOpportunities({
        tradeDate: effectiveDate,
        rpsPeriod: 20,
        limit: 1000,
      }),
    enabled: Boolean(effectiveDate) && activeSource === 'alpha',
  });

  const pocketQuery = useQuery({
    queryKey: ['strategy-source-signal', effectiveDate, 'pocket_pivot'],
    queryFn: () => quantApi.getSignals({ tradeDate: effectiveDate, signalType: 'pocket_pivot', limit: 1000 }),
    enabled: Boolean(effectiveDate) && activeSource === 'pocket',
  });

  const breakoutQuery = useQuery({
    queryKey: ['strategy-source-signal', effectiveDate, 'breakout'],
    queryFn: () => quantApi.getSignals({ tradeDate: effectiveDate, signalType: 'breakout', limit: 1000 }),
    enabled: Boolean(effectiveDate) && activeSource === 'breakout',
  });

  const exhaustionQuery = useQuery({
    queryKey: ['strategy-source-signal', effectiveDate, 'supply_exhaustion'],
    queryFn: () => quantApi.getSignals({ tradeDate: effectiveDate, signalType: 'supply_exhaustion', limit: 1000 }),
    enabled: Boolean(effectiveDate) && activeSource === 'exhaustion',
  });

  const sourceRows = useMemo<Record<SourceId, Row[]>>(
    () => ({
      mrgc: rows<Row>(mrgcQuery.data?.rows),
      alpha: rows<Row>(alphaQuery.data?.rows),
      pocket: rows<Row>(pocketQuery.data?.rows),
      breakout: rows<Row>(breakoutQuery.data?.rows),
      exhaustion: rows<Row>(exhaustionQuery.data?.rows),
    }),
    [alphaQuery.data, breakoutQuery.data, exhaustionQuery.data, mrgcQuery.data, pocketQuery.data],
  );

  const sourceCounts = useMemo<Record<SourceId, number | null>>(
    () => ({
      mrgc: mrgcQuery.data ? sourceRows.mrgc.length : null,
      alpha: alphaQuery.data ? sourceRows.alpha.length : null,
      pocket: pocketQuery.data ? sourceRows.pocket.length : null,
      breakout: breakoutQuery.data ? sourceRows.breakout.length : null,
      exhaustion: exhaustionQuery.data ? sourceRows.exhaustion.length : null,
    }),
    [alphaQuery.data, breakoutQuery.data, exhaustionQuery.data, mrgcQuery.data, pocketQuery.data, sourceRows],
  );

  const isLoading =
    (activeSource === 'mrgc' && mrgcQuery.isLoading) ||
    (activeSource === 'alpha' && alphaQuery.isLoading) ||
    (activeSource === 'pocket' && pocketQuery.isLoading) ||
    (activeSource === 'breakout' && breakoutQuery.isLoading) ||
    (activeSource === 'exhaustion' && exhaustionQuery.isLoading);

  const currentRows = useMemo(() => {
    const rowsWithIndex = sourceRows[activeSource].map((row, index) => ({ row, index }));

    if (sortState) {
      return rowsWithIndex
        .sort((a, b) =>
          compareNullableNumber(
            numericValue(a.row, sortState.key),
            numericValue(b.row, sortState.key),
            sortState.dir,
            a.index,
            b.index,
          ),
        )
        .map((item) => item.row);
    }

    if (activeSource === 'alpha') {
      return rowsWithIndex
        .sort((a, b) =>
          compareNullableNumber(numericValue(a.row, 'rps_20'), numericValue(b.row, 'rps_20'), 'desc', a.index, b.index),
        )
        .map((item) => item.row);
    }

    if (SIGNAL_SOURCE_TYPE[activeSource]) {
      return rowsWithIndex
        .sort((a, b) =>
          compareNullableNumber(
            numericValue(a.row, 'signal_score'),
            numericValue(b.row, 'signal_score'),
            'desc',
            a.index,
            b.index,
          ),
        )
        .map((item) => item.row);
    }

    return rowsWithIndex.map((item) => item.row);
  }, [activeSource, sortState, sourceRows]);

  const selectedIndex = useMemo(
    () => currentRows.findIndex((row) => rowSymbol(row) === selectedSymbol),
    [currentRows, selectedSymbol],
  );
  const selectedRow = selectedIndex >= 0 ? currentRows[selectedIndex] : undefined;
  const activeOption = SOURCE_OPTIONS.find((option) => option.id === activeSource) ?? SOURCE_OPTIONS[0];

  const tableColumns = useMemo<TableColumn[]>(
    () => [
      {
        key: 'symbol',
        label: '代码',
        className: 'w-[68px] min-w-[68px] max-w-[68px]',
        stickyLeft: 'left-0',
        render: (row) => <span className="font-mono text-brand-blue-light">{rowSymbol(row)}</span>,
      },
      {
        key: 'name',
        label: '名称',
        className: 'w-[92px] min-w-[92px] max-w-[92px]',
        stickyLeft: 'left-[68px]',
        render: (row) => <span className="block truncate">{text(row.name, '-')}</span>,
      },
      {
        key: 'sector',
        label: '行业',
        className: 'w-[112px] min-w-[112px] max-w-[112px]',
        stickyLeft: 'left-[160px]',
        stickyShadow: true,
        render: (row) => <span className="block truncate">{text(row.sector_name, '-')}</span>,
      },
      {
        key: 'close',
        label: '股价',
        sortKey: 'close',
        align: 'right',
        className: 'w-[66px] min-w-[66px]',
        render: (row) => (
          <span className="font-mono text-text-primary" title="所选交易日收盘价（元）">
            {numericValue(row, 'close') == null ? '-' : num(row.close, 2)}
          </span>
        ),
      },
      {
        key: 'pct',
        label: '涨跌',
        sortKey: 'pct_chg',
        align: 'right',
        className: 'w-[66px] min-w-[66px]',
        render: (row) => formatPctCell(row.pct_chg),
      },
      ...RPS_SORT_KEYS.map<TableColumn>((key) => ({
        key,
        label: key.replace('rps_', 'R'),
        sortKey: key,
        align: 'right',
        className: 'w-[48px] min-w-[48px]',
        render: (row) => <span className="font-mono text-text-primary">{formatRpsValue(row[key])}</span>,
      })),
      {
        key: 'state',
        label: 'S',
        align: 'center',
        className: 'w-[40px] min-w-[40px]',
        render: (row) => <StageBadge value={text(row.lifecycle_state, '-')} />,
      },
    ],
    [],
  );

  const handleSort = useCallback((key: SortKey) => {
    setSortState((current) => {
      if (!current || current.key !== key) {
        return { key, dir: 'desc' };
      }
      return { key, dir: current.dir === 'desc' ? 'asc' : 'desc' };
    });
  }, []);

  const selectSource = useCallback((source: SourceId) => {
    assistantSelectionRef.current = null;
    setActiveSource(source);
    setSortState(null);
    setSelectedSymbol('');
    setDetailOpen(false);
  }, []);

  const selectRowAt = useCallback(
    (index: number) => {
      if (!currentRows.length) {
        return;
      }

      const nextIndex = Math.min(currentRows.length - 1, Math.max(0, index));
      const nextSymbol = rowSymbol(currentRows[nextIndex]);
      if (nextSymbol) {
        assistantSelectionRef.current = null;
        setSelectedSymbol(nextSymbol);
        setDetailOpen(true);
      }
    },
    [currentRows],
  );

  const handleListNavigation = useCallback(
    (event: KeyboardEvent) => {
      if (document.activeElement !== listRef.current) {
        return;
      }
      if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) {
        return;
      }

      event.preventDefault();

      if (event.key === 'Enter') {
        if (selectedSymbol) {
          setDetailOpen(true);
        } else {
          selectRowAt(0);
        }
        return;
      }

      const fallbackIndex = selectedIndex >= 0 ? selectedIndex : 0;
      selectRowAt(event.key === 'ArrowDown' ? fallbackIndex + 1 : fallbackIndex - 1);
    },
    [selectRowAt, selectedIndex, selectedSymbol],
  );

  useEffect(() => {
    document.addEventListener('keydown', handleListNavigation);
    return () => document.removeEventListener('keydown', handleListNavigation);
  }, [handleListNavigation]);

  const handleRefresh = useCallback(() => {
    if (activeSource === 'mrgc') void mrgcQuery.refetch();
    if (activeSource === 'alpha') void alphaQuery.refetch();
    if (activeSource === 'pocket') void pocketQuery.refetch();
    if (activeSource === 'breakout') void breakoutQuery.refetch();
    if (activeSource === 'exhaustion') void exhaustionQuery.refetch();
  }, [activeSource, alphaQuery, breakoutQuery, exhaustionQuery, mrgcQuery, pocketQuery]);

  useEffect(() => {
    if (!detailOpen) {
      return;
    }

    const assistantSelection = assistantSelectionRef.current;
    if (assistantSelection?.symbol === selectedSymbol && assistantSelection.date === effectiveDate && assistantSelection.source === activeSource) return;

    if (!currentRows.length) {
      if (selectedSymbol) {
        queueMicrotask(() => setSelectedSymbol(''));
      }
      return;
    }

    if (currentRows.some((row) => rowSymbol(row) === selectedSymbol)) {
      return;
    }

    const nextSymbol = rowSymbol(currentRows[0]);
    if (nextSymbol) {
      queueMicrotask(() => setSelectedSymbol(nextSymbol));
    }
  }, [currentRows, detailOpen, selectedSymbol, effectiveDate, activeSource]);

  useEffect(() => {
    splitPercentRef.current = splitPercent;
  }, [splitPercent]);

  useEffect(() => {
    const stored = Number(window.localStorage.getItem(SPLIT_STORAGE_KEY));
    queueMicrotask(() => {
      if (Number.isFinite(stored)) {
        setSplitPercent(clampSplit(stored));
      }
      setSplitReady(true);
    });
  }, []);

  useEffect(() => {
    if (!splitReady) {
      return;
    }
    window.localStorage.setItem(SPLIT_STORAGE_KEY, String(Math.round(splitPercent)));
  }, [splitPercent, splitReady]);

  useEffect(() => {
    if (!selectedSymbol || !listRef.current) {
      return;
    }

    const escapedSymbol = selectedSymbol.replace(/"/g, '\\"');
    const rowElement = listRef.current.querySelector(`[data-symbol="${escapedSymbol}"]`);
    rowElement?.scrollIntoView({ block: 'nearest' });
  }, [currentRows, selectedSymbol]);

  useEffect(() => {
    if (!isDragging) {
      return;
    }

    const handleMove = (event: PointerEvent) => {
      const rect = splitAreaRef.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0) {
        return;
      }

      const next = clampSplit(((event.clientX - rect.left) / rect.width) * 100);
      splitPercentRef.current = next;
      setSplitPercent(next);
    };

    const handleUp = () => {
      setIsDragging(false);
      window.localStorage.setItem(SPLIT_STORAGE_KEY, String(Math.round(splitPercentRef.current)));
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);

    return () => {
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isDragging]);

  const listStyle: CSSProperties | undefined = detailOpen
    ? { flexBasis: `${splitPercent}%`, maxWidth: `${splitPercent}%` }
    : { flexBasis: '100%', maxWidth: '100%' };

  const mrgcCalculation = (mrgcQuery.data?.calculation ?? {}) as Row;
  const mrgcCalculated = Boolean(mrgcCalculation.calculated);
  const mrgcRebuildActive =
    mrgcRebuildMutation.isPending || mrgcRebuildRun?.tradeDate === effectiveDate;
  const mrgcNeedsRecalculation =
    activeSource === 'mrgc' && !isLoading && !mrgcCalculated;
  const emptyText =
    activeSource === 'mrgc'
      ? mrgcCalculated
        ? 'MRGC 筛选已完成，本日没有命中结果。'
        : text(mrgcCalculation.error_message, '当前日期尚未运行 MRGC 筛选。')
      : activeSource === 'alpha'
      ? '当前日期暂无 S4/S5 Alpha 机会，请检查 stock_lifecycle_state 是否生成。'
      : SIGNAL_SOURCE_TYPE[activeSource]
        ? `当前日期暂无${activeOption.label}。`
        : '当前日期暂无策略结果。';

  return (
    <AppShell>
      <div className="evo-command-space flex min-h-0 flex-col lg:h-[calc(100dvh-var(--evo-nav-height,64px)-12px)] lg:!min-h-0">
        <section className="evo-panel mb-1.5 shrink-0 px-2.5 py-1.5">
          <div className="flex min-w-0 items-center gap-2">
            <div className="hidden shrink-0 items-baseline gap-2 xl:flex">
              <h1 className="text-[16px] font-black leading-none text-white">
                <span className="font-mono">04</span> Strategy
              </h1>
              <span className="text-[11px] text-text-tertiary">策略选股</span>
            </div>
            <div className="min-w-0 flex-1">
              <SourceTabs activeSource={activeSource} counts={sourceCounts} onSelect={selectSource} />
            </div>
            <select
              value={effectiveDate || ''}
              onChange={(event) => setGlobalDate(event.target.value)}
              className="h-8 min-w-[130px] rounded-md border border-border-primary bg-[#0b1220] px-2.5 font-mono text-xs text-text-primary outline-none focus:border-brand-blue/70"
            >
              {dateOptions.map((date) => (
                <option key={date} value={date} className="bg-bg-secondary">
                  {date}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => setDetailOpen((value) => !value)}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border-primary/70 bg-bg-primary/55 px-2.5 text-xs text-text-secondary transition hover:border-brand-blue/50 hover:text-brand-blue-light"
            >
              {detailOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
              <span className="hidden sm:inline">{detailOpen ? '收起详情' : '展开详情'}</span>
            </button>
            <button
              type="button"
              onClick={handleRefresh}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border-primary/70 bg-bg-primary/55 text-text-secondary transition hover:border-brand-blue/50 hover:text-brand-blue-light"
              aria-label="刷新策略页面数据"
            >
              <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </section>

      <div
        ref={splitAreaRef}
        className="flex min-h-0 flex-col gap-1.5 lg:flex-1 lg:flex-row lg:items-stretch"
      >
        <section
          className="evo-panel flex min-h-[440px] flex-col overflow-hidden border-border-secondary/80 lg:h-full lg:min-w-[432px]"
          style={listStyle}
        >
          <div className="flex min-h-[36px] items-center justify-between gap-2 border-b border-border-secondary bg-bg-secondary/60 px-2.5 py-1.5">
            <div className="flex min-w-0 items-center gap-2">
              <activeOption.icon className="h-4 w-4 shrink-0 text-brand-blue-light" />
              <div className="min-w-0">
                <div className="truncate text-sm font-black text-text-primary">{activeOption.label} 股池</div>
                <div className="truncate text-[10px] text-text-tertiary">
                  ↑ / ↓ 切换，表头排序，横向查看指标
                </div>
              </div>
            </div>
            <div className="font-mono text-xs text-text-tertiary">
              {selectedIndex >= 0 ? `${selectedIndex + 1} / ${currentRows.length}` : `${currentRows.length}`}
            </div>
          </div>

          <div
            ref={listRef}
            tabIndex={0}
            onClick={() => listRef.current?.focus()}
            className="min-h-0 flex-1 overflow-auto outline-none custom-scrollbar focus-visible:ring-1 focus-visible:ring-brand-blue-light/70"
          >
            <table className="w-[732px] table-fixed border-separate border-spacing-0 text-left text-[11px]">
              <thead className="sticky top-0 z-10 border-b border-border-secondary bg-[#07111f]/98 text-[10px] text-text-tertiary backdrop-blur">
                <tr>
                  {tableColumns.map((column) => (
                    <th
                      key={column.key}
                      className={`border-b border-border-secondary px-1.5 py-1 font-bold ${
                        column.align === 'right'
                          ? 'text-right'
                          : column.align === 'center'
                            ? 'text-center'
                            : 'text-left'
                      } ${column.className ?? ''} ${stickyHeaderClass(column)}`}
                    >
                      <SortHeader column={column} sortState={sortState} onSort={handleSort} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr>
                    <td colSpan={tableColumns.length} className="px-3 py-10 text-center text-text-tertiary">
                      正在读取数据库结果...
                    </td>
                  </tr>
                ) : currentRows.length ? (
                  currentRows.map((row, index) => {
                    const symbol = rowSymbol(row);
                    const isSelected = symbol === selectedSymbol;
                    return (
                      <tr
                        key={`${activeSource}-${symbol}-${index}`}
                        data-symbol={symbol}
                        onClick={() => {
                          assistantSelectionRef.current = null;
                          setSelectedSymbol(symbol);
                          setDetailOpen(true);
                        }}
                        className={`group cursor-pointer transition ${
                          isSelected
                            ? 'bg-brand-blue/16 text-text-primary shadow-[inset_2px_0_0_rgba(86,216,255,0.95)]'
                            : 'text-text-secondary hover:bg-bg-hover/45 hover:text-text-primary'
                        }`}
                      >
                        {tableColumns.map((column) => (
                          <td
                            key={column.key}
                            className={`border-b border-border-secondary/70 px-1.5 py-[3px] leading-5 ${
                              column.align === 'right'
                                ? 'text-right'
                                : column.align === 'center'
                                  ? 'text-center'
                                  : ''
                            } ${column.className ?? ''} ${stickyColumnClass(column, isSelected)}`}
                          >
                            {column.render(row)}
                          </td>
                        ))}
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={tableColumns.length} className="px-3 py-10 text-center text-text-tertiary">
                      <div className="flex flex-col items-center gap-3">
                        <span>{emptyText}</span>
                        {mrgcNeedsRecalculation ? (
                          <button
                            type="button"
                            disabled={mrgcRebuildActive || !effectiveDate}
                            onClick={() => effectiveDate && mrgcRebuildMutation.mutate(effectiveDate)}
                            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-brand-blue/55 bg-brand-blue/12 px-3 text-xs font-bold text-brand-blue-light transition hover:border-brand-blue-light/75 hover:bg-brand-blue/20 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <RefreshCw className={`h-3.5 w-3.5 ${mrgcRebuildActive ? 'animate-spin' : ''}`} />
                            {mrgcRebuildActive ? '正在重新筛选' : '重新筛选该日期'}
                          </button>
                        ) : null}
                        {activeSource === 'mrgc' && mrgcRebuildMessage ? (
                          <span className="text-[10px] text-text-secondary">{mrgcRebuildMessage}</span>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {detailOpen ? (
          <>
            <button
              type="button"
              aria-label="拖拽调整左右分栏宽度"
              onPointerDown={(event) => {
                event.currentTarget.setPointerCapture(event.pointerId);
                setIsDragging(true);
              }}
              className="hidden w-2 cursor-col-resize items-center justify-center rounded-md border border-transparent text-text-tertiary transition hover:border-brand-blue/40 hover:bg-brand-blue/10 hover:text-brand-blue-light lg:flex"
            >
              <GripVertical className="h-5 w-5" />
            </button>

            <aside className="min-h-[520px] min-w-0 flex-1 lg:h-full lg:min-h-0">
              <StockDetailPanel
                symbol={selectedSymbol || rowSymbol(selectedRow ?? {})}
                tradeDate={effectiveDate}
                onSelectSymbol={(symbol) => { assistantSelectionRef.current = { symbol, date: effectiveDate, source: activeSource }; setSelectedSymbol(symbol); }}
              />
            </aside>
          </>
        ) : null}
      </div>
      </div>
    </AppShell>
  );
}
