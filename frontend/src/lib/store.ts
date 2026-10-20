import { create } from 'zustand';
import { DEFAULT_STOCK_SIDEBAR_UI, patchStockSidebarUi, boundedSidebarRecord } from './stockSidebarState';
import type { StockSidebarUi } from './stockSidebarState';
import type { ReportFilter, ReportGroup } from './mrgcReportSelection';
import type { ReportStock } from '../components/stock/MrgcDailyReportPanel';

export interface ReportPanelUi {
  date: string;
  view: 'chat' | 'report' | 'email';
  group: ReportGroup;
  filter: ReportFilter;
  sector: string;
  question: string;
  answer: { hintSymbols?: string[]; hintFilter?: StockSidebarUi['hintsFilter']; question: string; title: string; stocks: ReportStock[];
    group: ReportGroup; allStocks: ReportStock[]; total: number; sector: boolean; unsupported: boolean } | null;
}

export interface ReportLinkedStock {
  symbol: string;
  name: string;
  industry: string;
  close: number | null;
  pct_chg: number | null;
  rps_20: number | null;
  note: string;
}

interface GlobalState {
  stockSidebarUi: StockSidebarUi;
  setStockSidebarUi: (patch: Partial<StockSidebarUi>) => void;
  setSidebarScroll: (key: string, top: number) => void;
  setSidebarExpanded: (key: string, expanded: boolean) => void;
  reportPanelUi: ReportPanelUi;
  setReportPanelUi: (patch: Partial<Omit<ReportPanelUi, 'date'>>) => void;
  syncReportPanelDate: (date: string) => void;
  reportOpen: boolean;
  setReportOpen: (open: boolean) => void;
  reportContext: { date: string; stocks: ReportLinkedStock[]; label: string } | null;
  setReportContext: (context: GlobalState['reportContext']) => void;
  reportFocus: { date: string; symbol: string } | null;
  setReportFocus: (focus: GlobalState['reportFocus']) => void;
  // 日期相关
  globalDate: string;
  setGlobalDate: (date: string) => void;

  // 桌面侧边栏缩放
  isSidebarExpanded: boolean;
  toggleSidebar: () => void;

  // 跨页业务状态
  selectedSector: string;
  setSelectedSector: (sector: string) => void;

  selectedStock: string;
  setSelectedStock: (symbol: string) => void;

  // 全局个股抽屉
  drawerSymbol: string;
  drawerOpen: boolean;
  openDrawer: (symbol: string) => void;
  closeDrawer: () => void;
}

export const useGlobalStore = create<GlobalState>((set) => ({
  stockSidebarUi: DEFAULT_STOCK_SIDEBAR_UI,
  setStockSidebarUi: (patch) => set((state) => {
    const stockSidebarUi = patchStockSidebarUi(state.stockSidebarUi, patch);
    return { stockSidebarUi, reportOpen: stockSidebarUi.tab === 'ai' };
  }),
  setSidebarScroll: (key, top) => set((state) => !key || !Number.isFinite(top) ? state : ({
    stockSidebarUi: { ...state.stockSidebarUi, scrollPositions: boundedSidebarRecord(state.stockSidebarUi.scrollPositions, key, Math.min(100000, Math.max(0, top))) },
  })),
  setSidebarExpanded: (key, expanded) => set((state) => !key ? state : ({
    stockSidebarUi: { ...state.stockSidebarUi, expanded: boundedSidebarRecord(state.stockSidebarUi.expanded, key, expanded) },
  })),
  reportPanelUi: { date: '', view: 'chat', group: 'all', filter: 'all', sector: '', question: '', answer: null },
  setReportPanelUi: (patch) => set((state) => ({ reportPanelUi: { ...state.reportPanelUi, ...patch } })),
  syncReportPanelDate: (date) => set((state) => !date || state.reportPanelUi.date === date ? state : ({
    reportPanelUi: { ...state.reportPanelUi, date, group: 'all', filter: 'all', sector: '', question: '', answer: null },
  })),
  reportOpen: false,
  setReportOpen: (reportOpen) => set((state) => ({
    reportOpen, stockSidebarUi: { ...state.stockSidebarUi, infoHidden: reportOpen ? false : state.stockSidebarUi.infoHidden, tab: reportOpen ? 'ai' : state.stockSidebarUi.tab === 'ai' ? 'info' : state.stockSidebarUi.tab },
  })),
  reportContext: null,
  setReportContext: (reportContext) => set({ reportContext }),
  reportFocus: null,
  setReportFocus: (reportFocus) => set({ reportFocus }),
  globalDate: '',
  setGlobalDate: (date) => set({ globalDate: date }),

  // 桌面侧边栏缩放
  isSidebarExpanded: true,
  toggleSidebar: () => set((state) => ({ isSidebarExpanded: !state.isSidebarExpanded })),

  // 跨页业务状态
  selectedSector: '',
  setSelectedSector: (sector) => set({ selectedSector: sector }),

  selectedStock: '',
  setSelectedStock: (symbol) => set({ selectedStock: symbol }),

  // 全局个股抽屉
  drawerSymbol: '',
  drawerOpen: false,
  openDrawer: (symbol) => set({ drawerSymbol: symbol, drawerOpen: true }),
  closeDrawer: () => set({ drawerOpen: false }),
}));
