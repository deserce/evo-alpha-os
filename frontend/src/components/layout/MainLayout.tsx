'use client';
import { useGlobalStore } from '@/lib/store';
import Sidebar from './Sidebar';
import Topbar from './Topbar';

export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#080a0d]">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 h-full relative">
        <Topbar />
        <main className="flex-1 overflow-hidden relative bg-[#080a0d]">
          {children}
        </main>
      </div>
    </div>
  );
}