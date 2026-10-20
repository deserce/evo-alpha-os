'use client';

import { useLayoutEffect, useRef } from 'react';
import type { UIEvent } from 'react';
import { useGlobalStore } from '../../lib/store';

/** Persist on navigation, never on each scroll event. Restore after async content grows. */
export function useSidebarScroll(key: string, ready = true) {
  const ref = useRef<HTMLDivElement>(null);
  const userScrolled = useRef(false);
  const lastTop = useRef(0);
  const restorationTop = useRef<number | null>(null);
  const activeObserver = useRef<ResizeObserver | null>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    // Async report sections must not let browser anchoring shift a restored position.
    const previousOverflowAnchor = element.style.overflowAnchor;
    element.style.overflowAnchor = 'none';
    const saved = useGlobalStore.getState().stockSidebarUi.scrollPositions[key] ?? 0;
    userScrolled.current = false;
    lastTop.current = saved;
    restorationTop.current = null;
    let restored = saved === 0;
    let frame = 0;
    let attempts = 0;
    let observer: ResizeObserver | undefined;
    const restore = () => {
      frame = 0;
      if (!ready || userScrolled.current) return;
      element.scrollTop = saved;
      restorationTop.current = element.scrollTop;
      restored = Math.abs(element.scrollTop - saved) < 1;
      if (restored || ++attempts >= 32) observer?.disconnect();
    };
    const schedule = () => {
      if (!frame && !userScrolled.current && attempts < 32) frame = requestAnimationFrame(restore);
    };
    if (ready) {
      restore();
      schedule();
      if (!restored && typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(schedule);
        activeObserver.current = observer;
        observer.observe(element);
        for (const child of Array.from(element.children).slice(0, 32)) observer.observe(child);
      }
    }
    return () => {
      if (frame) cancelAnimationFrame(frame);
      observer?.disconnect();
      if (activeObserver.current === observer) activeObserver.current = null;
      if (userScrolled.current || restored) {
        useGlobalStore.getState().setSidebarScroll(key, lastTop.current);
      }
      element.style.overflowAnchor = previousOverflowAnchor;
    };
  }, [key, ready]);

  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    if (!ready || event.currentTarget.clientHeight === 0) return;
    const top = event.currentTarget.scrollTop;
    if (restorationTop.current !== null && Math.abs(top - restorationTop.current) < 1) return;
    // Programmatic restoration also fires scroll; only treat a changed position as user input.
    if (Math.abs(top - lastTop.current) < 1) return;
    userScrolled.current = true;
    lastTop.current = top;
    activeObserver.current?.disconnect();
  };
  return { ref, onScroll };
}
