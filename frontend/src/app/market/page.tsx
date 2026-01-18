'use client';

import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api';
import { TrendingUp, TrendingDown, Activity, BarChart3, Layers, Zap } from 'lucide-react';
import { clsx } from 'clsx';
// 确保这些组件路径正确，根据之前的重构，它们应该在 market 目录下
// 如果你的组件还在 ui 目录，请自行调整路径，或者把 import 去掉先看布局
import SectorMatrix from '@/components/market/SectorMatrix'; 
// import TreemapChart from '@/components/market/TreemapChart'; // 如果有的话

export default function MarketPage() {
  // 获取市场概览数据
  const { data: overview } = useQuery({
    queryKey: ['marketOverview'],
    queryFn: marketApi.getOverview,
    refetchInterval: 30000, // 30秒刷新一次
  });

  return (
    // 🔴 核心修复：
    // 1. h-full: 占满 MainLayout 留出的空白区域
    // 2. w-full: 宽度占满
    // 3. overflow-y-auto: 开启垂直滚动条 (代替原来的 body 滚动)
    // 4. custom-scrollbar: 美化滚动条
    <div className="h-full w-full overflow-y-auto custom-scrollbar p-6 space-y-6 text-slate-200">
      
      {/* --- 顶部：Header --- */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <Layers className="text-blue-500" /> 市场指挥部
          </h1>
          <p className="text-slate-500 text-xs mt-1 font-mono tracking-wider">MARKET COMMAND CENTER</p>
        </div>
        <div className="text-right">
             <div className="text-[10px] text-slate-500 font-mono">DATA DATE</div>
             <div className="text-xl font-bold text-white font-mono">{overview?.date || '2026-01-01'}</div>
        </div>
      </div>

      {/* --- 第一排：核心指标卡片 (Grid 布局) --- */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        
        {/* 1. 市场情绪 */}
        <div className="bg-[#09090b] border border-white/10 rounded-2xl p-5 shadow-lg relative overflow-hidden group hover:border-blue-500/30 transition-all">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <Activity size={48} />
          </div>
          <div className="text-slate-500 text-xs font-bold uppercase tracking-wider mb-2">Market Sentiment</div>
          <div className="flex items-end gap-3">
             <span className="text-3xl font-bold text-red-400">{overview?.sentiment || '偏强'}</span>
             <span className="text-xs text-slate-400 mb-1 font-mono">强: {overview?.stats?.up || 3200}</span>
          </div>
          <div className="mt-3 w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
             <div className="h-full bg-gradient-to-r from-blue-500 to-red-500 w-[70%]"></div>
          </div>
        </div>

        {/* 2. 涨停家数 */}
        <div className="bg-[#09090b] border border-white/10 rounded-2xl p-5 shadow-lg relative overflow-hidden group hover:border-red-500/30 transition-all">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <Zap size={48} className="text-red-500"/>
          </div>
          <div className="text-slate-500 text-xs font-bold uppercase tracking-wider mb-2">Limit Up</div>
          <div className="flex items-end gap-3">
             <span className="text-3xl font-bold text-red-500">{overview?.stats?.limit_up || 88}</span>
             <span className="text-xs text-slate-400 mb-1 bg-red-500/10 px-1.5 rounded text-red-400">High Spirit</span>
          </div>
        </div>

        {/* 3. 平均涨幅 */}
        <div className="bg-[#09090b] border border-white/10 rounded-2xl p-5 shadow-lg relative overflow-hidden group hover:border-emerald-500/30 transition-all">
           <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <BarChart3 size={48} className="text-emerald-500"/>
          </div>
          <div className="text-slate-500 text-xs font-bold uppercase tracking-wider mb-2">Avg Chg</div>
          <div className="flex items-end gap-3">
             <span className="text-3xl font-bold text-red-400">+{overview?.stats?.avg_chg || 1.25}%</span>
          </div>
        </div>

        {/* 4. 成交量 (Mock) */}
        <div className="bg-[#09090b] border border-white/10 rounded-2xl p-5 shadow-lg relative overflow-hidden group hover:border-blue-500/30 transition-all">
          <div className="text-slate-500 text-xs font-bold uppercase tracking-wider mb-2">Volume</div>
          <div className="flex items-end gap-3">
             <span className="text-3xl font-bold text-slate-200">1.2<span className="text-sm text-slate-500 ml-1">T</span></span>
             <span className="text-xs text-red-400 mb-1">Vol +15%</span>
          </div>
        </div>
      </div>

      {/* --- 第二排：板块 RPS 矩阵 (占据主要空间) --- */}
      {/* 这里不需要 overflow-hidden，因为外层已经是 overflow-y-auto 了。
          直接让这个卡片撑开高度，用户滚页面就行。
      */}
      <div className="bg-[#09090b] border border-white/10 rounded-2xl p-1 shadow-lg min-h-[500px]">
         {/* 如果你之前把 SectorMatrix 移到了 components/market/ 下，这里就能正常显示 */}
         {/* 如果还没有这个组件，可以先注释掉下面这行，页面也能跑 */}
         <SectorMatrix />
      </div>

      {/* --- 底部占位 (防止滚动到底部太贴边) --- */}
      <div className="h-8"></div>
    </div>
  );
}