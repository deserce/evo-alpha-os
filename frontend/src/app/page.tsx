import { redirect } from 'next/navigation';

export default function RootPage() {
  // 访问根域名自动跳到 /market
  redirect('/market');
}