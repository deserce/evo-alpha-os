可以。下面我把 **Market 市场温度页 + 指数K线模块** 写成一版完整规划，可以直接作为 `market_page_spec.md` 或交给 Claude Code / Cursor 的开发说明。

之前上传的部分文件已经过期，如果后面要继续读取旧代码或旧文档，需要重新上传；但基于当前截图和我们讨论的信息，下面这版已经可以独立作为施工蓝图。

---

# EvoAlpha OS｜Market 市场温度页完整规划 v0.2

## 0. 页面核心定位

Market 页是 EvoAlpha OS 的  **L0 市场强度模块** ，它不是普通指数行情页，而是整个系统的  **交易权限判断页** 。

它要回答四个核心问题：

```text
1. 今天市场能不能开新仓？
2. 当前市场适合多大仓位？
3. 指数趋势是否支持强势股交易？
4. 市场宽度、赚钱效应、强势扩散是否健康？
```

页面最终要形成这样的使用体验：

> 用户打开 Market 页，第一眼看到市场状态是 M3 偏强，知道今天允许正常参与；
> 然后通过指数 K 线确认主要指数是否站上 MA20 / MA50 / MA120；
> 再看市场宽度、涨跌停、RPS90、新高数量和成交额，判断市场是否真的扩散；
> 最后根据系统提示，决定是否进入 Industries 页寻找主战场。

---

# 1. 页面最终结构

Market 页采用  **四段式结构** ：

```text
第一段：Market Hero 市场交易权限总卡
第二段：指数K线 + 市场宽度卡组
第三段：市场宽度趋势 + 涨跌停情绪
第四段：强势扩散趋势 + 成交额与风险提示
```

---

## 1.1 页面整体布局示意

```text
┌──────────────────────────────────────────────────────────────┐
│ Topbar：搜索 / 日期 / AI助手 / 通知 / 用户                    │
├──────────────────────────────────────────────────────────────┤
│ Page Header：Market / 市场温度                                │
├──────────────────────────────────────────────────────────────┤
│ Market Hero：市场状态、交易权限、建议仓位、风险提示             │
├──────────────────────────────────────────────┬───────────────┤
│ Index Kline Panel 指数K线面板                 │ Breadth Cards │
│ 上证/沪深300/中证1000/创业板/科创50 可切换       │ 市场宽度卡组   │
├──────────────────────────────┬───────────────┴───────────────┤
│ Breadth Trend Chart           │ Limit Mood Chart              │
│ 市场宽度趋势                   │ 涨跌停情绪                     │
├──────────────────────────────┬───────────────────────────────┤
│ Strength Diffusion Chart       │ Amount & Risk Panel           │
│ 新高 + RPS90 扩散              │ 成交额 + 风险提示               │
└──────────────────────────────┴───────────────────────────────┘
```

---

# 2. 页面布局规格

## 2.1 基础规格

```text
设计画布：1440 × 900 起
Sidebar：240px
Topbar：64px
主内容区宽度：calc(100vw - 240px)
主内容区 padding：24px
Grid：12 columns
Gap：16px
卡片圆角：16px / 20px
页面最小高度：1200px
```

当前截图的问题是内容只占顶部约 200px，下面大量空白。新版必须让内容自然撑开，形成完整分析页。

---

## 2.2 主内容 Grid

```tsx
<div className="space-y-6 p-6">
  <MarketPageHeader />

  <MarketHeroCard />

  <div className="grid grid-cols-12 gap-4">
    <IndexKlinePanel className="col-span-8" />
    <MarketBreadthCards className="col-span-4" />
  </div>

  <div className="grid grid-cols-12 gap-4">
    <BreadthTrendChart className="col-span-6" />
    <LimitMoodChart className="col-span-6" />
  </div>

  <div className="grid grid-cols-12 gap-4">
    <StrengthDiffusionChart className="col-span-6" />
    <AmountAndRiskPanel className="col-span-6" />
  </div>
</div>
```

---

# 3. 模块一：Market Page Header

## 3.1 位置

```text
grid-column: 1 / 13
height: 56px
```

## 3.2 内容

左侧：

```text
MARKET / 市场温度
市场强度与交易权限
```

右侧：

```text
日期选择器
刷新按钮
生成市场解读按钮
```

## 3.3 功能

| 功能     | 说明                       |
| -------- | -------------------------- |
| 日期选择 | 查看历史某日市场状态       |
| 刷新     | 重新请求市场 Dashboard API |
| AI 解读  | 调用市场 AI 分析接口       |
| 页面标题 | 明确此页是交易权限页       |

---

# 4. 模块二：Market Hero 市场交易权限总卡

## 4.1 模块定位

这是整页最重要的部分。它要把复杂市场状态压缩成清晰结论：

```text
今天能不能做？
适合什么仓位？
当前风险在哪里？
下一步应该看什么？
```

---

## 4.2 位置与尺寸

```text
grid-column: 1 / 13
height: 190px - 220px
```

---

## 4.3 内部布局

```text
┌──────────────┬──────────────────────┬──────────────────┬──────────────────┐
│ 大号仪表盘    │ 市场状态与交易权限      │ 建议仓位           │ 风险提示           │
│ 65.87        │ M3 偏强                │ 39%-59%           │ 高位分化 / 成交额等 │
└──────────────┴──────────────────────┴──────────────────┴──────────────────┘
```

### 左侧：大号仪表盘

```text
市场强度分：65.87 / 100
市场状态：M3 偏强
M0-M4 五档刻度
```

### 中间：状态结论

```text
M3 偏强
交易权限：允许正常参与
系统判断：市场处于偏强状态，强势股数量扩散，但尚未进入全面进攻阶段。
```

### 右中：建议仓位

```text
建议仓位：39%-59%
单笔风险：≤ 账户 1%
策略权限：允许 S4 / S5 扫描
```

### 右侧：风险提示

```text
高位强势股分化：中
跌停数量：低
成交额放大：支持
RPS90 扩散：良好
```

---

## 4.4 状态颜色

| 状态    | 颜色 |
| ------- | ---- |
| M0 极弱 | 红   |
| M1 偏弱 | 橙红 |
| M2 中性 | 黄   |
| M3 偏强 | 青绿 |
| M4 强势 | 蓝绿 |

---

## 4.5 API

```http
GET /api/market/dashboard?date=latest
```

Hero 使用返回中的：

```ts
type MarketHero = {
  tradeDate: string
  marketState: 'M0' | 'M1' | 'M2' | 'M3' | 'M4'
  marketStrengthScore: number
  previousMarketState?: string
  stateChange?: 'upgrade' | 'downgrade' | 'stable'
  suggestedPositionMin: number
  suggestedPositionMax: number
  tradingPermission: 'forbidden' | 'observe' | 'limited' | 'normal' | 'active'
  summary: string
  riskNotes: {
    level: 'low' | 'medium' | 'high'
    title: string
    description: string
  }[]
  updatedAt: string
}
```

---

# 5. 模块三：Index Kline Panel 指数K线面板

## 5.1 模块定位

指数区不能再只是小卡片，要升级成完整的指数趋势判断面板。

它回答：

```text
主要指数是否处于健康趋势？
指数是否站上关键均线？
指数趋势是否支持强势股交易？
```

---

## 5.2 位置与尺寸

```text
grid-column: 1 / 9
height: 380px - 420px
```

右侧配合市场宽度卡组：

```text
MarketBreadthCards：grid-column: 9 / 13
height: 380px - 420px
```

---

## 5.3 内部结构

```text
┌────────────────────────────────────────────────────────────┐
│ 指数 Tabs：上证 / 沪深300 / 中证1000 / 创业板 / 科创50 / 中证全指 │
├────────────────────────────────────────────────────────────┤
│ 指数摘要条：点位 / 涨跌幅 / 趋势状态 / MA20 / MA50 / MA120       │
├────────────────────────────────────────────────────────────┤
│ K线主图：K线 + MA20 + MA50 + MA120 + MA250                    │
├────────────────────────────────────────────────────────────┤
│ 成交量副图：成交量柱 + 20日均量                                │
└────────────────────────────────────────────────────────────┘
```

---

## 5.4 指数 Tabs

默认指数：

```text
上证指数
沪深300
中证1000
创业板指
科创50
中证全指
```

默认选中：

```text
上证指数 或 中证全指
```

建议可以在配置中调整默认指数。

---

## 5.5 指数摘要条

字段：

```text
指数名称
当前点位
今日涨跌幅
趋势状态：强 / 中 / 弱
MA20：站上 / 未站上
MA50：站上 / 未站上
MA120：站上 / 未站上
20日涨幅
50日涨幅
成交额
```

示例：

```text
创业板指｜3677.18｜+0.78%
趋势：偏强
MA20：站上｜MA50：站上｜MA120：站上
20日：+6.2%｜50日：+11.4%
```

---

## 5.6 K线图显示内容

MVP 必须显示：

```text
K线
MA20
MA50
MA120
成交量
```

进阶显示：

```text
MA250
市场状态升级标记
市场状态降级标记
站上 / 跌破关键均线标记
```

---

## 5.7 技术选择

指数 K 线推荐：

```text
lightweight-charts
```

原因：

```text
适合金融 K 线
性能好
交互自然
后续可复用于个股作战室
```

统计图继续用：

```text
ECharts
```

---

## 5.8 API

指数列表与摘要：

```http
GET /api/market/indices
```

单个指数 K 线：

```http
GET /api/market/indices/{indexCode}/kline?days=250
```

也可以 Dashboard 聚合接口先返回 indices 摘要，但 K 线单独请求，避免主接口太重。

---

## 5.9 数据结构

```ts
type MarketIndexItem = {
  indexCode: string
  indexName: string
  close: number
  pctChange: number
  amount: number
  aboveMa20: boolean
  aboveMa50: boolean
  aboveMa120: boolean
  aboveMa250?: boolean
  trendState: 'weak' | 'neutral' | 'strong'
  return20d: number
  return50d: number
  return120d?: number
}

type IndexKlineItem = {
  tradeDate: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  ma20: number | null
  ma50: number | null
  ma120: number | null
  ma250?: number | null
}
```

---

# 6. 模块四：Market Breadth Cards 市场宽度卡组

## 6.1 模块定位

指数强不代表市场强，Market 页必须看到市场宽度。

它回答：

```text
市场是否普涨？
赚钱效应是否扩散？
强势股数量是否增加？
亏钱效应是否可控？
```

---

## 6.2 位置与尺寸

```text
grid-column: 9 / 13
height: 380px - 420px
```

---

## 6.3 卡组布局

建议 2 列 × 4 行小卡：

```text
上涨家数
下跌家数
涨停数量
跌停数量
创60日新高
创120日新高
RPS90 股票数
全市场成交额
```

每张卡包含：

```text
指标名称
当前数值
较昨日变化
状态颜色
小型 sparkline
```

---

## 6.4 API 数据

来自：

```ts
type MarketBreadth = {
  upCount: number
  downCount: number
  limitUpCount: number
  limitDownCount: number
  newHigh20Count: number
  newHigh60Count: number
  newHigh120Count: number
  rps90Count: number
  totalAmount: number
  amountRatio20: number
}
```

---

# 7. 模块五：Breadth Trend Chart 市场宽度趋势图

## 7.1 模块定位

回答：

```text
上涨家数是否持续增加？
市场是少数权重拉指数，还是整体扩散？
```

---

## 7.2 位置与尺寸

```text
grid-column: 1 / 7
height: 280px
```

---

## 7.3 图表内容

最近 60 日：

```text
上涨家数
下跌家数
上涨比例
50% 中性参考线
```

---

## 7.4 技术

```text
ECharts Line Chart
```

---

## 7.5 数据结构

```ts
type BreadthHistoryItem = {
  tradeDate: string
  upCount: number
  downCount: number
  upRatio: number
}
```

---

# 8. 模块六：Limit Mood Chart 涨跌停情绪图

## 8.1 模块定位

回答：

```text
市场赚钱效应和亏钱效应如何？
短线情绪是升温还是退潮？
```

---

## 8.2 位置与尺寸

```text
grid-column: 7 / 13
height: 280px
```

---

## 8.3 图表内容

最近 60 日：

```text
涨停数量
跌停数量
连板高度，可选
封板率，可选
```

---

## 8.4 技术

```text
ECharts Bar Chart
```

---

## 8.5 数据结构

```ts
type LimitStatsItem = {
  tradeDate: string
  limitUpCount: number
  limitDownCount: number
  maxBoardHeight?: number
  sealedRate?: number
}
```

---

# 9. 模块七：Strength Diffusion Chart 强势扩散图

## 9.1 模块定位

回答：

```text
市场中的强势股是否在扩散？
RPS90 股票是否增加？
创新高股票是否增加？
```

这部分是从 M2 过渡到 M3 / M4 的关键。

---

## 9.2 位置与尺寸

```text
grid-column: 1 / 7
height: 280px
```

---

## 9.3 图表内容

最近 120 日：

```text
创20日新高数量
创60日新高数量
创120日新高数量
RPS90 股票数量
```

---

## 9.4 技术

```text
ECharts Line / Area Chart
```

---

## 9.5 数据结构

```ts
type StrengthDiffusionItem = {
  tradeDate: string
  newHigh20Count: number
  newHigh60Count: number
  newHigh120Count: number
  rps90Count: number
}
```

---

# 10. 模块八：Amount & Risk Panel 成交额与风险提示

## 10.1 模块定位

回答：

```text
当前成交额是否支持行情？
市场是否存在需要注意的风险？
```

---

## 10.2 位置与尺寸

```text
grid-column: 7 / 13
height: 280px
```

---

## 10.3 内部结构

```text
左侧 60%：成交额图
右侧 40%：风险提示列表
```

---

## 10.4 成交额图

内容：

```text
全市场成交额
20日均成交额
成交额放大倍数
```

技术：

```text
ECharts Bar + Line Combo
```

---

## 10.5 风险提示列表

风险来源可以先规则生成，后续再 AI 总结。

示例：

```text
高位强股分化：中等
跌停数量：低风险
成交额放大：支持行情
RPS90 扩散：良好
短线涨停升温：注意追高
```

---

## 10.6 数据结构

```ts
type AmountHistoryItem = {
  tradeDate: string
  totalAmount: number
  amountMa20: number
  amountRatio20: number
}

type RiskNote = {
  level: 'low' | 'medium' | 'high'
  title: string
  description: string
}
```

---

# 11. 后端数据表规划

## 11.1 市场强度表

```sql
CREATE TABLE market_strength_daily (
    trade_date DATE PRIMARY KEY,

    market_state TEXT,
    market_strength_score NUMERIC,

    index_trend_score NUMERIC,
    market_breadth_score NUMERIC,
    amount_score NUMERIC,
    new_high_score NUMERIC,
    limit_up_score NUMERIC,
    limit_down_risk_score NUMERIC,
    rps_diffusion_score NUMERIC,

    up_count INTEGER,
    down_count INTEGER,
    up_ratio NUMERIC,

    limit_up_count INTEGER,
    limit_down_count INTEGER,

    new_high_20_count INTEGER,
    new_high_60_count INTEGER,
    new_high_120_count INTEGER,

    rps90_count INTEGER,

    total_amount NUMERIC,
    amount_ma20 NUMERIC,
    amount_ratio_20 NUMERIC,

    suggested_position_min NUMERIC,
    suggested_position_max NUMERIC,

    summary TEXT,
    risk_notes JSONB,

    created_at TIMESTAMP DEFAULT now()
);
```

---

## 11.2 指数日线表

```sql
CREATE TABLE index_daily_prices (
    trade_date DATE,
    index_code TEXT,
    index_name TEXT,

    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    pre_close NUMERIC,
    pct_change NUMERIC,

    volume NUMERIC,
    amount NUMERIC,

    created_at TIMESTAMP DEFAULT now(),

    PRIMARY KEY (trade_date, index_code)
);
```

---

## 11.3 指数特征表

```sql
CREATE TABLE market_index_features (
    trade_date DATE,
    index_code TEXT,
    index_name TEXT,

    close NUMERIC,
    pct_change NUMERIC,

    ma20 NUMERIC,
    ma50 NUMERIC,
    ma120 NUMERIC,
    ma250 NUMERIC,

    above_ma20 BOOLEAN,
    above_ma50 BOOLEAN,
    above_ma120 BOOLEAN,
    above_ma250 BOOLEAN,

    return_20d NUMERIC,
    return_50d NUMERIC,
    return_120d NUMERIC,

    trend_score NUMERIC,
    trend_state TEXT,

    created_at TIMESTAMP DEFAULT now(),

    PRIMARY KEY (trade_date, index_code)
);
```

---

# 12. 本地数据工厂任务规划

## 12.1 新增 / 强化脚本

```text
backend/data_job/update_index_kline.py
backend/quant_engine/features/calc_index_features.py
backend/quant_engine/features/calc_market_strength.py
```

---

## 12.2 update_index_kline.py

职责：

```text
拉取主要指数日线数据
清洗字段
写入 index_daily_prices
```

默认指数列表：

```text
000001.SH 上证指数
000300.SH 沪深300
000852.SH 中证1000
399006.SZ 创业板指
000688.SH 科创50
000985.SH 中证全指
```

---

## 12.3 calc_index_features.py

职责：

```text
计算 MA20 / MA50 / MA120 / MA250
计算 20 / 50 / 120 日涨跌幅
判断是否站上均线
计算 trend_score
映射 trend_state
写入 market_index_features
```

---

## 12.4 calc_market_strength.py

职责：

```text
读取全市场个股日线
读取 stock_strength_daily
读取 market_index_features
计算上涨家数 / 下跌家数
计算涨停 / 跌停数量
计算新高数量
计算 RPS90 数量
计算成交额放大倍数
计算 market_strength_score
映射 M0-M4
生成风险提示 risk_notes
写入 market_strength_daily
```

---

# 13. API 规划

## 13.1 Market 主聚合 API

```http
GET /api/market/dashboard?date=latest
```

返回：

```ts
type MarketDashboard = {
  tradeDate: string
  updatedAt: string

  hero: MarketHero

  indices: MarketIndexItem[]

  defaultIndexCode: string

  breadth: MarketBreadth

  breadthHistory: BreadthHistoryItem[]

  limitStatsHistory: LimitStatsItem[]

  strengthDiffusionHistory: StrengthDiffusionItem[]

  amountHistory: AmountHistoryItem[]

  riskNotes: RiskNote[]
}
```

---

## 13.2 指数 K 线 API

```http
GET /api/market/indices/{indexCode}/kline?days=250
```

返回：

```ts
type IndexKlineResponse = {
  indexCode: string
  indexName: string
  items: IndexKlineItem[]
}
```

---

## 13.3 指数摘要 API，可选

```http
GET /api/market/indices
```

返回：

```ts
type MarketIndexItem[]
```

如果 `/api/market/dashboard` 已经返回 indices，可以暂时不单独做这个接口。

---

# 14. 前端技术栈

Market 页使用：

```text
Next.js App Router
TypeScript
Tailwind CSS
shadcn/ui
TanStack Query
lightweight-charts
ECharts
Lucide React
Zustand，可选
```

---

## 14.1 图表分工

| 模块            | 技术                          |
| --------------- | ----------------------------- |
| 指数 K 线       | lightweight-charts            |
| 指数成交量      | lightweight-charts 或 ECharts |
| 市场仪表盘      | ECharts Gauge                 |
| 市场宽度趋势    | ECharts Line                  |
| 涨跌停情绪      | ECharts Bar                   |
| 新高 / RPS 扩散 | ECharts Line / Area           |
| 成交额          | ECharts Bar + Line            |

---

# 15. 前端组件规划

## 15.1 页面组件树

```text
MarketPage
├── MarketPageHeader
├── MarketHeroCard
│   ├── MarketGauge
│   ├── MarketPermissionBlock
│   ├── PositionSuggestionBlock
│   └── MarketRiskNotes
├── MarketMainGrid
│   ├── IndexKlinePanel
│   │   ├── IndexTabs
│   │   ├── IndexSummaryStrip
│   │   └── IndexKlineChart
│   └── MarketBreadthCards
├── MarketChartsGrid
│   ├── BreadthTrendChart
│   ├── LimitMoodChart
│   ├── StrengthDiffusionChart
│   └── AmountAndRiskPanel
```

---

## 15.2 组件文件

```text
components/market/MarketPage.tsx
components/market/MarketHeroCard.tsx
components/market/MarketGauge.tsx
components/market/MarketPermissionBlock.tsx
components/market/IndexKlinePanel.tsx
components/market/IndexTabs.tsx
components/market/IndexSummaryStrip.tsx
components/market/IndexKlineChart.tsx
components/market/MarketBreadthCards.tsx
components/market/BreadthTrendChart.tsx
components/market/LimitMoodChart.tsx
components/market/StrengthDiffusionChart.tsx
components/market/AmountAndRiskPanel.tsx
components/market/MarketRiskNotes.tsx
```

---

# 16. 文件结构规划

```text
evoalpha-web/
├── app/
│   ├── market/
│   │   └── page.tsx
│   └── api/
│       └── market/
│           ├── dashboard/
│           │   └── route.ts
│           └── indices/
│               └── [indexCode]/
│                   └── kline/
│                       └── route.ts
│
├── components/
│   ├── market/
│   │   ├── MarketPage.tsx
│   │   ├── MarketHeroCard.tsx
│   │   ├── MarketGauge.tsx
│   │   ├── MarketPermissionBlock.tsx
│   │   ├── IndexKlinePanel.tsx
│   │   ├── IndexTabs.tsx
│   │   ├── IndexSummaryStrip.tsx
│   │   ├── IndexKlineChart.tsx
│   │   ├── MarketBreadthCards.tsx
│   │   ├── BreadthTrendChart.tsx
│   │   ├── LimitMoodChart.tsx
│   │   ├── StrengthDiffusionChart.tsx
│   │   ├── AmountAndRiskPanel.tsx
│   │   └── MarketRiskNotes.tsx
│   │
│   └── shared/
│       ├── SectionCard.tsx
│       ├── StatusBadge.tsx
│       ├── LoadingSkeleton.tsx
│       └── EmptyState.tsx
│
├── lib/
│   ├── api/
│   │   └── market.ts
│   ├── db/
│   │   └── market-queries.ts
│   ├── types/
│   │   └── market.ts
│   ├── constants/
│   │   └── market-states.ts
│   └── format.ts
```

---

# 17. MVP 开发顺序

## Step 1：静态页面重构

先不接真实 API，用 mock data 完成布局：

```text
MarketHeroCard
IndexKlinePanel
MarketBreadthCards
BreadthTrendChart
LimitMoodChart
StrengthDiffusionChart
AmountAndRiskPanel
```

目标是先解决当前页面“顶部压缩 + 大面积空白”的问题。

---

## Step 2：实现指数 K 线 mock

使用 `lightweight-charts` 画出：

```text
K线
MA20
MA50
MA120
成交量
```

---

## Step 3：定义 TypeScript 类型

```text
MarketDashboard
MarketHero
MarketIndexItem
IndexKlineItem
MarketBreadth
BreadthHistoryItem
LimitStatsItem
StrengthDiffusionItem
AmountHistoryItem
RiskNote
```

---

## Step 4：实现 API mock

先让：

```http
GET /api/market/dashboard
GET /api/market/indices/{indexCode}/kline
```

返回 mock JSON。

---

## Step 5：接数据库

连接：

```text
market_strength_daily
index_daily_prices
market_index_features
```

---

## Step 6：接本地数据工厂

确保本地每日任务能生成：

```text
index_daily_prices
market_index_features
market_strength_daily
```

并同步到云端。

---

# 18. 给 Claude Code / Cursor 的完整开发指令

你可以直接把下面这段丢给开发模型：

```text
请重构 EvoAlpha OS 的 /market 页面，目标是做成完整的“市场温度 / 交易权限 Dashboard”，不要把所有信息压缩在页面顶部。

技术栈：
Next.js App Router + TypeScript + Tailwind CSS + shadcn/ui + TanStack Query + lightweight-charts + ECharts。

页面布局：
1. 主内容区使用 p-6 space-y-6。
2. 顶部为 MarketPageHeader。
3. 第一块为 MarketHeroCard，高度 190-220px，展示：
   - 大号市场强度仪表盘
   - M0-M4 市场状态
   - 市场强度分
   - 交易权限
   - 建议仓位
   - 风险提示
4. 第二块为 12 栅格：
   - 左侧 col-span-8：IndexKlinePanel，高度 400px
   - 右侧 col-span-4：MarketBreadthCards，高度 400px
5. IndexKlinePanel 要包含：
   - Tabs：上证指数、沪深300、中证1000、创业板指、科创50、中证全指
   - IndexSummaryStrip：点位、涨跌幅、趋势状态、MA20/MA50/MA120、20日涨幅、50日涨幅
   - lightweight-charts K线图
   - MA20 / MA50 / MA120 线
   - 成交量副图
6. 第三块为 12 栅格：
   - col-span-6 BreadthTrendChart，展示上涨/下跌家数和上涨比例
   - col-span-6 LimitMoodChart，展示涨停/跌停数量
7. 第四块为 12 栅格：
   - col-span-6 StrengthDiffusionChart，展示新高数量和 RPS90 数量
   - col-span-6 AmountAndRiskPanel，展示成交额、20日均额、风险提示
8. 页面最小高度不低于 1200px，不允许出现下方大面积空白。
9. 所有图表先用 mock data 实现，后续接 API。
10. 需要实现两个 API mock：
    - GET /api/market/dashboard
    - GET /api/market/indices/[indexCode]/kline?days=250
11. 卡片统一风格：
    bg-slate-950/60 border border-white/10 rounded-2xl
12. 状态颜色：
    M0 红，M1 橙红，M2 黄，M3 青绿，M4 蓝绿。
13. 不删除现有顶部导航和侧边栏，只重构 market 主内容区域。
```

---

# 19. 当前页面改造的验收标准

改完后，Market 页必须满足：

```text
1. 页面不是顶部信息条，而是完整 Dashboard。
2. 第一眼能看到 M3 / M0-M4 市场状态和交易权限。
3. 指数区域有可切换 K 线图。
4. 右侧市场宽度卡组清晰展示上涨、下跌、涨停、跌停、新高、RPS90。
5. 下方至少有四张趋势图。
6. 页面没有大面积空白。
7. 所有数据模块都有明确 API 对应。
8. 后续能自然扩展到真实数据库。
```

---

# 20. 最终效果目标

Market 页最终应该让用户形成这样的判断：

```text
今天市场处于 M3 偏强状态；
指数大多站上 MA20 / MA50，趋势结构支持交易；
市场宽度改善，上涨家数占优；
涨停数量升温，跌停数量较低；
新高数量和 RPS90 股票数量扩散；
成交额高于 20 日均额；
系统建议仓位 39%-59%；
下一步应该去 Industries 页寻找 I3/I4 主线行业。
```

一句话：

> **Market 页不是行情页，而是 EvoAlpha OS 的交易权限页。**
> 加入指数 K 线后，它才真正具备“市场温度 + 指数趋势 + 风险权限”的完整判断能力。
>
