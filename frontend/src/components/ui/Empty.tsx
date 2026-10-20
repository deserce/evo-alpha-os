'use client';

import { FileQuestion, Inbox, SearchX } from 'lucide-react';
import { clsx } from 'clsx';

type EmptyType = 'no-data' | 'no-search' | 'error' | 'loading';

interface EmptyProps {
  type?: EmptyType;
  title?: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
}

const typeConfig = {
  'no-data': {
    icon: Inbox,
    title: '暂无数据',
    description: '当前没有任何数据',
  },
  'no-search': {
    icon: SearchX,
    title: '未找到结果',
    description: '没有找到匹配的内容',
  },
  'error': {
    icon: FileQuestion,
    title: '加载失败',
    description: '数据加载出错，请稍后重试',
  },
  'loading': {
    icon: Inbox,
    title: '加载中...',
    description: '正在努力加载数据',
  },
};

export default function Empty({ type = 'no-data', title, description, action }: EmptyProps) {
  const config = typeConfig[type];
  const Icon = config.icon;

  return (
    <div className="flex flex-col items-center justify-center py-12 px-4">
      <div className="w-16 h-16 rounded-full bg-bg-tertiary flex items-center justify-center mb-4">
        <Icon size={32} className="text-text-tertiary" />
      </div>

      <h3 className={clsx(
        'font-bold text-text-primary mb-2',
        type === 'error' ? 'text-lg' : 'text-base'
      )}>
        {title || config.title}
      </h3>

      <p className="text-sm text-text-secondary text-center max-w-md mb-6">
        {description || config.description}
      </p>

      {action && (
        <button
          onClick={action.onClick}
          className="px-4 py-2 bg-brand-blue hover:bg-brand-blue-light text-white text-sm font-medium rounded-lg transition-colors"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
