'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Search, X } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { stockApi } from '@/lib/api';

interface SearchResult {
  symbol: string;
  name: string;
  industry?: string;
  market_cap?: number;
  pe?: number;
  pb?: number;
}

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const closePalette = useCallback(() => {
    setOpen(false);
    setKeyword('');
    setActiveIndex(0);
  }, []);

  // ⌘K / Ctrl+K 打开
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        if (open) {
          closePalette();
        } else {
          setOpen(true);
        }
      }
      if (e.key === 'Escape') {
        closePalette();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [closePalette, open]);

  // 打开时自动聚焦
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => inputRef.current?.focus(), 50);
    return () => window.clearTimeout(timer);
  }, [open]);

  const { data, isFetching } = useQuery({
    queryKey: ['cmdSearch', keyword],
    queryFn: async () => {
      if (!keyword.trim()) return [];
      const res = await stockApi.searchStock(keyword.trim());
      const stocks = (res as any)?.stocks ?? [];
      return stocks as SearchResult[];
    },
    enabled: open && keyword.trim().length > 0,
    staleTime: 30_000,
  });

  const results: SearchResult[] = useMemo(() => data ?? [], [data]);

  const navigate = useCallback((symbol: string) => {
    router.push(`/a/stock/${symbol}`);
    closePalette();
  }, [closePalette, router]);

  // 键盘导航
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, results.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' && results[activeIndex]) {
        e.preventDefault();
        navigate(results[activeIndex].symbol);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, results, activeIndex, navigate]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center pt-[15vh]"
      onClick={closePalette}
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-[560px] mx-4 rounded-xl border border-border-primary bg-bg-secondary shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 搜索框 */}
        <div className="flex items-center gap-3 border-b border-border-secondary px-4 py-3">
          <Search size={18} className="shrink-0 text-text-tertiary" />
          <input
            ref={inputRef}
            value={keyword}
            onChange={(e) => {
              setKeyword(e.target.value);
              setActiveIndex(0);
            }}
            placeholder="搜索股票代码 / 名称 / 拼音..."
            className="flex-1 bg-transparent text-sm text-text-primary outline-none placeholder:text-text-tertiary"
          />
          <kbd className="rounded border border-border-secondary px-1.5 py-0.5 font-mono text-[10px] text-text-tertiary">ESC</kbd>
          <button onClick={closePalette} className="text-text-tertiary hover:text-text-primary">
            <X size={16} />
          </button>
        </div>

        {/* 结果列表 */}
        <div ref={listRef} className="max-h-[360px] overflow-y-auto custom-scrollbar">
          {isFetching && (
            <div className="px-4 py-8 text-center text-xs text-text-tertiary">搜索中...</div>
          )}
          {!isFetching && keyword.trim() && results.length === 0 && (
            <div className="px-4 py-8 text-center text-xs text-text-tertiary">
              没有找到匹配的股票
            </div>
          )}
          {!isFetching && !keyword.trim() && (
            <div className="px-4 py-8 text-center text-xs text-text-tertiary">
              输入股票代码或名称开始搜索
            </div>
          )}
          {!isFetching && results.length > 0 && (
            <div className="py-1">
              {results.map((stock, index) => (
                <button
                  key={stock.symbol}
                  type="button"
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => navigate(stock.symbol)}
                  className={`flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                    index === activeIndex ? 'bg-brand-blue/15' : 'hover:bg-bg-tertiary/60'
                  }`}
                >
                  <span className={`font-mono text-sm font-bold ${index === activeIndex ? 'text-brand-blue-light' : 'text-text-primary'}`}>
                    {stock.symbol}
                  </span>
                  <span className="flex-1 truncate text-sm text-text-primary">{stock.name}</span>
                  {stock.industry && (
                    <span className="truncate text-[11px] text-text-tertiary">{stock.industry}</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* 底部提示 */}
        <div className="flex items-center justify-between border-t border-border-secondary px-4 py-2 text-[10px] text-text-tertiary">
          <div className="flex items-center gap-3">
            <span><kbd className="rounded border border-border-secondary px-1">↑↓</kbd> 导航</span>
            <span><kbd className="rounded border border-border-secondary px-1">↵</kbd> 进入作战室</span>
          </div>
          <span>共 {results.length} 条结果</span>
        </div>
      </div>
    </div>
  );
}
