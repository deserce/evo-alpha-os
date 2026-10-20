'use client';

import { Loader2 } from 'lucide-react';
import { clsx } from 'clsx';

interface LoadingProps {
  size?: 'sm' | 'md' | 'lg';
  text?: string;
  fullScreen?: boolean;
}

const sizeStyles = {
  sm: 'w-4 h-4',
  md: 'w-8 h-8',
  lg: 'w-12 h-12',
};

const textSizeStyles = {
  sm: 'text-xs',
  md: 'text-sm',
  lg: 'text-base',
};

export default function Loading({ size = 'md', text, fullScreen = false }: LoadingProps) {
  const content = (
    <div className="flex flex-col items-center justify-center gap-3">
      <Loader2 className={clsx(sizeStyles[size], 'animate-spin text-brand-blue')} />
      {text && (
        <p className={clsx('text-text-secondary font-medium', textSizeStyles[size])}>
          {text}
        </p>
      )}
    </div>
  );

  if (fullScreen) {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-bg-primary/80 backdrop-blur-sm z-50">
        {content}
      </div>
    );
  }

  return content;
}

// 卡片骨架屏加载组件
interface CardSkeletonProps {
  count?: number;
}

export function CardSkeleton({ count = 1 }: CardSkeletonProps) {
  return (
    <div className="grid grid-cols-3 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="bg-bg-secondary border border-border-primary rounded-xl p-5 animate-pulse"
        >
          <div className="h-4 bg-bg-tertiary rounded w-20 mb-3"></div>
          <div className="h-8 bg-bg-tertiary rounded w-32 mb-2"></div>
          <div className="h-5 bg-bg-tertiary rounded w-24"></div>
        </div>
      ))}
    </div>
  );
}

// 表格骨架屏加载组件
interface TableSkeletonProps {
  rows?: number;
  columns?: number;
}

export function TableSkeleton({ rows = 5, columns = 4 }: TableSkeletonProps) {
  return (
    <div className="space-y-2">
      <div className="h-10 bg-bg-tertiary rounded animate-pulse"></div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-4">
          {Array.from({ length: columns }).map((_, j) => (
            <div key={j} className="h-8 bg-bg-secondary rounded flex-1 animate-pulse"></div>
          ))}
        </div>
      ))}
    </div>
  );
}
