// frontend/src/app/providers.tsx
'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';

export default function Providers({ children }: { children: React.ReactNode }) {
  // 创建一个 queryClient 实例
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false, // 窗口切回来时不自动刷新 (防止K线乱跳)
        staleTime: 1000 * 60 * 5,    // 数据 5 分钟内认为是新鲜的
        retry: 1,                    // 网络错误时重试 1 次
        retryDelay: 1000,            // 重试间隔 1 秒
      },
    },
  }));

  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}