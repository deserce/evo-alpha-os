'use client';

import { FormEvent, useState } from 'react';
import { LockKeyhole } from 'lucide-react';
import { PageHeader, Panel } from '@/components/workspace/DashboardPrimitives';

export default function LoginPage() {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/v1/users/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: fields.get('email'), password: fields.get('password') }),
      });
      if (!response.ok) {
        setError(response.status === 401 ? '邮箱或密码有误。' : '登录服务暂时不可用。');
        return;
      }
      const { landing } = await response.json() as { landing?: string };
      const fallback = landing === 'watchlist' ? '/a/watchlist?view=mine' : '/a/dashboard';
      const next = new URLSearchParams(window.location.search).get('next');
      window.location.assign(next && next !== '/a' && /^\/a(?:\/|\?|$)/.test(next) && !next.includes('\\') ? next : fallback);
    } catch {
      setError('连接登录服务失败。');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md p-4">
      <PageHeader title="登录 EvoAlpha OS" icon={LockKeyhole} />
      <Panel title="本地测试账户" icon={LockKeyhole}>
        <form className="grid gap-4 p-4 text-sm" onSubmit={submit}>
          <label className="grid gap-1">邮箱
            <input name="email" type="email" required autoComplete="username" maxLength={254}
              className="rounded border border-border-primary bg-bg-primary p-2" />
          </label>
          <label className="grid gap-1">密码
            <input name="password" type="password" required autoComplete="current-password" maxLength={128}
              className="rounded border border-border-primary bg-bg-primary p-2" />
          </label>
          {error && <p role="alert" className="text-danger">{error}</p>}
          <button disabled={busy} className="rounded border border-brand-blue/50 bg-brand-blue/15 p-2 text-brand-blue-light disabled:opacity-50">
            {busy ? '正在登录…' : '登录'}
          </button>
        </form>
      </Panel>
    </div>
  );
}
