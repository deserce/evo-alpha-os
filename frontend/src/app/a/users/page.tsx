'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useState } from 'react';
import { ShieldCheck, UserPlus, Users } from 'lucide-react';
import { AppShell, Badge, PageHeader, Panel } from '@/components/workspace/DashboardPrimitives';

interface User {
  id: number;
  email: string;
  role: 'admin' | 'user';
  active: boolean;
  last_login_at: string | null;
}

async function request<T>(path = '', method = 'GET', body?: object): Promise<T> {
  const response = await fetch('/api/v1/users' + path, {
    method,
    cache: 'no-store',
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: '请先登录。',
      403: '此页面仅管理员可访问。',
      409: '邮箱已登记，或当前账户状态无法修改。',
      422: '请检查输入，或刷新后重新选择自选分组。',
      502: '站内变更已保存，Cloudflare 同步失败，请点击重新同步。',
      503: '用户管理尚未配置。',
    };
    throw new Error(messages[response.status] ?? '请求失败，请稍后重试。');
  }
  return response.json();
}

function UserManagement() {
  const [email, setEmail] = useState('');
  const client = useQueryClient();
  const users = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => request<User[]>(),
    retry: false,
    gcTime: 0,
  });
  const change = useMutation({
    mutationFn: ({ path, method, body }: { path: string; method: string; body?: object }) =>
      request(path, method, body),
    onError: () => client.invalidateQueries({ queryKey: ['admin-users'] }),
    onSuccess: async (_, { method }) => {
      if (method === 'POST') setEmail('');
      await client.invalidateQueries({ queryKey: ['admin-users'] });
    },
  });

  return (
    <>
      <PageHeader title="用户管理" icon={Users}>
        {users.data && `${users.data.length} 个账户 · ${users.data.filter((user) => user.active).length} 个已启用`}
      </PageHeader>
      <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_280px]">
      <Panel title="账户列表" icon={ShieldCheck}>
      {users.isPending && <p className="p-3 text-xs text-text-secondary">正在读取用户…</p>}
      {users.error && <p role="alert" className="p-3 text-xs text-danger">{users.error.message}</p>}
      {users.data && <>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-xs [&_td]:p-3 [&_th]:p-3">
            <thead className="bg-bg-secondary text-text-secondary"><tr><th>邮箱</th><th>角色</th><th>状态</th><th>最近验证（UTC）</th><th>操作</th></tr></thead>
            <tbody>
              {users.data.map((user) => (
                <tr key={user.id} className="border-t border-border-secondary hover:bg-bg-secondary/60">
                  <td>{user.email}</td><td>{user.role === 'admin' ? '管理员' : '普通用户'}</td>
                  <td><Badge status={!user.active ? 'stale' : user.last_login_at ? 'ok' : undefined}>{!user.active ? '已停用' : user.last_login_at ? '已启用' : '待首次登录'}</Badge></td>
                  <td>{user.last_login_at ?? '—'}</td>
                  <td>{user.role === 'admin' ? <span className="text-text-tertiary">—</span> : <button type="button" className="rounded border border-border-primary px-2 py-1 hover:text-brand-blue-light disabled:opacity-50"
                    disabled={change.isPending}
                    onClick={() => change.mutate({
                      path: '/' + user.id, method: 'PATCH', body: { active: !user.active },
                    })}>{user.active ? '停用' : '启用'}</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>}
      </Panel>
      {users.data && <Panel title="预授权用户" icon={UserPlus}>
        <p className="px-3 pt-3 text-xs text-text-secondary">登记邮箱后，受邀用户还需通过访问入口的身份验证。</p>
        <form className="grid gap-3 p-3" onSubmit={(event) => {
          event.preventDefault();
          change.mutate({ path: '', method: 'POST', body: { email } });
        }}>
          <label className="grid gap-1 text-sm">
            邮箱
            <input className="min-w-0 rounded-md border border-border-primary bg-bg-primary px-3 py-2" type="email" required
              maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <button className="rounded-md border border-brand-blue/50 bg-brand-blue/15 px-4 py-2 text-xs font-bold text-brand-blue-light disabled:opacity-50"
            disabled={change.isPending}>预授权</button>
        </form>
        <button type="button" disabled={change.isPending} className="px-3 pb-3 text-xs text-accent" onClick={() => change.mutate({ path: '/sync-access', method: 'POST' })}>重新同步 Cloudflare</button>
        {change.error && <p role="alert" className="px-3 pb-3 text-xs text-danger">{change.error.message}</p>}
        {change.isSuccess && <p role="status" className="px-3 pb-3 text-xs text-success">已保存。</p>}
      </Panel>}
      </div>
    </>
  );
}

interface Profile extends User {
  nickname: string;
  landing: 'dashboard' | 'watchlist';
  group_id: number | null;
}

const fieldClass = 'rounded-md border border-border-primary bg-bg-primary px-3 py-2';

function PersonalSettings({ profile }: { profile: Profile }) {
  const client = useQueryClient();
  const [nickname, setNickname] = useState(profile.nickname);
  const [landing, setLanding] = useState(profile.landing);
  const [group, setGroup] = useState(profile.group_id ?? 0);
  const groups = useQuery({
    queryKey: ['personal-stocks', profile.id],
    queryFn: async () => {
      const response = await fetch('/api/v1/personal-stocks/groups', { cache: 'no-store' });
      if (!response.ok) throw new Error('无法读取自选分组。');
      return response.json() as Promise<{ id: number; name: string }[]>;
    },
    retry: false,
    gcTime: 0,
  });
  const selected = groups.data?.some((item) => item.id === group) ? group : 0;
  const save = useMutation({
    mutationFn: () => request('/me', 'PATCH', { nickname, landing, group_id: selected || null }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['navigation-identity'] }),
  });
  return <Panel title="个人设置" icon={Users}>
    <form className="grid max-w-lg gap-4 p-4 text-sm" onSubmit={(event) => {
      event.preventDefault();
      save.mutate();
    }}>
      <p>{profile.email} · {profile.role === 'admin' ? '管理员' : '普通用户'}</p>
      <label className="grid gap-1">昵称
        <input className={fieldClass} maxLength={32} value={nickname} onChange={(event) => setNickname(event.target.value)} />
      </label>
      <label className="grid gap-1">登录后默认页面
        <select className={fieldClass} value={landing} onChange={(event) => setLanding(event.target.value === 'watchlist' ? 'watchlist' : 'dashboard')}>
          <option value="dashboard">仪表盘</option><option value="watchlist">我的自选</option>
        </select>
      </label>
      <label className="grid gap-1">默认自选分组
        <select className={fieldClass} disabled={!groups.data} value={selected} onChange={(event) => setGroup(Number(event.target.value))}>
          <option value={0}>自动选择首个分组</option>
          {groups.data?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      {groups.error && <p role="alert" className="text-danger">{groups.error.message}</p>}
      {save.error && <p role="alert" className="text-danger">{save.error.message}</p>}
      {save.isSuccess && <p role="status" className="text-success">设置已保存。</p>}
      <button disabled={save.isPending || !groups.data} className="w-fit rounded border border-brand-blue/50 bg-brand-blue/15 px-4 py-2 text-brand-blue-light disabled:opacity-50">
        {save.isPending ? '正在保存…' : '保存设置'}
      </button>
    </form>
  </Panel>;
}

export default function UsersPage() {
  const [tab, setTab] = useState<'profile' | 'admin'>();
  const profile = useQuery({
    queryKey: ['navigation-identity'],
    queryFn: () => request<Profile>('/me'),
    retry: false,
  });
  const isAdmin = profile.data?.role === 'admin';
  const managing = isAdmin && tab !== 'profile';
  return <AppShell>
    <PageHeader title="账户中心" icon={Users} />
    {profile.isPending && <p>正在读取账户…</p>}
    {profile.error && <p role="alert">{profile.error.message}</p>}
    {profile.data === null && <p role="alert">当前账户不可用。</p>}
    {profile.data && <>
      {isAdmin && <div role="group" aria-label="账户中心" className="mb-3 flex flex-wrap gap-2">
        <button type="button" className={fieldClass} aria-pressed={!managing} onClick={() => setTab('profile')}>个人设置</button>
        <button type="button" className={fieldClass} aria-pressed={managing} onClick={() => setTab('admin')}>用户管理</button>
        <Link href="/a/data" className={fieldClass}>数据管理</Link>
        <Link href="/a/dashboard#ai-brief" className={fieldClass}>AI 实验室</Link>
      </div>}
      {managing ? <UserManagement /> : <PersonalSettings key={profile.data.id} profile={profile.data} />}
    </>}
  </AppShell>;
}
