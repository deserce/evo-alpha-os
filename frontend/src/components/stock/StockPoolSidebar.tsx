'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import { useParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { watchlistApi } from '@/lib/api';

const TABS = [
  { id: 'strategy', label: '策略池' },
  { id: 'watchlist', label: '自选股' },
 , 
];

export default function StockPoolSidebar() {
  const [activeTab, setActiveTab] = useState('strategy'); 
  const params = useParams();
  const currentSymbol = params.symbol as string;

  const [selectedStrategy, setSelectedStrategy] = useState('all');
  const [selectedDate, setSelectedDate] = useState('all');

  const { data: meta } = useQuery({
    queryKey: ['strategyMeta'],
    queryFn: watchlistApi.getStrategyMeta,
  });

  useEffect(() => {
    if (meta?.dates?.length > 0) setSelectedDate(meta.dates[0]); 
  }, [meta]);

  const { data: watchlistData } = useQuery({
    queryKey: ['watchlist'],
    queryFn: watchlistApi.getAll,
    enabled: activeTab === 'watchlist',
  });

  const { data: strategyData } = useQuery({
    queryKey: ['strategyPool', selectedStrategy, selectedDate],
    queryFn: () => watchlistApi.getStrategyPool(selectedStrategy, selectedDate),
    enabled: activeTab === 'strategy',
  });

  let stocks = activeTab === 'watchlist' ? (watchlistData || []) : (strategyData || []);

  return (
    <div className="h-full flex flex-col bg-[#111417] border-r border-[#1e2226]">
      {/* 顶部 Tab - Webull 标准高度 */}
      <div className="flex border-b border-[#1e2226] bg-[#080a0d] shrink-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={clsx(
              "flex-1 py-3 text-[11px] font-bold transition-all",
              activeTab === tab.id
                ? "text-white border-b-2 border-[#1861ff] bg-[#1861ff]/5"
                : "text-[#8a8d91] border-b-2 border-transparent hover:text-white"
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* 筛选栏 - 极窄紧凑 */}
      {activeTab === 'strategy' && (
        <div className="flex gap-1 p-1.5 bg-[#111417] border-b border-[#1e2226] shrink-0">
          <select 
            className="flex-1 bg-[#1e2226] text-[10px] text-[#d1d1d1] border border-transparent rounded-sm px-1 py-1 focus:outline-none focus:border-[#1861ff] cursor-pointer"
            value={selectedStrategy}
            onChange={(e) => setSelectedStrategy(e.target.value)}
          >
            <option value="all">全策略</option>
            {meta?.strategies?.map((s: string) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select 
            className="flex-1 bg-[#1e2226] text-[10px] text-[#d1d1d1] border border-transparent rounded-sm px-1 py-1 focus:outline-none focus:border-[#1861ff] cursor-pointer"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
          >
            <option value="all">全日期</option>
            {meta?.dates?.map((d: string) => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
      )}

      {/* 列表区域 - 仿微牛自选股列表 */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {stocks.map((stock: any) => (
          <Link
            key={stock.code + stock.strategy_label + stock.date} 
            href={`/stock/${stock.code}`}
            className={clsx(
              "group flex flex-col px-3 py-2 border-b border-[#1e2226] transition-all",
              currentSymbol === stock.code
                ? "bg-[#1861ff]/15 relative"
                : "bg-transparent hover:bg-[#1c2127]"
            )}
          >
            {currentSymbol === stock.code && <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-[#1861ff]" />}
            
            <div className="flex justify-between items-center mb-0.5">
              <span className={clsx(
                "text-[12px] font-bold truncate",
                currentSymbol === stock.code ? "text-white" : "text-[#d1d1d1] group-hover:text-white"
              )}>
                {stock.name}
              </span>
              {stock.zf !== undefined && (
                <span className={clsx(
                  "text-[11px] font-bold font-mono",
                  stock.zf > 0 ? "text-[#ff4d4d]" : "text-[#00c087]"
                )}>
                  {stock.zf > 0 ? '+' : ''}{stock.zf}%
                </span>
              )}
            </div>

            <div className="flex justify-between items-center">
              <span className="text-[10px] text-[#8a8d91] font-mono tracking-tighter">{stock.code}</span>
              {stock.rps > 0 && (
                <div className="text-[9px] text-[#ffb11a] font-bold bg-[#ffb11a]/10 px-1 rounded-sm border border-[#ffb11a]/20 font-mono">
                  RPS {stock.rps}
                </div>
              )}
            </div>
            
            {activeTab === 'strategy' && stock.strategy_label && (
              <div className="mt-1.5 text-[9px] text-[#a78bfa] truncate opacity-80 uppercase tracking-tighter font-bold">
                {stock.strategy_label}
              </div>
            )}
          </Link>
        ))}
        
        {stocks.length === 0 && (
          <div className="flex items-center justify-center h-20 text-[#8a8d91] text-[10px] font-mono opacity-50 uppercase">NO DATA</div>
        )}
      </div>
    </div>
  );
}