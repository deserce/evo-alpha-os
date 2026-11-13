'use client';

import Link from 'next/link';
import dynamic from 'next/dynamic';
import type { DailyHintsContext } from '@/components/stock/DailyHintsPanel';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  Bell,
  Bot,
  BriefcaseBusiness,
  Database,
  FileText,
  Layers,
  LineChart,
  Menu,
  Radar,
  Search,
  Sparkles,
  Star,
  Target,
  X,
  Zap,
} from 'lucide-react';
import { clsx } from 'clsx';
import { useGlobalStore } from '@/lib/store';
import { quantApi } from '@/lib/api';

const SharedWatchlistHints = dynamic(() =>
  import('@/components/stock/StockWorkspace').then((module) => module.AllWatchlistDailyHints),
  { ssr: false },
);
const ignoreHintSelection = () => {};

function WatchlistNotifications({ date, account }: { date: string; account: string }) {
  const [hints, setHints] = useState<DailyHintsContext>();
  const readKey = `evoalpha:shared-watchlist-read:${account}`;
  const [readDate, setReadDate] = useState(() => {
    try {
      return localStorage.getItem(readKey);
    } catch {
      return null;
    }
  });
  const unread = hints?.date === date && !hints.pending && hints.items.length > 0 && readDate !== date;

  function openHints() {
    const state = useGlobalStore.getState();
    state.setGlobalDate(date);
    state.setReportFocus(null);
    state.setStockSidebarUi({ tab: 'hints', hintsFilter: 'all', assistantLink: null, infoHidden: false });
    if (unread) {
      setReadDate(date);
      try {
        localStorage.setItem(readKey, date);
      } catch {
        // Keep the current tab's read state when storage is unavailable.
      }
    }
  }

  return (
    <>
      {readDate !== date && <SharedWatchlistHints date={date} summaryOnly
        onSelectSymbol={ignoreHintSelection} onContext={setHints} />}
      <Link href="/a/watchlist" onClick={openHints}
        aria-label={unread ? '共享自选股每日提示，有未读' : '共享自选股每日提示'}
        className="relative inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border-primary bg-[#060d19] text-text-secondary">
        <Bell size={14} />
        {unread && <span aria-hidden="true" className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-danger" />}
      </Link>
    </>
  );
}

const NAV_TABS = [
  { id: 'dashboard', label: '总览', sub: '总览', path: '/a/dashboard', activePath: '/a/dashboard', icon: Radar },
  { id: 'market', label: '市场', sub: '市场温度', path: '/a/market', activePath: '/a/market', icon: Activity },
  { id: 'sector', label: '行业', sub: '行业战役', path: '/a/sector', activePath: '/a/sector', icon: Layers },
  { id: 'strategy', label: '策略', sub: '策略选股', path: '/a/strategy', activePath: '/a/strategy', icon: Target },
  { id: 'research', label: '研究', sub: '产业研究', path: '/a/research', activePath: '/a/research', icon: Sparkles },
  { id: 'watchlist', label: '自选股', sub: '共享参考', path: '/a/watchlist', activePath: '/a/watchlist', icon: Star },
  { id: 'portfolio', label: '持仓', sub: '持仓风控', path: '/a/portfolio', activePath: '/a/portfolio', icon: BriefcaseBusiness },
  ...(process.env.NEXT_PUBLIC_EVOALPHA_HIDE_REVIEW === '1' ? [] : [{ id: 'review', label: '复盘', sub: '复盘进化', path: '/a/report', activePath: '/a/report', icon: FileText }]),
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
  const { globalDate, setGlobalDate } = useGlobalStore();
  const [showMobileMenu, setShowMobileMenu] = useState(false);
  const [showAccountMenu, setShowAccountMenu] = useState(false);
  const identity = useQuery({
    queryKey: ['navigation-identity'],
    queryFn: async () => {
      const response = await fetch('/api/v1/users/me', { cache: 'no-store' });
      if (!response.ok) return null;
      return (await response.json()) as { id: number; role: string; email: string; nickname: string; group_id: number | null };
    },
    retry: false,
  });
  const isAdmin = identity.data?.role === 'admin';
  const navigationTabs = NAV_TABS.filter((tab) => tab.id !== "portfolio" || isAdmin || !identity.data);
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
            <Link href="/a/dashboard" className="flex shrink-0 items-center gap-2">
              <div className="evo-mobile-logo">
                <span>A</span>
              </div>
              <div className="hidden sm:block">
                <div className="text-sm font-black leading-none text-white">EvoAlpha OS</div>
                <div className="mt-0.5 text-[10px] font-mono text-text-tertiary">A-SHARE OS</div>
              </div>
            </Link>
          </div>

          <nav className="hidden min-w-0 flex-1 items-center gap-0.5 overflow-x-auto custom-scrollbar lg:flex 2xl:flex-initial">
            {navigationTabs.map((tab) => {
              const Icon = tab.icon;
              const active = isActive(pathname, tab.activePath);
              return (
                <Link
                  key={tab.id}
                  href={tab.path}
                  aria-current={active ? 'page' : undefined}
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

          <div className="hidden min-w-0 flex-1 justify-center 2xl:flex">
            <button
              type="button"
              aria-label="打开全局搜索"
              className="flex h-8 w-full max-w-64 items-center gap-2 rounded-lg border border-border-primary bg-[#060d19] px-3 text-text-tertiary transition-colors hover:border-brand-blue/40"
              onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}
            >
              <Search size={14} className="shrink-0" />
              <span className="truncate text-xs">搜索股票 / 行业 / 主题 / 指标</span>
              <kbd className="ml-auto shrink-0 whitespace-nowrap rounded border border-border-secondary px-1.5 py-0.5 font-mono text-[10px]">CMD K</kbd>
            </button>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <div id="topbar-trade-date" className="empty:hidden" />
            {datesQuery.data?.latest && identity.isFetched && (
              <WatchlistNotifications key={identity.data?.id ?? 'local'}
                date={datesQuery.data.latest} account={String(identity.data?.id ?? 'local')} />
            )}

            {identity.data && <div className="relative">
              <button type="button" aria-label="账户菜单" aria-expanded={showAccountMenu}
                onClick={() => setShowAccountMenu((open) => !open)}
                className="flex h-8 items-center gap-2 rounded-lg border border-border-primary bg-[#060d19] px-2">
                <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-[#ffd0c2] to-[#7aa7ff] text-[10px] font-black text-bg-primary">
                  {Array.from(identity.data.nickname || identity.data.email)[0]?.toUpperCase()}
                </span>
                <span className="hidden max-w-28 truncate text-xs 2xl:block">{identity.data.nickname || identity.data.email}</span>
              </button>
              {showAccountMenu && <>
                <div className="fixed inset-0 z-40" onClick={() => setShowAccountMenu(false)} />
                <div className="absolute right-0 top-11 z-50 grid w-48 rounded-lg border border-border-primary bg-bg-secondary py-1 text-xs shadow-xl">
                  <span className="truncate px-3 py-2 text-text-secondary">{identity.data.nickname || identity.data.email}</span>
                  <Link href="/a/users" onClick={() => setShowAccountMenu(false)} className="px-3 py-2 hover:bg-bg-tertiary">
                    {isAdmin ? '账户中心 · 用户管理' : '个人设置'}
                  </Link>
                  <Link href="/a/watchlist?view=mine" onClick={() => setShowAccountMenu(false)} className="px-3 py-2 hover:bg-bg-tertiary">我的自选</Link>
                  {isAdmin && <>
                    <Link href="/a/data" onClick={() => setShowAccountMenu(false)} className="flex items-center gap-2 px-3 py-2 hover:bg-bg-tertiary"><Database size={13} /> 数据管理</Link>
                    <Link href="/a/dashboard#ai-brief" onClick={() => setShowAccountMenu(false)} className="flex items-center gap-2 px-3 py-2 hover:bg-bg-tertiary"><Bot size={13} /> AI 实验室</Link>
                  </>}
                  {process.env.NEXT_PUBLIC_EVOALPHA_LOGIN_TEST === '1' ?
                    <button type="button" className="px-3 py-2 text-left hover:bg-bg-tertiary" onClick={async (event) => {
                      const button = event.currentTarget;
                      button.disabled = true;
                      try {
                        const response = await fetch('/api/v1/users/logout', { method: 'POST' });
                        if (!response.ok) throw new Error('退出失败');
                        window.location.replace('/login');
                      } catch {
                        button.disabled = false;
                        window.alert('退出失败，请重试。');
                      }
                    }}>退出</button> :
                    <a href="/cdn-cgi/access/logout" className="px-3 py-2 hover:bg-bg-tertiary">退出</a>}
                </div>
              </>}
            </div>}

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
            {navigationTabs.map((tab) => {
              const Icon = tab.icon;
              const active = isActive(pathname, tab.activePath);
              return (
                <Link
                  key={tab.id}
                  href={tab.path}
                  aria-current={active ? 'page' : undefined}
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
