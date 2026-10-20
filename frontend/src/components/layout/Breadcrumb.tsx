'use client';

import { usePathname, useParams } from 'next/navigation';
import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { useMemo } from 'react';

export default function Breadcrumb() {
  const pathname = usePathname();
  const params = useParams();

  const breadcrumb = useMemo(() => {
    const segments = pathname.split('/').filter(Boolean);

    if (segments.length === 0) {
      return [{ label: '首页', path: '/' }];
    }

    const items = [{ label: 'EVOQUANT', path: '/' }];

    // 市场层级
    if (segments[0] === 'a') {
      items.push({ label: 'A股市场', path: '/a' });

      // 功能模块层级
      if (segments[1]) {
        const moduleMap: Record<string, string> = {
          market: '市场指挥部',
          stock: '个股作战',
          sector: '板块中心',
          report: '投研报告',
          quant: '量化工厂',
        };

        if (moduleMap[segments[1]]) {
          const modulePath = `/a/${segments[1]}`;
          items.push({ label: moduleMap[segments[1]], path: modulePath });

          // 个股详情层级
          if (segments[1] === 'stock' && segments[2]) {
            const symbol = (segments[2] as string).toUpperCase();
            items.push({ label: symbol, path: `/a/stock/${segments[2]}` });
          }
        }
      }
    } else if (segments[0] === 'web3') {
      items.push({ label: 'Web3', path: '/web3' });
    } else if (segments[0] === 'us') {
      items.push({ label: '美股', path: '/us' });
    } else if (segments[0] === 'hk') {
      items.push({ label: '港股', path: '/hk' });
    }

    return items;
  }, [pathname]);

  return (
    <div className="flex items-center gap-2 px-6 py-3 border-b border-border-primary bg-bg-secondary/50">
      {breadcrumb.map((item, index) => (
        <div key={item.path} className="flex items-center">
          {index > 0 && (
            <ChevronRight size={12} className="mx-2 text-text-tertiary" />
          )}
          {index === breadcrumb.length - 1 ? (
            <span className="text-xs font-bold text-text-primary">
              {item.label}
            </span>
          ) : (
            <Link
              href={item.path}
              className="text-xs font-medium text-text-secondary hover:text-text-primary transition-colors"
            >
              {item.label}
            </Link>
          )}
        </div>
      ))}
    </div>
  );
}
