'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { strategyApi, watchlistApi } from '@/lib/api';
import { useQuantStore } from '@/lib/quant-store';
import { Play, Filter, Zap, Clock, TerminalSquare, ArrowRight, CheckCircle2, Loader2 } from 'lucide-react';
import { clsx } from 'clsx';

const STRATEGIES = [
  { 
    id: '陶博士每日观察', 
    name: '陶博士每日观察', 
    type: 'TREND', 
    desc: 'RPS > 87 + 均线多头 + 口袋支点' 
  },
];

export default function QuantFactoryPage() {
  const queryClient = useQueryClient();
  
  const { 
    selectedStrategy, timeMode, targetDate, startDate, endDate, scrollTop,
    setStrategy, setTimeMode, setDates, setScrollTop
  } = useQuantStore();

  const [logs, setLogs] = useState<string[]>([]);
  const listContainerRef = useRef<HTMLDivElement>(null);

  const { data: results, refetch, isFetching } = useQuery({
    queryKey: ['strategyPool', selectedStrategy, timeMode, targetDate, startDate, endDate],
    queryFn: () => {
      if (timeMode === 'single') {
        return watchlistApi.getStrategyPool(selectedStrategy, targetDate);
      } else {
        return watchlistApi.getStrategyPool(selectedStrategy, 'all', startDate, endDate);
      }
    },
    staleTime: 1000 * 60 * 30, 
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (results && listContainerRef.current) {
      requestAnimationFrame(() => {
        if (listContainerRef.current) {
          listContainerRef.current.scrollTop = scrollTop;
        }
      });
    }
  }, [results]);

  const runMutation = useMutation({
    mutationFn: (params: any) => strategyApi.run(params),
    onMutate: () => {
      setLogs(['> [SYSTEM] 指令已发送，正在唤醒量化引擎...']);
    },
    onSuccess: (data) => {
      setLogs(prev => [...prev, `> [SUCCESS] 运行成功: ${data.message || '完成'}`]);
      setLogs(prev => [...prev, `> [DB] 正在拉取最新结果...`]);
      setScrollTop(0); 
      queryClient.invalidateQueries({ queryKey: ['strategyPool'] });
    },
    onError: (error: any) => {
      setLogs(prev => [...prev, `> [ERROR] 运行失败: ${error.message || '超时或错误'}`]);
    }
  });

  const handleRun = () => {
    if (timeMode === 'single') {
      setLogs(prev => [...prev, `> [CONFIG] 模式: 单日狙击 | 日期: ${targetDate}`]);
      runMutation.mutate({ strategy_name: selectedStrategy, trade_date: targetDate });
    } else {
      setLogs(prev => [...prev, `> [CONFIG] 模式: 区间回测 | ${startDate} -> ${endDate}`]);
      runMutation.mutate({ strategy_name: selectedStrategy, start_date: startDate, end_date: endDate });
    }
  };

  return (
    <div className="h-full flex flex-col bg-[#080a0d] text-[#d1d1d1]">
      
      {/* 顶部标题 - 对齐 Webull 标准高度 */}
      <div className="h-12 flex items-center justify-between px-4 border-b border-[#1e2226] bg-[#111417] shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-1 h-4 bg-[#1861ff]"></div>
          <h1 className="text-sm font-bold text-white flex items-center gap-3 uppercase tracking-tight">
            Quant Factory <span className="text-[#8a8d91] font-mono text-[10px]">v2.1</span>
          </h1>
        </div>
      </div>

      {/* 主体区域 */}
      <div className="flex-1 flex flex-col p-1 gap-1 min-h-0 overflow-hidden">
        
        {/* 上半部分：控制台 */}
        <div className="h-[280px] shrink-0 flex gap-1">
          
          {/* 左侧：策略库 */}
          <div className="flex-[2] bg-[#111417] border border-[#1e2226] flex flex-col min-w-0">
            <div className="h-9 px-4 border-b border-[#1e2226] flex items-center gap-2 text-[11px] text-[#1861ff] font-bold uppercase shrink-0 bg-[#1c2127]/30">
              <Filter size={12} /> Strategy Library
            </div>
            <div className="flex-1 overflow-y-auto custom-scrollbar p-3 space-y-1">
              {STRATEGIES.map(s => (
                <div 
                  key={s.id}
                  onClick={() => setStrategy(s.id)}
                  className={clsx(
                    "p-3 rounded-sm border transition-all cursor-pointer flex justify-between items-center group",
                    selectedStrategy === s.id 
                      ? "bg-[#1861ff]/10 border-[#1861ff] shadow-[inset_0_0_10px_rgba(24,97,255,0.05)]" 
                      : "bg-[#1c2127]/40 border-transparent hover:bg-[#1c2127]/80 hover:border-[#30363d]"
                  )}
                >
                  <div>
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className={clsx("font-bold text-[13px]", selectedStrategy === s.id ? "text-white" : "text-[#d1d1d1]")}>{s.name}</span>
                      <span className="text-[9px] px-1 bg-black/40 rounded-sm text-[#8a8d91] border border-[#1e2226] font-mono">{s.type}</span>
                    </div>
                    <p className="text-[11px] text-[#8a8d91] font-medium leading-none">{s.desc}</p>
                  </div>
                  {selectedStrategy === s.id && <CheckCircle2 size={16} className="text-[#1861ff]" />}
                </div>
              ))}
            </div>
          </div>

          {/* 右侧：参数控制 */}
          <div className="flex-1 bg-[#111417] border border-[#1e2226] flex flex-col min-w-[300px]">
             <div className="h-9 px-4 border-b border-[#1e2226] flex items-center gap-2 text-[11px] text-[#00c087] font-bold uppercase shrink-0 bg-[#1c2127]/30">
                <Clock size={12} /> Parameters
             </div>
             <div className="p-4 flex flex-col gap-4 flex-1 overflow-y-auto">
                 {/* 仿微牛 Segmented Switch */}
                 <div className="flex bg-[#080a0d] p-0.5 rounded-sm border border-[#1e2226] shrink-0">
                   <button onClick={() => setTimeMode('single')} className={clsx("flex-1 py-1 text-[10px] font-bold transition-all", timeMode === 'single' ? "bg-[#1e2226] text-white" : "text-[#8a8d91] hover:text-white")}>
                     SINGLE
                   </button>
                   <button onClick={() => setTimeMode('range')} className={clsx("flex-1 py-1 text-[10px] font-bold transition-all", timeMode === 'range' ? "bg-[#1e2226] text-white" : "text-[#8a8d91] hover:text-white")}>
                     RANGE
                   </button>
                 </div>

                 <div className="flex-1 flex flex-col justify-center gap-3">
                    {timeMode === 'single' ? (
                      <div className="space-y-1">
                        <label className="text-[10px] text-[#8a8d91] uppercase font-bold ml-0.5">Target Date</label>
                        <input type="date" value={targetDate} onChange={(e) => setDates({ target: e.target.value })} className="w-full bg-[#1c2127] border border-[#30363d] rounded-sm px-3 py-2 text-[12px] text-white focus:border-[#1861ff] outline-none font-mono" />
                      </div>
                    ) : (
                      <div className="space-y-3">
                        <div className="space-y-1">
                          <label className="text-[10px] text-[#8a8d91] uppercase font-bold ml-0.5">Start Date</label>
                          <input type="date" value={startDate} onChange={(e) => setDates({ start: e.target.value })} className="w-full bg-[#1c2127] border border-[#30363d] rounded-sm px-3 py-1.5 text-[12px] text-white focus:border-[#1861ff] outline-none font-mono" />
                        </div>
                        <div className="space-y-1">
                          <label className="text-[10px] text-[#8a8d91] uppercase font-bold ml-0.5">End Date</label>
                          <input type="date" value={endDate} onChange={(e) => setDates({ end: e.target.value })} className="w-full bg-[#1c2127] border border-[#30363d] rounded-sm px-3 py-1.5 text-[12px] text-white focus:border-[#1861ff] outline-none font-mono" />
                        </div>
                      </div>
                    )}
                 </div>

                 <button onClick={handleRun} disabled={runMutation.isPending} className={clsx("w-full py-2.5 rounded-sm font-black text-[12px] flex items-center justify-center gap-2 transition-all shrink-0 uppercase tracking-widest shadow-lg", runMutation.isPending ? "bg-[#1c2127] text-[#8a8d91] cursor-not-allowed" : "bg-[#1861ff] text-white shadow-blue-900/20 hover:bg-[#1861ff]/90")}>
                   {runMutation.isPending ? <Loader2 className="animate-spin" size={14} /> : <Play size={14} fill="currentColor" />}
                   {runMutation.isPending ? "Computing..." : "Execute Strategy"}
                 </button>
             </div>
          </div>
        </div>

        {/* 下半部分：结果与终端 */}
        <div className="flex-1 flex gap-1 min-h-0 overflow-hidden">
           
           {/* 信号池卡片 */}
           <div className="flex-[2] bg-[#111417] border border-[#1e2226] flex flex-col min-w-0">
              <div className="h-9 bg-[#1c2127]/30 border-b border-[#1e2226] flex items-center px-4 justify-between shrink-0">
                 <span className="text-[11px] font-bold text-[#8a8d91] flex items-center gap-2 uppercase tracking-tight">
                   <Zap size={12} className="text-[#ffb11a]"/> 
                   Signal Pool ({results?.length || 0})
                 </span>
                 <div className="text-[10px] text-[#30363d] font-mono">
                    {timeMode === 'single' ? targetDate : `${startDate} ~ ${endDate}`}
                 </div>
              </div>
              
              <div 
                ref={listContainerRef}
                onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
                className="flex-1 overflow-y-auto custom-scrollbar p-0"
              >
                 {isFetching && !results ? (
                    <div className="h-full flex items-center justify-center text-[#8a8d91] text-[10px] font-mono animate-pulse uppercase">Fetching Results...</div>
                 ) : !results?.length ? (
                   <div className="h-full flex flex-col items-center justify-center text-[#30363d] gap-2">
                     <div className="text-3xl opacity-20 italic font-black">NULL</div>
                   </div>
                 ) : (
                   <table className="w-full text-left border-collapse">
                     <thead className="bg-[#1c2127] text-[#8a8d91] sticky top-0 z-10">
                       <tr>
                         <th className="px-4 py-2 text-[10px] font-bold uppercase tracking-tighter">Date</th>
                         <th className="px-4 py-2 text-[10px] font-bold uppercase tracking-tighter">Code</th>
                         <th className="px-4 py-2 text-[10px] font-bold uppercase tracking-tighter">Name</th>
                         <th className="px-4 py-2 text-[10px] font-bold uppercase tracking-tighter">RPS</th>
                         <th className="px-4 py-2 text-right"></th>
                       </tr>
                     </thead>
                     <tbody className="divide-y divide-[#1e2226]">
                       {results.map((stock: any, idx: number) => (
                         <tr key={`${stock.code}-${stock.date}-${idx}`} className="hover:bg-[#1c2127] transition-colors group">
                           <td className="px-4 py-2.5 text-[#8a8d91] font-mono text-[11px]">{stock.date}</td>
                           <td className="px-4 py-2.5 font-mono text-[#1861ff] text-[12px] font-bold">{stock.code}</td>
                           <td className="px-4 py-2.5 font-bold text-[#d1d1d1] text-[12px]">{stock.name}</td>
                           <td className="px-4 py-2.5">
                             <span className={clsx(
                               "text-[11px] font-mono font-bold",
                               stock.rps >= 95 ? "text-[#ff4d4d]" : "text-[#ffb11a]"
                             )}>
                               {stock.rps}
                             </span>
                           </td>
                           <td className="px-4 py-2.5 text-right">
                             <Link 
                               href={`/stock/${stock.code}`}
                               scroll={false} 
                               className="inline-flex items-center gap-1.5 px-3 py-1 bg-[#1861ff]/10 text-[#1861ff] border border-[#1861ff]/20 hover:bg-[#1861ff] hover:text-white rounded-sm text-[10px] font-black uppercase transition-all"
                             >
                               ANALYSIS <ArrowRight size={10} />
                             </Link>
                           </td>
                         </tr>
                       ))}
                     </tbody>
                   </table>
                 )}
              </div>
           </div>

           {/* 终端日志 - 纯粹的黑客终端风格 */}
           <div className="flex-1 bg-[#080a0d] border border-[#1e2226] flex flex-col font-mono shadow-inner min-w-[240px]">
              <div className="h-7 bg-[#1c2127]/50 border-b border-[#1e2226] flex items-center px-3 gap-2 shrink-0">
                 <TerminalSquare size={10} className="#8a8d91"/>
                 <span className="text-[9px] text-[#30363d] font-black tracking-widest uppercase">Console Terminal</span>
              </div>
              
              <div className="flex-1 overflow-y-auto custom-scrollbar p-3">
                 <div className="min-h-full pb-2"> 
                   {logs.length === 0 && <div className="text-[#1e2226] text-[11px] select-none font-black italic tracking-tighter">_AWAITING_COMMAND...</div>}
                   {logs.map((log, i) => (
                     <div key={i} className="break-all animate-in slide-in-from-left-0.5 duration-100 text-[#8a8d91] leading-relaxed text-[10px] mb-0.5 font-medium border-l border-[#1861ff]/20 pl-2">
                       {log}
                     </div>
                   ))}
                 </div>
              </div>
           </div>
        </div>
      </div>
    </div>
  );
}

function FlaskConicalIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="text-[#1861ff]"><path d="M10 2v7.527a2 2 0 0 1-.211.896L4.72 20.55a1 1 0 0 0 .9 1.45h12.76a1 1 0 0 0 .9-1.45l-5.069-10.127A2 2 0 0 1 14 9.527V2"/><path d="M8.5 2h7"/><path d="M7 16h10"/></svg>
  )
}