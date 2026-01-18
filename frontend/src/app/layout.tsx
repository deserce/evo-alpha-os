// src/app/layout.tsx
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css"; // 确保引用的 CSS 路径正确
import Providers from "./providers";
import MainLayout from "@/components/layout/MainLayout";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "EvoQuant OS v2.0",
  description: "AI-Driven Quantitative Research System",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // 🔴 必须包含 html 标签，并指定 lang
    <html lang="zh-CN" className="dark">
      {/* 🔴 必须包含 body 标签 */}
      <body className={`${inter.className} bg-[#080a0d] text-slate-200 overflow-hidden`}>
        <Providers>
          {/* 这里嵌套我们之前写好的 Webull 风格整体布局 */}
          <MainLayout>
            {children}
          </MainLayout>
        </Providers>
      </body>
    </html>
  );
}