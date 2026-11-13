'use client';

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { CalendarDays } from 'lucide-react';

export function TopbarTradeDate({ value, dates, onChange }: {
  value?: string;
  dates: string[];
  onChange: (date: string) => void;
}) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const target = document.getElementById('topbar-trade-date');
    queueMicrotask(() => setSlot(target));
  }, []);

  if (!slot || (!dates.length && !value)) return null;

  return createPortal(
    <label className="inline-flex h-8 items-center gap-1 rounded-lg border border-border-primary bg-bg-primary px-1.5 text-xs text-text-primary sm:px-2">
      <CalendarDays size={14} aria-hidden="true" className="hidden sm:block" />
      <select aria-label="交易日" value={value || dates[0]} onChange={(event) => onChange(event.target.value)}
        className="bg-bg-primary font-mono outline-none focus-visible:ring-1 focus-visible:ring-brand-blue">
        {value && !dates.includes(value) && <option value={value}>{value}</option>}
        {dates.map((date) => <option key={date} value={date}>{date}</option>)}
      </select>
    </label>,
    slot,
  );
}
