'use client';

import { useGlobalStore } from '@/lib/store';
import { Calendar, Search, Bell, ChevronRight } from 'lucide-react';

export default function Topbar() {
  const { globalDate, setGlobalDate } = useGlobalStore();

  return (
    <header className="h-16 flex items-center justify-between px-8 border-b border-white/5 bg-[#0d1117]/80 backdrop-blur-md z-30 shrink-0">
      
      {/* 左侧：搜索区 */}
      <div className="flex items-center gap-6">
        <div className="flex items-center text-[10px] text-slate-500 font-bold tracking-[0.2em] uppercase">
          <span className="text-blue-500">EVO</span>QUANT <ChevronRight size={12} className="mx-2 text-slate-700" /> 
          <span className="text-slate-300">Market</span>
        </div>

        <div className="group relative flex items-center">
           <Search size={14} className="absolute left-3 text-slate-500 group-focus-within:text-blue-400" />
           <input 
             type="text" 
             placeholder="Search stocks..." 
             className="h-9 w-64 bg-white/5 border border-white/10 rounded-lg pl-9 pr-4 text-xs text-slate-200 focus:outline-none focus:border-blue-500/50 focus:w-80 transition-all placeholder:text-slate-600"
           />
        </div>
      </div>

      {/* 右侧：功能区 */}
      <div className="flex items-center gap-4">
        {/* Time Machine 胶囊 */}
        <div className="flex items-center bg-black/40 border border-white/10 rounded-full px-3 py-1 gap-3">
           <span className="text-[10px] font-black text-blue-500 uppercase tracking-tighter">Time Machine</span>
           <div className="flex items-center gap-2 bg-blue-500/10 border border-blue-500/20 rounded-full px-3 py-0.5">
             <Calendar size={12} className="text-blue-400" />
             <input 
               type="date" 
               value={globalDate}
               onChange={(e) => setGlobalDate(e.target.value)}
               className="bg-transparent text-[11px] font-mono text-blue-200 focus:outline-none"
             />
           </div>
        </div>

        <div className="h-4 w-px bg-white/10 mx-2"></div>

        <button className="p-2 text-slate-400 hover:text-white transition-colors relative">
          <Bell size={18} />
          <span className="absolute top-2 right-2 w-1.5 h-1.5 bg-red-500 rounded-full shadow-[0_0_8px_rgba(239,68,68,0.5)]"></span>
        </button>

        <div className="flex items-center gap-3 pl-2 border-l border-white/5">
           <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-600 to-blue-700 flex items-center justify-center text-[10px] font-bold text-white shadow-lg shadow-blue-900/20">
             EQ
           </div>
        </div>
      </div>
    </header>
  );
}