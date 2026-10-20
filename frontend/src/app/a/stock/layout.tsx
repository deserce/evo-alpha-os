'use client';

import type { ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import StockDetailView from '@/components/stock/StockDetailView';

// Keep the chart and assistant mounted while the dynamic stock segment changes.
export default function StockLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const match = /^\/a\/stock\/([0-9]{6})\/?$/.exec(pathname);
  if (!match) return children;

  return <><StockDetailView symbol={match[1]} />{children}</>;
}
