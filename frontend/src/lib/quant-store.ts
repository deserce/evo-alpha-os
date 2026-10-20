import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { format } from 'date-fns';

interface QuantState {
  // 核心状态
  category: 'picker' | 'timer'; // ✅ 新增：当前分类
  selectedStrategy: string;
  timeMode: 'single' | 'range';
  targetDate: string;
  startDate: string;
  endDate: string;
  scrollTop: number;

  // Actions
  setCategory: (cat: 'picker' | 'timer') => void; // ✅ 新增
  setStrategy: (id: string) => void;
  setTimeMode: (mode: 'single' | 'range') => void;
  setDates: (dates: { target?: string; start?: string; end?: string }) => void;
  setScrollTop: (val: number) => void;
}

export const useQuantStore = create<QuantState>()(
  persist(
    (set) => ({
      category: 'picker', // 默认是选股
      selectedStrategy: '陶博士每日观察',
      timeMode: 'single',
      targetDate: format(new Date(), 'yyyy-MM-dd'),
      startDate: format(new Date(), 'yyyy-MM-01'),
      endDate: format(new Date(), 'yyyy-MM-dd'),
      scrollTop: 0,

      setCategory: (cat) => set({ category: cat }), // ✅
      setStrategy: (id) => set({ selectedStrategy: id }),
      setTimeMode: (mode) => set({ timeMode: mode }),
      setDates: (dates) => set((state) => ({
        targetDate: dates.target ?? state.targetDate,
        startDate: dates.start ?? state.startDate,
        endDate: dates.end ?? state.endDate,
      })),
      setScrollTop: (val) => set({ scrollTop: val }),
    }),
    {
      name: 'evoquant-storage',
      storage: createJSONStorage(() => localStorage),
    }
  )
);