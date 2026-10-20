'use client';

import { useEffect, useRef, type ReactNode } from 'react';

export default function ChartSettingsDisclosure({ label, children, align = 'left' }: { label: string; children: ReactNode; align?: 'left' | 'right' }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (ref.current?.open && event.target instanceof Node && !ref.current.contains(event.target)) {
        ref.current.open = false;
      }
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && ref.current?.open) {
        ref.current.open = false;
        ref.current.querySelector('summary')?.focus();
      }
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeEscape);
    };
  }, []);
  const positionPanel = () => {
    const details = ref.current;
    if (!details?.open) return;
    const panel = details.querySelector<HTMLElement>('[data-settings-panel]');
    if (!panel) return;
    let left = 8;
    let right = window.innerWidth - 8;
    for (let parent = details.parentElement; parent; parent = parent.parentElement) {
      if (['hidden', 'clip', 'auto', 'scroll'].includes(getComputedStyle(parent).overflowX)) {
        const bounds = parent.getBoundingClientRect();
        left = Math.max(left, bounds.left + 4);
        right = Math.min(right, bounds.right - 4);
      }
    }
    panel.style.maxWidth = `${Math.max(0, right - left)}px`;
    const anchor = details.getBoundingClientRect();
    const width = panel.getBoundingClientRect().width;
    const preferred = align === 'left' ? anchor.left : anchor.right - width;
    panel.style.left = `${Math.max(left, Math.min(preferred, right - width)) - anchor.left}px`;
    panel.style.right = 'auto';
  };
  return <details ref={ref} onToggle={positionPanel} className="relative">
    <summary aria-label={label} title={label}
      className="flex h-5 cursor-pointer list-none items-center rounded border border-border-primary px-1 text-[10px] text-text-secondary hover:text-white [&::-webkit-details-marker]:hidden">▾</summary>
    <div data-settings-panel aria-label={`${label}面板`} className={`absolute ${align === 'left' ? 'left-0' : 'right-0'} top-6 z-50 max-h-[60vh] w-72 max-w-[calc(100vw-40px)] overflow-y-auto rounded-lg border border-border-primary bg-[#0b121d] p-3 text-[11px] shadow-xl`}>
      <p className="mb-2 font-bold text-white">{label}</p>
      {children}
    </div>
  </details>;
}
