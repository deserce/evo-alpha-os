'use client';

import { usePathname } from 'next/navigation';
import { ReactNode } from 'react';
import TopNavigation from './TopNavigation';
import { CommandPalette } from './CommandPalette';

interface MainLayoutProps {
  children: ReactNode;
}

export default function MainLayout({ children }: MainLayoutProps) {
  const pathname = usePathname();
  if (pathname === "/login") return <main>{children}</main>;

  return (
    <div className="min-h-screen flex flex-col">
      <TopNavigation />
      <main className="flex-1 min-w-0">
        {children}
      </main>
      <CommandPalette />
    </div>
  );
}
