'use client';

import { ReactNode, HTMLAttributes } from 'react';
import { clsx } from 'clsx';

type CardVariant = 'default' | 'bordered' | 'elevated';
type CardPadding = 'none' | 'sm' | 'md' | 'lg';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
  padding?: CardPadding;
  hoverable?: boolean;
  children: ReactNode;
}

const variantStyles: Record<CardVariant, string> = {
  default: 'bg-bg-secondary border border-border-primary',
  bordered: 'bg-bg-secondary border-2 border-border-primary',
  elevated: 'bg-bg-secondary border border-border-primary shadow-lg',
};

const paddingStyles: Record<CardPadding, string> = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
};

export default function Card({
  variant = 'default',
  padding = 'md',
  hoverable = false,
  children,
  className,
  ...props
}: CardProps) {
  const styles = clsx(
    'rounded-xl transition-all duration-200',
    variantStyles[variant],
    paddingStyles[padding],
    hoverable && 'hover:shadow-md hover:border-brand-blue/20 cursor-pointer',
    className
  );

  return (
    <div className={styles} {...props}>
      {children}
    </div>
  );
}

// Card子组件
interface CardHeaderProps {
  title?: string;
  subtitle?: string;
  extra?: ReactNode;
  children?: ReactNode;
}

export function CardHeader({ title, subtitle, extra, children }: CardHeaderProps) {
  return (
    <div className="flex items-center justify-between mb-4">
      <div>
        {title && <h3 className="text-sm font-bold text-text-primary">{title}</h3>}
        {subtitle && <p className="text-xs text-text-secondary mt-0.5">{subtitle}</p>}
        {children}
      </div>
      {extra && <div>{extra}</div>}
    </div>
  );
}

interface CardBodyProps {
  children: ReactNode;
  className?: string;
}

export function CardBody({ children, className }: CardBodyProps) {
  return <div className={className}>{children}</div>;
}

interface CardFooterProps {
  children: ReactNode;
  className?: string;
}

export function CardFooter({ children, className }: CardFooterProps) {
  return (
    <div className={clsx('mt-4 pt-4 border-t border-border-primary', className)}>
      {children}
    </div>
  );
}
