'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { watchlistApi } from '@/lib/api'; // 确保 api.ts 路径正确
import { Loader2, Search, TrendingUp } from 'lucide-react';

export default function StockIndexPage() {
  const router = useRouter();
  const [status, setStatus] = useState<'loading' | 'empty'>('loading');

  useEffect(() => {
    async function initRedirect() {
      try {
        // 1. 尝试获取用户的自选股列表
        const list = await watchlistApi.getAll();
        
        // 2. 如果有自选股，直接跳到第一只 (比如 /stock/600519)
        if (list && list.length > 0) {
          const firstStock = list[0].code;
          router.replace(`/stock/${firstStock}`);
        } else {
          // 3. 如果没数据，显示空状态页
          setStatus('empty');
        }
      } catch (e) {
        // 出错也显示空状态，防止白屏
        console.error("Failed to load watchlist", e);
        setStatus('empty');
      }
    }

    initRedirect();
  }, [router]);

  // --- 加载中状态 ---
  if (status === 'loading') {
    return (
      <div className="h-full flex flex-col items-center justify-center text-slate-500 gap-3">
        <Loader2 className="animate-spin text-blue-500" size={32} />
        <div className="flex flex-col items-center">
          <span className="text-xs font-mono tracking-widest text-slate-400">INITIALIZING QUANT ENGINE</span>
          <span className="text-[10px] text-slate-600 mt-1">正在检索自选股池...</span>
        </div>
      </div>
    );
  }

  // --- 空状态 (引导用户搜索) ---
  return (
    <div className="h-full flex flex-col items-center justify-center p-6">
      
      {/* 装饰性背景光 */}
      <div className="absolute w-[500px] h-[500px] bg-blue-500/5 rounded-full blur-3xl pointer-events-none" />

      <div className="relative z-10 flex flex-col items-center max-w-md text-center">
        {/* 图标组 */}
        <div className="relative mb-8">
           <div className="w-20 h-20 bg-gradient-to-tr from-slate-800 to-slate-900 rounded-2xl border border-white/10 flex items-center justify-center shadow-2xl rotate-3">
              <TrendingUp size={40} className="text-blue-500" />
           </div>
           <div className="absolute -top-4 -right-4 w-12 h-12 bg-slate-800 rounded-xl border border-white/10 flex items-center justify-center shadow-lg -rotate-6 z-0 opacity-60">
              <Search size={20} className="text-slate-400" />
           </div>
        </div>

        <h1 className="text-2xl font-bold text-white mb-3">个股作战室就绪</h1>
        <p className="text-slate-400 text-sm leading-relaxed mb-8">
          你的自选股列表暂为空。请使用左上角的全局搜索框查找股票代码，或直接点击下方按钮查看示例。
        </p>

        <div className="flex gap-4">
           <button 
             onClick={() => router.push('/stock/600519')}
             className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-all shadow-[0_0_20px_rgba(37,99,235,0.3)] hover:shadow-[0_0_25px_rgba(37,99,235,0.5)] active:scale-95"
           >
             查看 贵州茅台 (600519)
           </button>
        </div>

        <div className="mt-12 pt-8 border-t border-white/5 w-full flex justify-center gap-8 text-[10px] text-slate-600 font-mono uppercase tracking-widest">
           <span>RPS Rating</span>
           <span>Trend Template</span>
           <span>Pocket Pivot</span>
        </div>
      </div>
    </div>
  );
}