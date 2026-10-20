'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Newspaper, ExternalLink, Clock, TrendingUp } from 'lucide-react';
import { clsx } from 'clsx';
import { marketApi } from '@/lib/api';

export default function NewsPanel({ limit = 5 }: { limit?: number }) {
  const [selectedSector, setSelectedSector] = useState<string>('all');
  const { data, isLoading } = useQuery({
    queryKey: ['marketNews', selectedSector, limit],
    queryFn: () => marketApi.getNews({
      limit,
      sector: selectedSector === 'all' ? undefined : selectedSector,
    }),
  });

  const newsItems = data?.items || [];
  const sectors = ['all', ...(data?.sectors || [])];

  return (
    <div className="w-full bg-bg-secondary border border-border-primary rounded-2xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-primary bg-[#1c2127]/30">
        <div className="flex items-center gap-2">
          <div className="w-1 h-3 bg-[#ffb11a]"></div>
          <h3 className="text-text-primary text-xs font-bold uppercase tracking-wider">
            热点新闻舆情
          </h3>
          <Newspaper size={12} className="text-[#ffb11a] ml-1" />
        </div>

        <div className="flex gap-1">
          {sectors.map((sector: string) => (
            <button
              key={sector}
              type="button"
              onClick={() => setSelectedSector(sector)}
              className={clsx(
                "px-2 py-1 text-[10px] font-bold transition-all rounded-sm",
                selectedSector === sector
                  ? "bg-[#1861ff] text-text-primary"
                  : "bg-[#080a0d] text-text-secondary hover:text-text-primary"
              )}
            >
              {sector === 'all' ? '全部' : sector}
            </button>
          ))}
        </div>
      </div>

      <div className="divide-y divide-[#1e2226]">
        {isLoading ? (
          <div className="p-4 text-center text-xs text-text-secondary">新闻数据加载中</div>
        ) : newsItems.length === 0 ? (
          <div className="p-4 text-center text-xs text-text-secondary">暂无数据库新闻</div>
        ) : newsItems.map((news: any, idx: number) => {
          const content = (
            <div className="flex gap-4">
              <div className="flex-shrink-0">
                <div
                  className={clsx(
                    "px-2 py-1 rounded-sm text-[10px] font-bold uppercase",
                    news.sentiment === 'positive'
                      ? "bg-[#ff4d4d]/10 text-[#ff4d4d] border border-[#ff4d4d]/20"
                      : news.sentiment === 'negative'
                      ? "bg-[#00c087]/10 text-[#00c087] border border-[#00c087]/20"
                      : "bg-[#8a8d91]/10 text-text-secondary border border-[#8a8d91]/20"
                  )}
                >
                  {news.sector || '全市场'}
                </div>
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <h4 className="text-sm font-bold text-text-primary group-hover:text-text-primary transition-colors flex-1 line-clamp-1">
                    {news.title}
                  </h4>
                  {news.impact === 'high' && (
                    <div className="flex items-center gap-1 text-[#ff4d4d] flex-shrink-0">
                      <TrendingUp size={10} />
                      <span className="text-[10px] font-bold uppercase">High Impact</span>
                    </div>
                  )}
                </div>

                <p className="text-[11px] text-text-secondary line-clamp-2 mb-2">{news.summary}</p>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 text-[10px] text-text-secondary">
                    <span className="font-mono">{news.source || '-'}</span>
                    <span className="flex items-center gap-1">
                      <Clock size={10} />
                      {news.time || '-'}
                    </span>
                  </div>
                  {news.url && (
                    <span className="flex items-center gap-1 text-[10px] text-[#1861ff] opacity-0 group-hover:opacity-100 transition-opacity">
                      查看详情 <ExternalLink size={10} />
                    </span>
                  )}
                </div>
              </div>
            </div>
          );

          if (news.url) {
            return (
              <a
                key={news.id || idx}
                href={news.url}
                target="_blank"
                rel="noreferrer"
                className="block p-4 hover:bg-[#1c2127]/50 transition-all group cursor-pointer"
              >
                {content}
              </a>
            );
          }

          return (
            <div
              key={news.id || idx}
              className="p-4 hover:bg-[#1c2127]/50 transition-all group"
            >
              {content}
            </div>
          );
        })}
      </div>
    </div>
  );
}
