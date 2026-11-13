'use client';

import { useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import StockDetailView from '@/components/stock/StockDetailView';

export default function StockModalPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string>();
  const activeSymbol = selectedSymbol ?? symbol;
  const validSymbol = /^[0-9]{6}$/.test(activeSymbol);

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, [validSymbol]);

  if (!validSymbol) return null;

  return (
    <dialog
      ref={dialog}
      aria-label="个股详情"
      className="fixed inset-0 m-auto max-h-none w-[min(96vw,1760px)] max-w-none overflow-hidden rounded-xl border border-border-primary bg-bg-primary text-text-primary shadow-2xl backdrop:bg-black/70"
      onCancel={(event) => { event.preventDefault(); router.back(); }}
    >
      <StockDetailView symbol={activeSymbol} onClose={() => router.back()} onSelectStock={setSelectedSymbol} />
    </dialog>
  );
}
