'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import StockWorkspace from '@/components/stock/StockWorkspace';
import MyList from './MyList';

function WatchlistWorkspace() {
  const mine = useSearchParams().get('view') === 'mine';
  const listSwitcher = (
    <div className="flex items-center gap-3">
      <h1 className="evo-page-title"><span className="evo-page-index">06</span> 自选股</h1>
      <div role="group" aria-label="自选股列表" className="stock-sidebar-tabs inline-flex items-center gap-1">
      {([{ label: '共享', mine: false }, { label: '个人', mine: true }]).map((view) => (
        <button key={view.label} type="button" aria-pressed={view.mine === mine}
          onClick={() => window.history.replaceState(null, '', view.mine ? '/a/watchlist?view=mine' : '/a/watchlist')}
          className={`px-3 ${view.mine === mine
            ? 'text-brand-blue-light'
            : 'text-text-secondary hover:bg-bg-secondary hover:text-text-primary'}`}>
          {view.label}
        </button>
      ))}
      </div>
    </div>
  );
  return mine ? <MyList listSwitcher={listSwitcher} /> : (
    <StockWorkspace listSwitcher={listSwitcher} />
  );
}

export default function WatchlistPage() {
  return <Suspense><WatchlistWorkspace /></Suspense>;
}
