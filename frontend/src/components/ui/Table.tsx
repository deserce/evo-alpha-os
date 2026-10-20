'use client';

import {
  ReactNode,
  useState,
  useMemo,
} from 'react';
import { clsx } from 'clsx';
import { ChevronUp, ChevronDown, ChevronsDownUp } from 'lucide-react';

type TableSize = 'sm' | 'md' | 'lg';

export interface Column<T> {
  key: string;
  title: string;
  width?: string;
  align?: 'left' | 'center' | 'right';
  sortable?: boolean;
  render?: (value: any, record: T, index: number) => ReactNode;
}

interface TableProps<T> {
  columns: Column<T>[];
  data: T[];
  size?: TableSize;
  striped?: boolean;
  bordered?: boolean;
  hoverable?: boolean;
  onRowClick?: (record: T, index: number) => void;
  rowClassName?: (record: T, index: number) => string;
  emptyText?: string;
  loading?: boolean;
}

type SortDirection = 'asc' | 'desc' | null;

const sizeStyles: Record<TableSize, string> = {
  sm: 'text-xs',
  md: 'text-sm',
  lg: 'text-base',
};

export default function Table<T extends Record<string, any>>({
  columns,
  data,
  size = 'sm',
  striped = true,
  bordered = false,
  hoverable = true,
  onRowClick,
  rowClassName,
  emptyText = '暂无数据',
  loading = false,
}: TableProps<T>) {
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>(null);

  // 排序逻辑
  const sortedData = useMemo(() => {
    if (!sortColumn || !sortDirection) return data;

    return [...data].sort((a, b) => {
      const aVal = a[sortColumn];
      const bVal = b[sortColumn];

      if (aVal === bVal) return 0;

      const comparison = aVal > bVal ? 1 : -1;
      return sortDirection === 'asc' ? comparison : -comparison;
    });
  }, [data, sortColumn, sortDirection]);

  // 处理排序
  const handleSort = (column: Column<T>) => {
    if (!column.sortable) return;

    if (sortColumn === column.key) {
      // 切换排序方向
      if (sortDirection === 'asc') {
        setSortDirection('desc');
      } else if (sortDirection === 'desc') {
        setSortDirection(null);
        setSortColumn(null);
      }
    } else {
      setSortColumn(column.key);
      setSortDirection('asc');
    }
  };

  // 获取对齐样式
  const getAlignClass = (align?: 'left' | 'center' | 'right') => {
    switch (align) {
      case 'center':
        return 'text-center';
      case 'right':
        return 'text-right';
      default:
        return 'text-left';
    }
  };

  // 渲染排序图标
  const renderSortIcon = (column: Column<T>) => {
    if (!column.sortable) return null;

    if (sortColumn !== column.key) {
      return <ChevronsDownUp size={12} className="text-text-tertiary" />;
    }

    if (sortDirection === 'asc') {
      return <ChevronUp size={12} className="text-brand-blue" />;
    }

    if (sortDirection === 'desc') {
      return <ChevronDown size={12} className="text-brand-blue" />;
    }

    return null;
  };

  return (
    <div className={clsx('w-full overflow-x-auto', bordered && 'border border-border-primary rounded-lg')}>
      <table className={clsx('w-full', sizeStyles[size])}>
        {/* 表头 */}
        <thead className={clsx('bg-bg-tertiary border-b border-border-primary', size === 'sm' && 'text-[11px] font-bold uppercase tracking-wider')}>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                className={clsx(
                  'px-4 py-3 font-medium text-text-secondary whitespace-nowrap',
                  getAlignClass(column.align),
                  column.sortable && 'cursor-pointer hover:bg-bg-hover transition-colors select-none'
                )}
                style={{ width: column.width }}
                onClick={() => handleSort(column)}
              >
                <div className={clsx('flex items-center gap-2', column.align === 'center' && 'justify-center', column.align === 'right' && 'justify-end')}>
                  <span>{column.title}</span>
                  {renderSortIcon(column)}
                </div>
              </th>
            ))}
          </tr>
        </thead>

        {/* 表体 */}
        <tbody className={clsx('divide-y divide-border-primary', striped && 'bg-bg-secondary even:bg-bg-tertiary/50')}>
          {loading ? (
            <tr>
              <td colSpan={columns.length} className="px-4 py-8 text-center text-text-secondary">
                加载中...
              </td>
            </tr>
          ) : sortedData.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-4 py-8 text-center text-text-secondary">
                {emptyText}
              </td>
            </tr>
          ) : (
            sortedData.map((record, rowIndex) => (
              <tr
                key={rowIndex}
                className={clsx(
                  'transition-colors',
                  hoverable && onRowClick && 'hover:bg-bg-hover cursor-pointer',
                  rowClassName && rowClassName(record, rowIndex)
                )}
                onClick={() => onRowClick?.(record, rowIndex)}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={clsx('px-4 py-3 text-text-primary', getAlignClass(column.align))}
                  >
                    {column.render
                      ? column.render(record[column.key], record, rowIndex)
                      : record[column.key]}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
