// src/lib/market-store.ts
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

interface MarketState {
  // 状态
  selectedSector: string; // 当前选中的板块名称
  scrollTop: number;      // 右侧列表的滚动位置

  // 动作
  setSelectedSector: (sector: string) => void;
  setScrollTop: (val: number) => void;
}

export const useMarketStore = create<MarketState>()(
  persist(
    (set) => ({
      selectedSector: '全市场', // 默认选中
      scrollTop: 0,

      setSelectedSector: (sector) => set({ selectedSector: sector }),
      setScrollTop: (val) => set({ scrollTop: val }),
    }),
    {
      name: 'evoalpha-market-storage', // 独立的 LocalStorage key
      storage: createJSONStorage(() => localStorage),
    }
  )
);