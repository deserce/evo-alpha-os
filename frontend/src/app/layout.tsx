import type { Metadata } from "next";
import "./globals.css";
import Providers from "./providers";
import MainLayout from "@/components/layout/MainLayout";
import ErrorBoundary from "@/components/ui/ErrorBoundary";

export const metadata: Metadata = {
  title: "EvoAlpha OS",
  description: "数据驱动的Alpha机会发现平台 - 进化即自由",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="bg-bg-primary text-text-primary">
        <Providers>
          <ErrorBoundary>
            <MainLayout>
              {children}
            </MainLayout>
          </ErrorBoundary>
        </Providers>
      </body>
    </html>
  );
}
