/**
 * EvoAlpha OS - 首页
 * Alpha 机会雷达
 */

export default function HomePage() {
  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 dark:from-slate-900 dark:to-slate-800">
      {/* 顶部导航 */}
      <nav className="border-b border-slate-200 dark:border-slate-700 bg-white/80 dark:bg-slate-900/80 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <div className="flex items-center space-x-3">
              <div className="w-8 h-8 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg"></div>
              <span className="text-xl font-bold text-slate-900 dark:text-white">
                EvoAlpha OS
              </span>
              <span className="text-sm text-slate-500 dark:text-slate-400 hidden sm:block">
                进化即自由
              </span>
            </div>
            <div className="flex items-center space-x-4">
              <button className="text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white">
                登录
              </button>
              <button className="px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-lg hover:from-blue-700 hover:to-indigo-700 transition">
                开始使用
              </button>
            </div>
          </div>
        </div>
      </nav>

      {/* Hero 区域 */}
      <section className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20">
        <div className="text-center">
          <h1 className="text-5xl font-bold text-slate-900 dark:text-white mb-6">
            发现 Alpha 机会
            <br />
            <span className="bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
              进化即自由
            </span>
          </h1>
          <p className="text-xl text-slate-600 dark:text-slate-300 mb-12 max-w-2xl mx-auto">
            量化筛选做减法，AI Agent 做加法
            <br />
            从 5000+ 只股票中，发现真正的 Alpha 机会
          </p>
        </div>

        {/* 今日机会卡片（占位） */}
        <div className="mt-16">
          <h2 className="text-2xl font-bold text-slate-900 dark:text-white mb-6">
            今日 Alpha 机会（Top 3）
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="bg-white dark:bg-slate-800 rounded-xl p-6 shadow-lg border border-slate-200 dark:border-slate-700"
              >
                <div className="flex items-center justify-between mb-4">
                  <span className="text-sm text-slate-500">股票{i}</span>
                  <span className="text-yellow-500">⭐⭐⭐</span>
                </div>
                <h3 className="text-xl font-bold text-slate-900 dark:text-white mb-2">
                  示例股票
                </h3>
                <p className="text-slate-600 dark:text-slate-300 mb-4">
                  RPS 相对强度领先，突破关键阻力位
                </p>
                <button className="w-full py-2 bg-slate-100 dark:bg-slate-700 text-slate-900 dark:text-white rounded-lg hover:bg-slate-200 dark:hover:bg-slate-600 transition">
                  查看详情
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* 登录提示 */}
        <div className="mt-12 text-center">
          <p className="text-slate-600 dark:text-slate-300">
            登录后查看全部机会 &nbsp;→
          </p>
        </div>
      </section>

      {/* 页脚 */}
      <footer className="border-t border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 mt-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="text-center text-slate-600 dark:text-slate-400">
            <p>© 2025 dlab (Evolution Lab). 进化即自由</p>
          </div>
        </div>
      </footer>
    </main>
  );
}
