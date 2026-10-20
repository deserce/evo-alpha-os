'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  Bell,
  Bot,
  BriefcaseBusiness,
  ChevronDown,
  Database,
  FileText,
  Layers,
  LineChart,
  Menu,
  Radar,
  Search,
  Settings,
  Sparkles,
  Star,
  Target,
  X,
  Zap,
} from 'lucide-react';
import { clsx } from 'clsx';
import { useGlobalStore } from '@/lib/store';
import { quantApi } from '@/lib/api';

const NAV_TABS = [
  { id: 'dashboard', label: 'Dashboard', sub: '总览', path: '/a/quant', activePath: '/a/quant', icon: Radar },
  { id: 'market', label: 'Market', sub: '市场温度', path: '/a/market', activePath: '/a/market', icon: Activity },
  { id: 'sector', label: 'Industries', sub: '行业战役', path: '/a/sector', activePath: '/a/sector', icon: Layers },
  { id: 'strategy', label: 'Strategy', sub: '策略选股', path: '/a/strategy', activePath: '/a/strategy', icon: Target },
  { id: 'research', label: 'Research', sub: '产业研究', path: '/a/research', activePath: '/a/research', icon: Sparkles },
  { id: 'watchlist', label: 'Watchlist', sub: '自选股', path: '/a/watchlist', activePath: '/a/watchlist', icon: Star },
  { id: 'portfolio', label: 'Portfolio', sub: '持仓风控', path: '/a/portfolio', activePath: '/a/portfolio', icon: BriefcaseBusiness },
  { id: 'review', label: 'Review', sub: '复盘进化', path: '/a/report', activePath: '/a/report', icon: FileText },
];

const MOBILE_NAV = [
  ...NAV_TABS,
  { id: 'data', label: 'Settings', sub: '系统设置', path: '/a/data', activePath: '/a/data', icon: Settings },
];

function isActive(pathname: string, activePath?: string) {
  if (!activePath) return false;
  if (activePath === '/a/strategy') {
    return pathname === '/a/strategy' || pathname.startsWith('/a/strategy/') ||
           pathname === '/a/alpha-pool' || pathname.startsWith('/a/alpha-pool/') ||
           pathname === '/a/signals' || pathname.startsWith('/a/signals/');
  }
  if (activePath === '/a/watchlist') {
    return pathname === '/a/watchlist' ||
           pathname.startsWith('/a/watchlist/') ||
           pathname.startsWith('/a/stock/');
  }
  return pathname === activePath || pathname.startsWith(`${activePath}/`);
}

export default function TopNavigation() {
  const pathname = usePathname();
  const { globalDate, setGlobalDate, drawerOpen } = useGlobalStore();
  const setReportOpen = useGlobalStore((state) => state.setReportOpen);
  const [showMobileMenu, setShowMobileMenu] = useState(false);
  const [showSettingsMenu, setShowSettingsMenu] = useState(false);
  const datesQuery = useQuery({
    queryKey: ['topQuantDates'],
    queryFn: () => quantApi.getDates(80),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (datesQuery.data?.latest && !globalDate) {
      setGlobalDate(datesQuery.data.latest);
    }
  }, [datesQuery.data?.latest, globalDate, setGlobalDate]);

  return (
    <>
      <header className="evo-topbar sticky top-0 z-40 w-full">
        <div className="flex h-12 items-center justify-between gap-3 px-3 sm:px-4">
          <div className="flex min-w-0 items-center gap-3">
            <Link href="/a/quant" className="flex shrink-0 items-center gap-2">
              <div className="evo-mobile-logo">
                <span>A</span>
              </div>
              <div className="hidden sm:block">
                <div className="text-sm font-black leading-none text-white">EvoAlpha OS</div>
                <div className="mt-0.5 text-[10px] font-mono text-text-tertiary">A-SHARE OS</div>
              </div>
            </Link>

            <div
              className="hidden h-8 w-[220px] max-w-[16vw] cursor-pointer items-center gap-2 rounded-lg border border-border-primary bg-[#060d19] px-3 text-text-tertiary transition-colors hover:border-brand-blue/40 2xl:flex"
              onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}
            >
              <Search size={14} />
              <span className="truncate text-xs">搜索股票 / 行业 / 主题 / 指标</span>
              <span className="ml-auto rounded border border-border-secondary px-1.5 py-0.5 font-mono text-[10px] text-text-tertiary">CMD K</span>
            </div>
          </div>

          <nav className="hidden min-w-0 flex-1 items-center gap-0.5 overflow-x-auto custom-scrollbar lg:flex">
            {NAV_TABS.map((tab) => {
              const Icon = tab.icon;
              const active = isActive(pathname, tab.activePath);
              return (
                <Link
                  key={tab.id}
                  href={tab.path}
                  className={clsx(
                    'group relative inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2 text-[11px] font-black transition-colors',
                    active ? 'bg-brand-blue/12 text-brand-blue-light' : 'text-text-secondary hover:bg-bg-secondary/80 hover:text-text-primary',
                  )}
                >
                  <Icon size={13} className={active ? 'text-brand-blue-light' : 'text-text-tertiary group-hover:text-text-secondary'} />
                  <span>{tab.label}</span>
                  {active && (
                    <span className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-brand-blue-light" />
                  )}
                </Link>
              );
            })}
          </nav>

          <div className="flex shrink-0 items-center gap-2">
            {pathname.startsWith('/a/watchlist') || pathname.startsWith('/a/stock/') || pathname.startsWith('/a/strategy') || drawerOpen ? (
            <button type="button" onClick={() => setReportOpen(true)} aria-label="打开 AI 助手"
              className="inline-flex h-8 items-center gap-2 rounded-lg border border-brand-blue/30 bg-brand-blue/15 px-2.5 text-xs font-black text-brand-blue-light">
              <Bot size={14} /><span className="hidden sm:inline">AI 助手</span>
            </button>
            ) : (
              <Link href="/a/watchlist" onClick={() => setReportOpen(true)} aria-label="打开 Watchlist AI 助手"
                className="inline-flex h-8 items-center gap-2 rounded-lg border border-brand-blue/30 bg-brand-blue/15 px-2.5 text-xs font-black text-brand-blue-light">
                <Bot size={14} /><span className="hidden sm:inline">AI 助手</span>
              </Link>
            )}

            <button className="relative hidden h-8 w-8 items-center justify-center rounded-lg border border-border-primary bg-[#060d19] text-text-secondary sm:inline-flex">
              <Bell size={14} />
              <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-danger" />
            </button>

            {/* Settings dropdown */}
            <div className="relative">
              <button
                type="button"
                onClick={() => setShowSettingsMenu((v) => !v)}
                className="hidden h-8 w-8 items-center justify-center rounded-lg border border-border-primary bg-[#060d19] text-text-secondary sm:inline-flex"
                title="系统设置"
              >
                <Settings size={14} />
              </button>
              {showSettingsMenu && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setShowSettingsMenu(false)} />
                  <div className="absolute right-0 top-11 z-50 w-44 rounded-lg border border-border-primary bg-bg-secondary py-1 shadow-xl">
                    <Link href="/a/data" onClick={() => setShowSettingsMenu(false)} className="flex items-center gap-2 px-3 py-2 text-xs text-text-secondary hover:bg-bg-tertiary hover:text-text-primary">
                      <Database size={13} /> 数据管理
                    </Link>
                    <Link href="/a/quant#ai-brief" onClick={() => setShowSettingsMenu(false)} className="flex items-center gap-2 px-3 py-2 text-xs text-text-secondary hover:bg-bg-tertiary hover:text-text-primary">
                      <Bot size={13} /> AI 实验室
                    </Link>
                  </div>
                </>
              )}
            </div>

            <button className="hidden h-8 items-center gap-2 rounded-lg border border-border-primary bg-[#060d19] px-2 sm:flex">
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-[#ffd0c2] to-[#7aa7ff] text-[10px] font-black text-bg-primary">A</span>
              <span className="hidden text-left 2xl:block">
                <span className="block text-[11px] font-bold leading-none text-white">Alpha Hunter</span>
                <span className="mt-0.5 block text-[9px] text-text-tertiary">专业版</span>
              </span>
              <ChevronDown size={12} className="hidden text-text-tertiary 2xl:block" />
            </button>

            <button
              type="button"
              onClick={() => setShowMobileMenu((value) => !value)}
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border-primary bg-bg-secondary text-text-primary lg:hidden"
            >
              {showMobileMenu ? <X size={16} /> : <Menu size={16} />}
            </button>
          </div>
        </div>

        {/* Mobile menu */}
        {showMobileMenu && (
          <div className="grid grid-cols-2 gap-2 border-t border-border-primary bg-bg-secondary px-3 py-2 sm:grid-cols-3 lg:hidden">
            {MOBILE_NAV.map((tab) => {
              const Icon = tab.icon;
              const active = isActive(pathname, tab.activePath);
              return (
                <Link
                  key={tab.id}
                  href={tab.path}
                  onClick={() => setShowMobileMenu(false)}
                  className={clsx(
                    'inline-flex h-10 items-center gap-2 rounded-lg px-3 text-xs font-bold',
                    active ? 'bg-brand-blue/15 text-brand-blue-light' : 'bg-bg-primary text-text-secondary',
                  )}
                >
                  <Icon size={14} />
                  {tab.sub}
                </Link>
              );
            })}
          </div>
        )}
      </header>
    </>
  );
}
