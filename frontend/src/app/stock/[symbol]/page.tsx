'use client';

import { useParams } from 'next/navigation';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { stockApi, watchlistApi } from '@/lib/api';
import StockChart from '@/components/stock/StockChart';
import StockPoolSidebar from '@/components/stock/StockPoolSidebar';
import AIResearcherPanel from '@/components/stock/AIResearcherPanel';
import { Star, ChevronRight, Activity } from 'lucide-react'; 
import { clsx } from 'clsx';

export default function StockDetailPage() {
  const params = useParams();
  const symbol = params.symbol as string;
  const queryClient = useQueryClient();

  const { data: info } = useQuery({
    queryKey: ['stockInfo', symbol],
    queryFn: () => stockApi.getInfo(symbol),
  });

  const { data: watchlist } = useQuery({
    queryKey: ['watchlist'],
    queryFn: watchlistApi.getAll,
  });
  
  const isWatched = watchlist?.some((item: any) => item.code === symbol);

  const addMutation = useMutation({
    mutationFn: watchlistApi.add,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  });

  const removeMutation = useMutation({
    mutationFn: watchlistApi.remove,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  });

  const toggleWatchlist = () => isWatched ? removeMutation.mutate(symbol) : addMutation.mutate(symbol);

  return (
    // 移除 h-screen w-screen，改为 h-full 并减去顶部外边距
    <div className="h-full flex flex-col bg-[#080a0d] text-[#d1d1d1]">
      
      {/* 1. 股票信息 Banner (仿微牛) */}
      <header className="h-14 shrink-0 flex items-center justify-between px-4 border-b border-[#1e2226] bg-[#111417]">
        <div className="flex items-center gap-4">
          <div className="flex items-baseline gap-2">
            <h1 className="text-xl font-bold text-white font-mono tracking-tight">{symbol}</h1>
            <span className="text-sm font-bold text-[#1861ff]">{info?.name || '---'}</span>
          </div>
          
          <div className="flex gap-1.5">
            {info?.sectors?.map((s: string) => (
              <span key={s} className="px-1.5 py-0.5 bg-[#1e2226] text-[10px] text-[#8a8d91] rounded-sm border border-white/5 uppercase">
                {s}
              </span>
            ))}
          </div>

          <button 
            onClick={toggleWatchlist}
            className={clsx(
              "ml-2 transition-all p-1 rounded-sm",
              isWatched ? "text-[#ffb11a]" : "text-[#8a8d91] hover:text-white"
            )}
          >
            <Star size={18} fill={isWatched ? "currentColor" : "none"} strokeWidth={2} />
          </button>
        </div>

        {/* 实时报价区域占位 (后续可对接实时接口) */}
        <div className="flex items-center gap-6">
          <div className="flex flex-col items-end">
            <span className="text-[10px] text-[#8a8d91] font-bold uppercase tracking-tighter">Real-Time</span>
            <div className="flex items-center gap-1.5 text-[#00c087] font-mono font-bold">
              <span className="w-1.5 h-1.5 rounded-full bg-[#00c087] animate-pulse"></span>
              LIVE
            </div>
          </div>
        </div>
      </header>

      {/* 2. 主操作区：紧凑网格布局 */}
      <div className="flex-1 flex overflow-hidden p-1 gap-1">
        {/* 左侧：股票池/自选列表 (Webull 风格窄侧边) */}
        <aside className="w-56 shrink-0 h-full border border-[#1e2226] bg-[#111417]">
          <StockPoolSidebar />
        </aside>

        {/* 中间：核心图表 (占满剩余空间) */}
        <section className="flex-1 min-w-0 h-full border border-[#1e2226] bg-[#111417] relative">
          <StockChart symbol={symbol} />
        </section>

        {/* 右侧：AI 深度调研员 (固定宽度) */}
        <aside className="w-80 shrink-0 h-full border border-[#1e2226] bg-[#111417] overflow-hidden">
          <AIResearcherPanel symbol={symbol} />
        </aside>
      </div>
    </div>
  );
}