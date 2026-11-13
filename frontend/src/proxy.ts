import { NextRequest, NextResponse } from 'next/server';

export async function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  if (process.env.NEXT_PUBLIC_EVOALPHA_HIDE_REVIEW === '1' &&
      (pathname === '/a/report' || pathname.startsWith('/a/report/'))) {
    return new NextResponse('复盘页面暂未开放。', { status: 404 });
  }

  if (process.env.EVOALPHA_LOGIN_TEST !== '1') return NextResponse.next();

  const response = await fetch('http://127.0.0.1:8301/api/v1/users/me', {
    headers: { cookie: request.headers.get('cookie') ?? '' },
    cache: 'no-store',
    signal: AbortSignal.timeout(3000),
  }).catch(() => null);

  if (response?.ok) {
    const { landing, role } = await response.json() as { landing?: string; role: string };
    if (role !== 'admin' && ['/a/data', '/a/portfolio'].some(
      (path) => pathname === path || pathname.startsWith(path + '/'),
    )) {
      return NextResponse.redirect(new URL('/a/users', request.url));
    }
    if ((pathname === '/' || pathname === '/a') && landing === 'watchlist') {
      return NextResponse.redirect(new URL('/a/watchlist?view=mine', request.url));
    }
    return NextResponse.next();
  }

  const login = new URL('/login', 'http://127.0.0.1:3001');
  login.searchParams.set('next', request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = { matcher: ['/', '/a/:path*'] };
