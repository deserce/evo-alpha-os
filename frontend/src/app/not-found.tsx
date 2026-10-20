import Link from 'next/link';
import { Compass } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4">
      <div className="text-center">
        <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full border border-brand-blue/30 bg-brand-blue/10">
          <Compass size={36} className="text-brand-blue-light" />
        </div>
        <h1 className="text-3xl font-black text-white">404</h1>
        <p className="mt-2 text-sm text-text-secondary">
          页面不存在或已迁移，请从左侧导航重新进入。
        </p>
        <Link
          href="/a/quant"
          className="mt-6 inline-flex h-10 items-center gap-2 rounded-lg border border-brand-blue/50 bg-brand-blue/15 px-6 text-xs font-black text-brand-blue-light transition-colors hover:bg-brand-blue/25"
        >
          返回 Dashboard
        </Link>
      </div>
    </div>
  );
}
