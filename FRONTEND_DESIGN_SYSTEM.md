
对，我也觉得前面生成的图更像“概念展示板”，还没有到真正产品级 UI。我们现在要换思路： **先把每一页的产品需求、信息层级、组件结构、数据接口和技术栈定下来** ，再去做高保真设计或直接开发。

我建议前端统一选择：

> **Next.js App Router + TypeScript + Tailwind CSS + shadcn/ui + ECharts/Recharts + Lightweight Charts + TanStack Table**

理由很简单：Vercel 对 Next.js 是一等支持，Next.js 可以直接在 `app/api/` 下写 Route Handler，Vercel 会部署为 Functions；Vercel 也支持多种框架，但我们这种“数据看板 + API 查询 + AI 按需调用 + 未来会员系统”的形态，用 Next.js 最顺。([Vercel](https://vercel.com/docs/frameworks?utm_source=chatgpt.com "Frameworks on Vercel"))

---

# 一、先确定整体技术选型

## 1. 推荐主框架

```text
Next.js App Router
React
TypeScript
Tailwind CSS
shadcn/ui
TanStack Table
ECharts / Recharts
Lightweight Charts
Zustand / TanStack Query
```

## 2. 为什么不用纯 Vite？

Vite 当然也可以部署到 Vercel，Vercel 官方也支持 Vite 生态。([Vercel](https://vercel.com/docs/frameworks/frontend/vite?utm_source=chatgpt.com "Vite on Vercel"))
但如果我们用 Vite，就需要额外维护后端 API 服务；而 Next.js 可以把轻量 API、页面、鉴权、AI 调用、数据查询放在一个项目里，前期开发效率更高。

## 3. 为什么重计算不放在 Vercel？

Vercel Functions 适合服务端代码、API、数据库查询、AI 调用等 I/O 型任务，官方定位也是无需管理服务器的 server-side code。([Vercel](https://vercel.com/docs/functions?utm_source=chatgpt.com "Vercel Functions"))
但我们的 RPS、市场强度、行业强度、结构信号、买点扫描属于批量量化计算，最好继续放在本地 MBP / Python 数据工厂里。前端只读取云端结果。

所以整体架构仍然是：

```text
Python 本地数据工厂
    ↓
SQLite / Parquet / CSV
    ↓
Cloudflare R2 / 对象存储
    ↓
CockroachDB / Neon / Supabase Postgres
    ↓
Next.js API Routes
    ↓
Next.js 前端页面
```

Vercel Marketplace 也提供 Neon、Supabase、Aurora Postgres、Upstash Redis 等存储集成选择。([Vercel](https://vercel.com/docs/marketplace-storage?utm_source=chatgpt.com "Storage on Vercel Marketplace"))
如果后续需要存图片、报告附件、导入文件，也可以考虑对象存储，Vercel Blob 官方定位就是面向图片、视频、文件等对象存储场景。([Vercel](https://vercel.com/docs/storage?utm_source=chatgpt.com "Vercel Storage overview"))

---

# 二、产品 UI 总方向重新定义

我们不要再做“九宫格大拼图”式效果，而是要做真正可用的应用。

## 1. 视觉方向

```text
深色模式为主
现代金融终端感
Linear 式简洁
少量科技蓝 / 紫色高亮
卡片式布局
状态标签明确
图表和表格结合
每一页都围绕一个决策问题设计
```

## 2. 页面体验原则

```text
先结论，后数据
先状态，后指标
先筛选，后展开
先全局，后个股
先行动建议，后复杂解释
```

## 3. 设计目标

每个页面都要让用户回答一个核心问题：

| 页面           | 用户核心问题                     |
| -------------- | -------------------------------- |
| Dashboard      | 今天整体该怎么看？               |
| Market         | 今天能不能做？                   |
| Industries     | 主战场在哪里？                   |
| Alpha Pool     | 哪些机会最值得盯？               |
| Research       | 我研究的产业池有没有被市场验证？ |
| Stock War Room | 这只股票现在处于什么阶段？       |
| Signals        | 今天有哪些买点触发？             |
| Portfolio      | 我的持仓有没有风险？             |
| Review         | 我的系统有没有进化？             |
| Settings       | 数据源、策略参数、权限怎么配置？ |

---

# 三、页面 01：Dashboard / 总览

## 页面目标

Dashboard 不是展示所有数据，而是每天打开后的“作战简报”。

它回答：

```text
今天能不能做？
主线在哪里？
哪些股票进入准备池？
哪些股票触发买点？
持仓有没有风险？
研究池有没有新变化？
```

## 推荐布局

```text
顶部导航栏
左侧 Sidebar
主内容区 12 栅格布局
```

### 第一行：全局状态卡片

4 张核心卡片：

1. **市场状态**
   * M3 偏强
   * 市场强度分：78
   * 建议仓位：40%-70%
   * 今日结论：允许正常参与
2. **强行业数量**
   * I4 核心主线：2 个
   * I3 强势行业：5 个
   * 今日最强：半导体设备
3. **Alpha 机会**
   * S4 结构准备：12 只
   * S5 买点触发：5 只
   * Core Alpha：3 只
4. **持仓风险**
   * P1 风险观察：2 只
   * P2 减仓警告：1 只
   * P3 退出触发：0 只

### 第二行：强行业榜 + 今日买点

左侧 50%：强行业榜
字段：

```text
行业名称
行业状态 I3/I4
强度分
20日涨幅
成交额放大
RPS90占比
龙头股
```

右侧 50%：今日 S5 买点触发
字段：

```text
股票
所属行业
买点类型
RPS标签
结构分
止损距离
信号有效性
```

### 第三行：核心共振池 + AI Brief

左侧：Core Alpha Pool
展示来自“技术入口 ∩ 研究入口”的股票。

右侧：AI 每日作战简报。

示例文案：

```text
今日市场处于 M3 偏强状态，半导体设备和 AI 算力仍为核心主线。
建议优先关注 I4 行业中的 S4/S5 前排标的，回避 I1/I2 轮动后排。
```

## 对应 API

```http
GET /api/dashboard/summary
GET /api/market/strength/latest
GET /api/industries/strength?state=I3,I4
GET /api/alpha/opportunities?state=S4,S5
GET /api/portfolio/risk-summary
GET /api/research/themes/in-motion
```

---

# 四、页面 02：Market / 市场温度

## 页面目标

回答：

> 今天这个市场环境，是否适合开新仓？

## 页面结构

### 顶部：市场状态 Hero

```text
市场状态：M3 偏强
市场强度分：78 / 100
建议仓位：40%-70%
交易权限：允许正常参与
```

### 中部：市场温度仪表盘 + 指数卡片

左侧：M0-M4 仪表盘
右侧：指数趋势卡

指数卡包括：

```text
上证指数
沪深300
创业板指
中证1000
科创50
```

每张卡显示：

```text
是否站上 MA20
是否站上 MA50
是否站上 MA120
今日涨跌幅
趋势状态
```

### 下部：市场宽度与情绪图

图表模块：

1. 上涨家数 / 下跌家数趋势
2. 涨停 / 跌停数量
3. 创 20/60/120 日新高数量
4. RPS90 股票数量
5. 全市场成交额 vs 20日均额

## 数据字段

```ts
type MarketStrength = {
  tradeDate: string
  marketState: 'M0' | 'M1' | 'M2' | 'M3' | 'M4'
  score: number
  suggestedPositionMin: number
  suggestedPositionMax: number
  upCount: number
  downCount: number
  limitUpCount: number
  limitDownCount: number
  newHigh20Count: number
  newHigh60Count: number
  rps90Count: number
  totalAmount: number
}
```

## 对应 API

```http
GET /api/market/strength/latest
GET /api/market/strength/history?days=120
GET /api/market/indices
GET /api/market/breadth
```

---

# 五、页面 03：Industries / 行业战役

## 页面目标

回答：

> 主战场在哪里？哪些行业是 I4 核心主线？

## 页面结构

### 顶部：行业概览

```text
I4 核心主线：2 个
I3 强势行业：5 个
今日最强行业：半导体设备
退潮行业：新能源链
```

### 左侧：行业热力图

用 ECharts Treemap。

维度：

```text
方块大小：成交额
颜色深浅：行业强度分
边框高亮：I4 核心主线
```

### 右侧：行业强度榜

字段：

```text
排名
行业名称
行业状态
行业强度分
20日涨幅
50日涨幅
成交额放大
RPS90占比
新高占比
龙头股
持续天数
```

### 行业详情抽屉 / 页面

点击行业后显示：

```text
行业指数 K线
行业强度趋势
成分股 RPS 排名
行业内 S3/S4/S5 股票
行业龙一 / 龙二 / 中军
AI 行业逻辑总结
```

## 对应 API

```http
GET /api/industries/strength?date=latest
GET /api/industries/{sectorCode}/dashboard
GET /api/industries/{sectorCode}/stocks
GET /api/industries/{sectorCode}/leaders
```

---

# 六、页面 04：Alpha Pool / Alpha 机会池

## 页面目标

回答：

> 哪些股票进入了系统机会池？哪些已经接近交易？

## 页面结构

顶部 Tab：

```text
全部
S3 行业内强股
S4 结构准备
S5 买点触发
Core Alpha 核心共振
Experimental 实验池
```

左侧筛选器：

```text
市场状态 M2/M3/M4
行业状态 I3/I4
RPS标签
结构分 > 70 / 85
买点类型
止损距离 < 8%
是否研究池标的
是否基金持股
是否北向持股
```

主表格字段：

```text
股票
行业
生命周期状态
行业状态
RPS20
RPS50
RPS120
RPS标签
结构分
买点类型
止损距离
研究主题
下一步动作
```

## 交互动作

```text
加入观察
生成交易计划
查看个股作战室
加入研究池
忽略本轮信号
```

## 对应 API

```http
GET /api/alpha/opportunities
GET /api/alpha/core-pool
POST /api/alpha/watch
POST /api/trade-plans/generate
```

---

# 七、页面 05：Research / 产业研究池

## 页面目标

回答：

> 我手动研究的行业股池，哪些开始被市场验证？

这个页面非常重要，因为它是你的“自下而上入口”。

## 页面 05A：研究主题列表

### 顶部

```text
新建研究主题
导入 CSV
AI 生成主题草稿
导入 Markdown / YAML
```

### 主题卡片字段

```text
主题名称
主题类型
研究 thesis
标的数量
S3 数量
S4 数量
S5 数量
Core Alpha 数量
最近更新
研究状态
```

示例：

```text
激光器三大件
硬科技 / 光电设备
股票数：12
S3：3
S4：1
S5：0
状态：跟踪中
```

## 页面 05B：研究主题详情

以“激光器三大件”为例。

### 顶部 Hero

```text
主题名称：激光器三大件
研究假设：国产激光器核心器件在高端制造、医疗和军工方向存在替代机会
研究状态：Tracking
AI 摘要按钮
```

### 中部：产业链节点图

节点：

```text
增益介质
泵浦源
谐振腔
激光器整机
激光加工设备
下游应用
```

每个节点显示：

```text
标的数量
S3/S4/S5 数量
代表公司
```

### 下部：研究池快照表

字段：

```text
股票
产业链节点
研究逻辑
官方行业
机构认可
RPS标签
结构标签
生命周期状态
买点状态
下一步动作
```

## 手动添加标的弹窗

表单字段：

```text
研究主题
产业链节点
股票代码
股票名称
角色定位
研究理由
核心逻辑
催化因素
风险因素
信心等级
研究状态
```

## 对应 API

```http
GET /api/research/themes
POST /api/research/themes
GET /api/research/themes/{themeId}
POST /api/research/themes/{themeId}/nodes
POST /api/research/watchlist
GET /api/research/themes/{themeId}/snapshot
POST /api/research/import/csv
POST /api/ai/generate-research-theme-draft
```

---

# 八、页面 06：Stock War Room / 个股作战室

## 页面目标

回答：

> 这只股票现在到底能不能做？如果能，怎么买？错了怎么卖？

## 页面结构

### 顶部信息条

```text
股票名称 / 代码
当前价格
涨跌幅
所属官方行业
所属研究主题
生命周期状态
```

### 左侧：K线与技术图表

组件：

```text
K线图
成交量
MA10 / MA20 / MA50 / MA120 / MA250
买点标记
止损线
结构区间
缺口区域
平台高点
```

建议用 `lightweight-charts` 实现 K 线。

### 中间：Evo 生命周期竖向状态流

```text
S0 机构认可：通过
S1 市场状态：M3
S2 行业状态：I4
S3 个股强度：中期启动
S4 结构健康：82 分
S5 买点信号：等待突破
S6 持仓状态：未持仓
S7 历史复盘：无
```

### 右侧：AI 分析 + 交易计划

AI 分析卡：

```text
当前判断：
该股处于 I4 行业内 S4 结构准备阶段，RPS50 已进入强势区间，结构健康分 82，等待放量突破平台。
```

交易计划卡：

```text
买点类型：突破买点
触发价：XX
止损价：XX
止损比例：7.4%
建议仓位：10%-15%
最大单笔风险：1%
是否系统内交易：是
```

## 下方 Tabs

```text
RPS详情
结构健康
买点历史
机构持股
新闻公告
研究逻辑
交易复盘
```

## 对应 API

```http
GET /api/stocks/{symbol}/dashboard
GET /api/stocks/{symbol}/kline
GET /api/stocks/{symbol}/rps
GET /api/stocks/{symbol}/structure
GET /api/stocks/{symbol}/signals
GET /api/stocks/{symbol}/trade-plan
POST /api/ai/analyze-stock
```

---

# 九、页面 07：Signals / 买点信号

## 页面目标

回答：

> 今天有哪些真正可执行的买点？

## 页面结构

顶部 Tab：

```text
口袋买点
突破买点
抛压衰竭
业绩二次突破
全部信号
```

顶部统计：

```text
今日有效信号：18
结构分 > 80：7
止损 < 8%：9
研究池交集：3
Core Alpha：2
```

表格字段：

```text
股票
行业
市场状态
行业状态
RPS标签
结构分
买点类型
触发价
止损价
止损距离
信号分
有效性
否决原因
```

筛选器：

```text
只看 I4
只看研究池
只看 Core Alpha
止损小于 8%
结构分大于 80
成交量倍数大于 1.5
```

## 对应 API

```http
GET /api/signals/today
GET /api/signals/today?signal_type=pocket_pivot
GET /api/signals/today?signal_type=breakout
GET /api/signals/today?signal_type=supply_exhaustion
POST /api/signals/{signalId}/create-trade-plan
```

---

# 十、页面 08：Portfolio / 持仓风控

## 页面目标

回答：

> 买入后还该不该拿？有没有触发风险？

## 页面结构

### 顶部：持仓总览

```text
持仓数量
总浮盈亏
今日风险提醒
P1 / P2 / P3 数量
总仓位
现金比例
```

### 中部：持仓状态分布

4 张卡片：

```text
P0 正常持有
P1 风险观察
P2 减仓警告
P3 退出触发
```

### 下方：持仓表格

字段：

```text
股票
成本价
现价
盈亏
所属行业
行业状态
RPS20
RPS50
持仓状态
初始止损
结构止损
移动止损
下一步动作
```

### 右侧：风险解释

点击某只持仓后显示：

```text
为什么进入 P1 / P2 / P3？
行业是否退潮？
RPS 是否下降？
是否跌破结构位？
下一步动作是什么？
```

## 对应 API

```http
GET /api/portfolio/summary
GET /api/portfolio/positions
GET /api/portfolio/risk-alerts
POST /api/portfolio/positions
PATCH /api/portfolio/positions/{positionId}
```

---

# 十一、页面 09：Review / 复盘进化

## 页面目标

回答：

> 我的交易系统有没有变好？错误来自哪里？

## 页面结构

### 顶部 KPI

```text
本周交易数
系统内交易比例
胜率
盈亏比
最大回撤
平均盈利
平均亏损
```

### 中部：分组表现

图表：

```text
按买点类型表现
按市场状态表现
按行业状态表现
按研究池 / 非研究池表现
按生命周期状态表现
```

### 下部：错误归因

错误类型：

```text
买点错误
市场环境错误
行业选择错误
结构判断错误
风控执行错误
情绪交易
```

### AI 复盘建议

```text
本周主要亏损来自 M1 市场中的系统外交易。
建议将 M1 市场下的口袋买点权限关闭，只保留观察功能。
抛压衰竭买点表现最好，值得继续跟踪。
```

## 对应 API

```http
GET /api/review/summary
GET /api/review/performance-by-signal
GET /api/review/performance-by-market-state
GET /api/review/performance-by-industry-state
POST /api/ai/review-trade
```

---

# 十二、页面 10：Settings / 系统设置

## 页面目标

管理数据源、策略参数、AI、同步任务。

## 页面结构

### 数据源设置

```text
AkShare 状态
Tushare 状态
东方财富数据状态
基金持股数据状态
北向快照状态
新闻公告数据状态
```

### 策略参数设置

```text
RPS周期
RPS阈值
市场强度阈值
行业强度阈值
结构健康阈值
买点参数
止损参数
```

### 云端同步设置

```text
R2 上传状态
CockroachDB 导入状态
上次同步时间
同步日志
```

### AI 设置

```text
模型供应商
API Key
缓存时间
日报生成开关
单股分析开关
```

## 对应 API

```http
GET /api/settings/data-sources
PATCH /api/settings/data-sources
GET /api/settings/strategy
PATCH /api/settings/strategy
GET /api/settings/sync-logs
```

---

# 十三、我建议的前端工程结构

```text
evoalpha-web/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                       # Dashboard
│   ├── market/
│   │   └── page.tsx
│   ├── industries/
│   │   ├── page.tsx
│   │   └── [sectorCode]/page.tsx
│   ├── alpha/
│   │   └── page.tsx
│   ├── research/
│   │   ├── page.tsx
│   │   └── [themeId]/page.tsx
│   ├── stocks/
│   │   └── [symbol]/page.tsx
│   ├── signals/
│   │   └── page.tsx
│   ├── portfolio/
│   │   └── page.tsx
│   ├── review/
│   │   └── page.tsx
│   ├── settings/
│   │   └── page.tsx
│   └── api/
│       ├── market/
│       ├── industries/
│       ├── alpha/
│       ├── research/
│       ├── stocks/
│       ├── signals/
│       ├── portfolio/
│       ├── review/
│       └── ai/
│
├── components/
│   ├── layout/
│   │   ├── AppSidebar.tsx
│   │   ├── TopBar.tsx
│   │   └── PageHeader.tsx
│   ├── cards/
│   │   ├── MetricCard.tsx
│   │   ├── StatusCard.tsx
│   │   ├── SignalCard.tsx
│   │   └── AiInsightCard.tsx
│   ├── charts/
│   │   ├── MarketGauge.tsx
│   │   ├── IndustryTreemap.tsx
│   │   ├── KlineChart.tsx
│   │   ├── RpsRadar.tsx
│   │   └── BreadthChart.tsx
│   ├── tables/
│   │   ├── IndustryRankTable.tsx
│   │   ├── AlphaOpportunityTable.tsx
│   │   ├── SignalTable.tsx
│   │   └── ResearchSnapshotTable.tsx
│   └── lifecycle/
│       ├── LifecycleFlow.tsx
│       ├── MarketStateBadge.tsx
│       ├── IndustryStateBadge.tsx
│       └── PositionStateBadge.tsx
│
├── lib/
│   ├── db.ts
│   ├── api.ts
│   ├── types.ts
│   ├── format.ts
│   └── constants.ts
│
├── styles/
│   └── globals.css
│
└── package.json
```

---

# 十四、核心组件树

## Dashboard

```text
DashboardPage
├── PageHeader
├── MarketSummaryCards
│   ├── MarketStateCard
│   ├── IndustrySummaryCard
│   ├── SignalSummaryCard
│   └── PortfolioRiskCard
├── IndustryRankTable
├── TodaySignalsTable
├── CoreAlphaPool
├── ResearchThemesInMotion
└── AiDailyBrief
```

## Stock War Room

```text
StockWarRoomPage
├── StockHeader
├── KlineChartPanel
├── LifecycleFlow
├── RpsRadarPanel
├── StructureHealthPanel
├── TimingSignalPanel
├── TradePlanPanel
├── AiStockInsight
└── StockTabs
    ├── NewsTab
    ├── ResearchTab
    ├── InstitutionTab
    └── ReviewTab
```

## Research Theme Detail

```text
ResearchThemeDetailPage
├── ThemeHeader
├── ValueChainMap
├── ThemeMetrics
├── ResearchSnapshotTable
├── AiThemeInsight
└── AddWatchlistDialog
```

---

# 十五、视觉实现建议

## 1. CSS 设计变量

```css
:root {
  --background: #080D18;
  --surface: #111827;
  --surface-2: #172033;
  --border: rgba(255, 255, 255, 0.08);

  --text-primary: #F5F7FB;
  --text-secondary: #AAB4C5;
  --text-muted: #73819A;

  --blue: #6EA8FE;
  --green: #30C48D;
  --orange: #F5B942;
  --red: #FF6B6B;
  --purple: #9B8CFF;
}
```

## 2. 状态颜色

```text
M3 / I4 / S5：蓝绿高亮
P1：黄色
P2：橙色
P3：红色
研究主题：紫色
AI 模块：蓝紫渐变
```

## 3. 字体

```text
中文：PingFang SC / Noto Sans SC
英文数字：Inter
代码和指标：JetBrains Mono
```

---

# 十六、这次设计和之前生成图的关键差异

之前的问题是：

```text
只是把很多页面拼在一起；
每页信息密度类似；
没有明确主次；
没有真正围绕使用场景设计；
文字和数据只是装饰。
```

现在要改成：

```text
每一页只回答一个核心问题；
每个模块对应一个实际 API；
每个页面有清楚的主行动；
每个数据都有决策意义；
前端组件能真正开发。
```

---

# 十七、最小可行版本页面顺序

为了最快做出真实产品，我建议先做 5 页，而不是一次做 10 页。

## MVP 1.0 先做

```text
1. Dashboard 总览
2. Market 市场温度
3. Industries 行业战役
4. Research 产业研究池
5. Stock War Room 个股作战室
```

## MVP 1.5 再做

```text
6. Signals 买点信号
7. Portfolio 持仓风控
8. Review 复盘进化
9. Settings 系统设置
```

这样比较现实。

---

# 十八、最终建议

技术栈就定为：

```text
Next.js App Router
TypeScript
Tailwind CSS
shadcn/ui
TanStack Table
ECharts
Lightweight Charts
TanStack Query
Zustand
Postgres / CockroachDB
Next.js Route Handlers
Vercel
```

架构上继续保持：

```text
Python 本地重计算
Next.js 云端轻查询
Vercel 负责前端和轻 API
数据库只存核心结果
AI 按需调用
```

下一步最应该做的不是继续生成概念图，而是做一份  **`frontend_pages.md + api_spec.md + component_tree.md`** ，然后可以直接交给 Claude Code / Cursor / v0 开始实现第一版页面。
