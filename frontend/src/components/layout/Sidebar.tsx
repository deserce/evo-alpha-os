'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  BarChart3,
  Bot,
  BriefcaseBusiness,
  ChevronLeft,
  ChevronRight,
  Database,
  FileText,
  Layers,
  Radar,
  Sparkles,
  Zap,
} from 'lucide-react';
import { clsx } from 'clsx';
import { useGlobalStore } from '@/lib/store';
import { quantApi } from '@/lib/api';
import { asList } from '@/components/workspace/DashboardPrimitives';

const MODULES = [
  { id: 'dashboard', name: 'Dashboard', sub: '总览', path: '/a/quant', activePath: '/a/quant', icon: Radar },
  { id: 'market', name: 'Market', sub: '市场温度', path: '/a/market', activePath: '/a/market', icon: Activity },
  { id: 'industries', name: 'Industries', sub: '行业战役', path: '/a/sector', activePath: '/a/sector', icon: Layers },
  { id: 'alpha', name: 'Alpha Pool', sub: '机会池', path: '/a/alpha-pool', activePath: '/a/alpha-pool', icon: Sparkles },
  { id: 'research', name: 'Research', sub: '产业研究池', path: '/a/research', activePath: '/a/research', icon: Sparkles },
  { id: 'stock', name: 'Stocks', sub: '个股作战', path: '/a/stock', activePath: '/a/stock', icon: BarChart3 },
  { id: 'signals', name: 'Signals', sub: '买点信号', path: '/a/signals', activePath: '/a/signals', icon: Zap },
  { id: 'portfolio', name: 'Portfolio', sub: '持仓风控', path: '/a/portfolio', activePath: '/a/portfolio', icon: BriefcaseBusiness },
  { id: 'review', name: 'Review', sub: '复盘进化', path: '/a/report', activePath: '/a/report', icon: FileText },
  { id: 'ai', name: 'AI Lab', sub: 'AI实验室', path: '/a/quant#ai-brief', icon: Bot },
  { id: 'settings', name: 'Settings', sub: '系统设置', path: '/a/data', activePath: '/a/data', icon: Database },
];

function isActive(pathname: string, activePath?: string) {
  if (!activePath) return false;
  return pathname === activePath || pathname.startsWith(`${activePath}/`);
}

export default function Sidebar() {
  const pathname = usePathname();
  const { globalDate, isSidebarExpanded, toggleSidebar } = useGlobalStore();
  const datesQuery = useQuery({
    queryKey: ['sidebarDates'],
    queryFn: () => quantApi.getDates(80),
    staleTime: 60_000,
  });
  const dates = asList<string>(datesQuery.data?.dates);
  const selectedDate = dates.includes(globalDate) ? globalDate : datesQuery.data?.latest || '';

  return (
    <aside
      style={{ width: isSidebarExpanded ? 240 : 56 }}
      className="evo-sidebar hidden lg:flex flex-col h-screen flex-shrink-0 overflow-hidden transition-all duration-300 ease-in-out"
    >
      {/* Logo + 折叠按钮 */}
      <div className="flex items-center gap-2 px-3 pb-3 pt-4 shrink-0">
        <Link href="/a/quant" className="flex items-center gap-2 min-w-0">
          <div className="evo-brand-mark shrink-0">
            <span>A</span>
          </div>
          {isSidebarExpanded && (
            <div className="min-w-0">
              <div className="text-sm font-black leading-none text-white truncate">EvoAlpha OS</div>
              <div className="mt-0.5 text-[9px] text-text-tertiary truncate">A-SHARE OS</div>
            </div>
          )}
        </Link>
        {isSidebarExpanded && (
          <button
            type="button"
            onClick={toggleSidebar}
            className="ml-auto h-7 w-7 flex items-center justify-center rounded-md border border-border-primary bg-bg-secondary text-text-secondary hover:text-text-primary hover:bg-bg-tertiary transition-colors shrink-0"
            title="收起侧边栏"
          >
            <ChevronLeft size={14} />
          </button>
        )}
      </div>

      {/* 展开按钮 — 折叠态显示 */}
      {!isSidebarExpanded && (
        <button
          type="button"
          onClick={toggleSidebar}
          className="mx-auto mb-2 h-7 w-7 flex items-center justify-center rounded-md border border-border-primary bg-bg-secondary text-text-secondary hover:text-text-primary hover:bg-bg-tertiary transition-colors shrink-0"
          title="展开侧边栏"
        >
          <ChevronRight size={14} />
        </button>
      )}

      <nav className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar px-2">
        <div className="space-y-0.5">
          {MODULES.map((module) => {
            const Icon = module.icon;
            const active = isActive(pathname, module.activePath);
            return (
              <Link
                key={module.id}
                href={module.path}
                className={clsx(
                  'group flex h-9 items-center rounded-lg border transition-all duration-150',
                  active
                    ? 'border-brand-blue/50 bg-brand-blue/15 text-white'
                    : 'border-transparent text-text-secondary hover:border-border-primary hover:bg-bg-secondary/80 hover:text-text-primary',
                  isSidebarExpanded ? 'gap-2 px-2' : 'justify-center px-0'
                )}
                title={!isSidebarExpanded ? `${module.name} - ${module.sub}` : undefined}
              >
                <span className={clsx(
                  'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md',
                  active
                    ? 'text-brand-blue-light'
                    : 'text-text-tertiary group-hover:text-brand-blue-light',
                )}>
                  <Icon size={15} />
                </span>
                {isSidebarExpanded && (
                  <span className="min-w-0">
                    <span className="block truncate text-[11px] font-bold leading-none">{module.name}</span>
                    <span className="mt-0.5 block truncate text-[9px] text-text-tertiary">{module.sub}</span>
                  </span>
                )}
              </Link>
            );
          })}
        </div>
      </nav>

      {isSidebarExpanded && (
        <div className="shrink-0 px-3 pb-4">
          <div className="rounded-lg border border-border-primary bg-bg-secondary/70 p-2.5">
            <div className="flex items-center gap-2 text-[10px] text-text-secondary">
              <span className="status-dot bg-success text-success" />
              数据刷新 {selectedDate || '-'}
            </div>
            <div className="mt-1 text-[9px] text-text-tertiary">免盘崩期</div>
          </div>
        </div>
      )}
    </aside>
  );
}
