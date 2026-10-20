import { notFound } from 'next/navigation';

export default async function StockSymbolPage({ params }: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  if (!/^[0-9]{6}$/.test(symbol)) notFound();
  // The persistent stock layout owns the detail view and its interaction state.
  return null;
}
