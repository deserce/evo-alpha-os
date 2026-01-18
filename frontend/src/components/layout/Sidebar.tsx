'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useGlobalStore } from '@/lib/store';
import { 
  LayoutDashboard, LineChart, PieChart, FileText, 
  FlaskConical, ChevronLeft, ChevronRight, Settings 
} from 'lucide-react';
import { clsx } from 'clsx';

const NAV_ITEMS = [
  { name: '市场指挥', path: '/market', icon: LayoutDashboard },
  { name: '个股作战', path: '/stock', icon: LineChart },
  { name: '板块中心', path: '/sector', icon: PieChart },
  { name: '投研报告', path: '/report', icon: FileText },
  { name: '量化工厂', path: '/quant', icon: FlaskConical },
];

export default function Sidebar() {
  const pathname = usePathname();
  const { isSidebarExpanded, toggleSidebar } = useGlobalStore();

  return (
    <aside className={clsx(
      "h-full bg-[#111417] border-r border-[#1e2226] flex flex-col transition-all duration-500 ease-in-out shrink-0 shadow-2xl z-50",
      // 竖向文字模式下，展开宽度不需要太大，w-20 (80px) 足够，且非常有终端感
      isSidebarExpanded ? "w-20" : "w-14" 
    )}>
      
      {/* 1. 展开/收缩按钮 - 统一色调，去掉白色方块 */}
      <div className="h-14 flex items-center justify-center border-b border-[#1e2226] bg-[#080a0d]/20 shrink-0">
        <button 
          onClick={toggleSidebar} 
          className="p-2 text-[#8a8d91] hover:text-[#1861ff] hover:bg-[#1861ff]/10 rounded-md transition-all group"
        >
          {isSidebarExpanded ? (
            <ChevronLeft size={20} className="group-hover:-translate-x-0.5 transition-transform" />
          ) : (
            <ChevronRight size={20} className="group-hover:translate-x-0.5 transition-transform" />
          )}
        </button>
      </div>

      {/* 2. 核心导航区域 - justify-evenly 实现五个板块均匀分布 */}
      <nav className="flex-1 flex flex-col justify-evenly py-6">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname.startsWith(item.path);
          return (
            <Link 
              key={item.path} 
              href={item.path} 
              className={clsx(
                "flex flex-col items-center gap-2.5 transition-all duration-300 relative group py-2",
                isActive 
                  ? "text-[#1861ff]" 
                  : "text-[#8a8d91] hover:text-[#d1d1d1]"
              )}
            >
              {/* 图标 24px */}
              <item.icon 
                size={24} 
                className={clsx(
                  "shrink-0 transition-all duration-300",
                  isActive ? "scale-110 drop-shadow-[0_0_8px_rgba(24,97,255,0.4)]" : "group-hover:scale-110"
                )} 
              />
              
              {/* 文字标签 - 展开后竖向排列 */}
              {isSidebarExpanded && (
                <span 
                  className="text-[11px] font-bold tracking-[0.2em] whitespace-nowrap animate-in fade-in slide-in-from-bottom-1 duration-500"
                  style={{ 
                    writingMode: 'vertical-rl', // 开启竖排模式
                    textOrientation: 'mixed' 
                  }}
                >
                  {item.name}
                </span>
              )}

              {/* 激活指示光条 - 放在侧边 */}
              {isActive && (
                <div className="absolute right-0 top-1/4 bottom-1/4 w-0.5 bg-[#1861ff] shadow-[0_0_12px_rgba(24,97,255,0.8)] rounded-l-full" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* 3. 底部设置按钮 - 统一色调 */}
      <div className="h-14 border-t border-[#1e2226] flex items-center justify-center bg-[#080a0d]/40 shrink-0">
        <button className="p-2 text-[#30363d] hover:text-[#1861ff] transition-all group">
          <Settings 
            size={20} 
            className="transition-transform group-hover:rotate-90 duration-700" 
          />
        </button>
      </div>
    </aside>
  );
}