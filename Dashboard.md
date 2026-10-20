好，那我们正式进入  **逐页施工图阶段** 。

下面先做  **第一页：Dashboard / 总览页 / Alpha 雷达页** 。
后面每一页我们都按这个格式做：

```text
1. 页面目标
2. 视觉布局施工图
3. 每个区域的位置与内容
4. 对应 API
5. 后端数据依赖
6. 前端技术栈
7. 组件拆分
8. 文件结构规划
9. 开发顺序
```

---

# Page 01｜Dashboard 总览页施工图 v0.1

## 1. 页面定位

Dashboard 是用户每天打开 EvoAlpha OS 后看到的第一页。

它不是“数据大杂烩”，而是一个  **每日作战中枢** 。

它要回答 6 个问题：

```text
1. 今天市场能不能做？
2. 主战场在哪些行业？
3. 哪些股票进入 S4 结构准备？
4. 哪些股票进入 S5 买点触发？
5. 我的研究池有没有新的市场验证？
6. 我的持仓有没有风险？
```

所以 Dashboard 的核心不是展示所有细节，而是：

> **用一屏告诉用户今天该关注什么、该避开什么、下一步做什么。**

---

# 2. 页面整体布局

## 2.1 页面基础尺寸

先按桌面端设计。

```text
推荐设计画布：1440 × 900
左侧 Sidebar：240px
顶部 Topbar：64px
主内容区 Padding：24px
主内容区 Grid：12 栅格
栅格间距：16px
卡片圆角：16px - 20px
```

---

## 2.2 总体结构

```text
┌──────────────────────────────────────────────────────────────┐
│ Topbar 顶部导航                                                │
├──────────────┬───────────────────────────────────────────────┤
│              │ Page Header 页面标题                           │
│              ├───────────────────────────────────────────────┤
│              │ 第一行：4 张核心状态卡                          │
│ Sidebar      ├───────────────────────────────────────────────┤
│ 左侧导航      │ 第二行：强行业榜 + 今日买点 + AI Brief           │
│              ├───────────────────────────────────────────────┤
│              │ 第三行：研究池动态 + 核心共振池 + 持仓风险         │
│              │                                                │
└──────────────┴───────────────────────────────────────────────┘
```

---

# 3. 详细布局施工图

## 3.1 具体位置划分

```text
屏幕宽度 1440
Sidebar 宽度 240
主内容区宽度 = 1440 - 240 = 1200
主内容区内部 Padding = 24
实际内容宽度 = 1152
12 栅格，每列约 82px，间距 16px
```

---

## 3.2 页面分区图

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ B. Topbar｜搜索框｜交易日｜AI 助手｜通知｜用户头像                            │
├───────────────┬─────────────────────────────────────────────────────────────┤
│               │ A. Page Header                                               │
│               │ Dashboard / 总览｜今日交易日｜数据更新时间｜刷新按钮             │
│               ├──────────────┬──────────────┬──────────────┬───────────────┤
│               │ C1 市场状态   │ C2 强行业概览 │ C3 Alpha机会 │ C4 持仓风险     │
│               │ 3 columns    │ 3 columns    │ 3 columns    │ 3 columns      │
│               ├──────────────────────┬────────────────────┬────────────────┤
│ Sidebar       │ D 强行业榜             │ E 今日买点触发      │ F AI每日简报     │
│ 240px         │ 5 columns              │ 4 columns          │ 3 columns       │
│               │                       │                    │                │
│               ├──────────────────────┬────────────────────┬────────────────┤
│               │ G 研究池动态           │ H 核心共振池        │ I 持仓风险提醒   │
│               │ 5 columns              │ 4 columns          │ 3 columns       │
│               │                       │                    │                │
└───────────────┴──────────────────────┴────────────────────┴────────────────┘
```

---

# 4. 左侧 Sidebar 设计

## 4.1 位置

```text
x: 0
y: 0
width: 240px
height: 100vh
position: fixed
```

## 4.2 内容

顶部：

```text
Logo：EvoAlpha OS
副标题：Evolution is Freedom
系统状态：A-share Alpha OS
```

导航菜单：

```text
Dashboard / 总览
Market / 市场温度
Industries / 行业战役
Alpha Pool / Alpha机会池
Research / 产业研究池
Stocks / 个股作战室
Signals / 买点信号
Portfolio / 持仓风控
Review / 复盘进化
AI Lab / AI实验室
Settings / 系统设置
```

底部：

```text
数据更新时间：2026-xx-xx 15:30
本地工厂状态：已同步
云端状态：正常
```

## 4.3 组件

```text
AppSidebar
NavItem
SyncStatusMini
```

---

# 5. 顶部 Topbar 设计

## 5.1 位置

```text
x: 240px
y: 0
width: calc(100vw - 240px)
height: 64px
position: sticky top-0
```

## 5.2 内容布局

从左到右：

```text
全局搜索框：搜索股票 / 行业 / 研究主题 / 指标
当前交易日：2026-xx-xx
数据状态：今日数据已更新
AI助手按钮
通知铃铛
用户头像
```

## 5.3 对应交互

| 交互         | 动作                              |
| ------------ | --------------------------------- |
| 搜索股票     | 跳转 `/stocks/[symbol]`         |
| 搜索行业     | 跳转 `/industries/[sectorCode]` |
| 搜索研究主题 | 跳转 `/research/[themeId]`      |
| 点击 AI 助手 | 打开右侧 AI Drawer                |
| 点击数据状态 | 打开同步日志弹窗                  |

---

# 6. Page Header 区域

## 6.1 位置

```text
主内容区顶部
height: 56px
grid-column: 1 / 13
```

## 6.2 内容

左侧：

```text
Dashboard / 总览
今日作战中枢
```

右侧：

```text
交易日：2026-xx-xx
数据更新时间：15:30
刷新按钮
生成 AI 日报按钮
```

## 6.3 文案示例

```text
Dashboard / 总览
从市场强度、行业主线、Alpha 机会、研究池变化和持仓风险五个维度，生成今日作战视图。
```

---

# 7. 第一行：4 张核心状态卡

第一行是 Dashboard 最重要的区域，要求用户 5 秒内看懂今天的环境。

---

## C1｜市场状态卡 Market State Card

### 位置

```text
grid-column: 1 / 4
height: 160px
```

### 内容

```text
市场状态：M3 偏强
市场强度分：78 / 100
建议仓位：40% - 70%
交易权限：允许正常参与
迷你趋势图：市场强度近 20 日变化
```

### 视觉

* M3 用绿色 / 青色高亮
* 如果 M0 / M1，用红色或橙色
* 卡片左上角显示状态标签

### 点击跳转

```text
点击卡片 → /market
```

### API

```http
GET /api/market/strength/latest
```

### 数据表来源

```text
market_strength_daily
```

---

## C2｜强行业概览 Strong Industries Card

### 位置

```text
grid-column: 4 / 7
height: 160px
```

### 内容

```text
I4 核心主线：2 个
I3 强势行业：5 个
今日最强：半导体设备
行业扩散：良好
```

展示 Top 3：

```text
半导体设备 I4
AI算力 I4
机器人 I3
```

### 点击跳转

```text
点击卡片 → /industries
```

### API

```http
GET /api/industries/strength?state=I3,I4&limit=3
```

### 数据表来源

```text
industry_strength_daily
```

---

## C3｜Alpha 机会卡 Alpha Opportunity Card

### 位置

```text
grid-column: 7 / 10
height: 160px
```

### 内容

```text
S4 结构准备：12 只
S5 买点触发：5 只
Core Alpha：3 只
今日新增：4 只
```

### 点击跳转

```text
点击 S4 → /alpha?state=S4
点击 S5 → /signals
点击 Core Alpha → /alpha?pool=core
```

### API

```http
GET /api/alpha/summary
```

底层也可以由以下接口聚合：

```http
GET /api/alpha/opportunities?state=S4
GET /api/alpha/opportunities?state=S5
GET /api/alpha/core-pool
```

### 数据表来源

```text
stock_lifecycle_state
timing_signals_daily
alpha_opportunities
research_stock_daily_snapshot
```

---

## C4｜持仓风险卡 Portfolio Risk Card

### 位置

```text
grid-column: 10 / 13
height: 160px
```

### 内容

```text
P0 正常持有：6 只
P1 风险观察：2 只
P2 减仓警告：1 只
P3 退出触发：0 只
```

下方显示：

```text
今日新增风险：1
最高风险标的：XXXX
```

### 点击跳转

```text
点击卡片 → /portfolio
```

### API

```http
GET /api/portfolio/risk-summary
```

### 数据表来源

```text
position_tracking_daily
```

---

# 8. 第二行：强行业榜 + 今日买点 + AI Brief

---

## D｜强行业榜 Industry Rank Panel

### 位置

```text
grid-column: 1 / 6
height: 360px
```

### 内容

表格字段：

```text
排名
行业名称
状态
强度分
20日涨幅
成交额放大
RPS90占比
龙头股
```

示例：

| 排名 | 行业       | 状态 | 强度分 | 20日涨幅 | RPS90占比 | 龙头     |
| ---- | ---------- | ---- | -----: | -------: | --------: | -------- |
| 1    | 半导体设备 | I4   |     88 |   +18.6% |       35% | 北方华创 |
| 2    | AI算力     | I4   |     85 |   +16.2% |       32% | 中际旭创 |
| 3    | 机器人     | I3   |     76 |   +12.8% |       22% | 汇川技术 |

### 交互

| 操作     | 跳转                         |
| -------- | ---------------------------- |
| 点击行业 | `/industries/[sectorCode]` |
| 点击龙头 | `/stocks/[symbol]`         |
| 点击更多 | `/industries`              |

### API

```http
GET /api/industries/strength?date=latest&state=I3,I4&limit=8
```

### 数据表来源

```text
industry_strength_daily
stock_sector_map
stock_strength_daily
```

---

## E｜今日买点触发 Today Signals Panel

### 位置

```text
grid-column: 6 / 10
height: 360px
```

### 内容

Tab：

```text
全部
口袋买点
突破买点
抛压衰竭
业绩二次突破
```

表格字段：

```text
股票
行业
买点类型
结构分
止损距离
信号分
有效性
```

示例：

| 股票     | 行业       | 买点 | 结构分 | 止损 | 信号 |
| -------- | ---------- | ---- | -----: | ---: | ---- |
| 中微公司 | 半导体设备 | 突破 |     84 | 7.2% | 有效 |
| 杰普特   | 激光设备   | 口袋 |     78 | 6.8% | 有效 |
| 汇川技术 | 机器人     | 衰竭 |     82 | 7.5% | 观察 |

### 交互

| 操作         | 动作              |
| ------------ | ----------------- |
| 点击股票     | 跳转个股作战室    |
| 点击买点类型 | 打开信号解释      |
| 点击生成计划 | POST 生成交易计划 |

### API

```http
GET /api/signals/today?limit=8
POST /api/trade-plans/generate
```

### 数据表来源

```text
timing_signals_daily
structure_health_daily
stock_lifecycle_state
trade_plans
```

---

## F｜AI 每日作战简报 AI Daily Brief

### 位置

```text
grid-column: 10 / 13
height: 360px
```

### 内容

上方：

```text
AI Brief
今日结论
```

正文分三段：

```text
1. 市场判断
2. 主线判断
3. 明日观察
```

示例：

```text
今日市场处于 M3 偏强状态，适合围绕 I4 核心主线寻找前排强股。
半导体设备和 AI 算力仍是资金主战场，机器人方向进入 I3 强势状态。
建议优先关注 S4 结构准备池中止损距离小于 8% 的标的。
```

底部按钮：

```text
生成完整日报
重新生成
查看依据
```

### API

```http
GET /api/ai/daily-brief?date=latest
POST /api/ai/generate-daily-report
```

### 数据表来源

```text
ai_analysis_cache
daily_reports
market_strength_daily
industry_strength_daily
stock_lifecycle_state
```

---

# 9. 第三行：研究池动态 + 核心共振池 + 持仓风险

---

## G｜研究池动态 Research Themes in Motion

### 位置

```text
grid-column: 1 / 6
height: 320px
```

### 内容

展示你的手动研究主题是否被市场验证。

字段：

```text
研究主题
股票数
S3 数量
S4 数量
S5 数量
Core 数量
最近变化
```

示例：

| 研究主题     | 股票数 | S3 | S4 | S5 | 动作 |
| ------------ | -----: | -: | -: | -: | ---- |
| 激光器三大件 |     12 |  3 |  1 |  0 | 查看 |
| 半导体设备   |     25 |  8 |  4 |  1 | 查看 |
| 合成生物     |     18 |  2 |  0 |  0 | 查看 |

### 交互

```text
点击主题 → /research/[themeId]
点击 S4/S5 数字 → 过滤该主题内对应股票
```

### API

```http
GET /api/research/themes/in-motion
```

### 数据表来源

```text
research_themes
research_watchlist
research_stock_daily_snapshot
```

---

## H｜核心共振池 Core Alpha Pool

### 位置

```text
grid-column: 6 / 10
height: 320px
```

### 定义

Core Alpha Pool = 技术强度池 ∩ 产业研究池。

也就是：

```text
既是研究池标的
又进入强行业 / 强个股 / 结构健康 / 买点接近状态
```

### 内容字段

```text
股票
研究主题
行业状态
RPS 标签
结构标签
生命周期状态
下一步动作
```

示例：

| 股票     | 研究主题     | 行业 | RPS      | 结构     | 状态 |
| -------- | ------------ | ---- | -------- | -------- | ---- |
| 杰普特   | 激光器三大件 | I3   | 中期启动 | VCP      | S4   |
| 中微公司 | 半导体设备   | I4   | 主升     | 平台突破 | S5   |
| 柏楚电子 | 激光器三大件 | I3   | 长期强股 | MA20回踩 | S4   |

### API

```http
GET /api/alpha/core-pool?limit=8
```

### 数据表来源

```text
research_stock_daily_snapshot
stock_lifecycle_state
industry_strength_daily
stock_strength_daily
structure_health_daily
timing_signals_daily
```

---

## I｜持仓风险提醒 Portfolio Risk Alerts

### 位置

```text
grid-column: 10 / 13
height: 320px
```

### 内容

按风险等级列出：

```text
P1 风险观察
P2 减仓警告
P3 退出触发
```

字段：

```text
股票
当前状态
触发原因
下一步动作
```

示例：

| 股票  | 状态 | 原因      | 动作 |
| ----- | ---- | --------- | ---- |
| 股票A | P1   | 跌破 MA10 | 观察 |
| 股票B | P2   | 行业降级  | 减仓 |
| 股票C | P3   | 跌破止损  | 退出 |

### API

```http
GET /api/portfolio/risk-alerts?limit=6
```

### 数据表来源

```text
position_tracking_daily
```

---

# 10. 推荐 Dashboard 聚合 API

虽然可以前端分别请求多个 API，但首页建议做一个聚合接口，减少请求数量。

## API

```http
GET /api/dashboard/summary
```

## 返回结构

```ts
type DashboardSummary = {
  tradeDate: string
  updatedAt: string

  market: {
    state: 'M0' | 'M1' | 'M2' | 'M3' | 'M4'
    score: number
    suggestedPositionMin: number
    suggestedPositionMax: number
    summary: string
  }

  industries: {
    i4Count: number
    i3Count: number
    topIndustries: IndustrySummary[]
  }

  alpha: {
    s4Count: number
    s5Count: number
    coreCount: number
    todaySignals: SignalSummary[]
  }

  research: {
    themesInMotion: ResearchThemeMotion[]
    coreAlpha: CoreAlphaItem[]
  }

  portfolio: {
    p0Count: number
    p1Count: number
    p2Count: number
    p3Count: number
    riskAlerts: PortfolioRiskAlert[]
  }

  aiBrief: {
    briefId: string
    summary: string
    generatedAt: string
  }
}
```

---

# 11. Dashboard 后端数据依赖

第一页依赖这些核心表：

```text
market_strength_daily
industry_strength_daily
stock_strength_daily
structure_health_daily
timing_signals_daily
stock_lifecycle_state
research_themes
research_watchlist
research_stock_daily_snapshot
position_tracking_daily
ai_analysis_cache
daily_reports
trade_plans
```

---

# 12. Dashboard 技术栈声明

第一页使用：

```text
Next.js App Router
TypeScript
Tailwind CSS
shadcn/ui
TanStack Query
TanStack Table
Recharts 或 ECharts
Lucide React Icons
Zustand
```

## 具体选择

| 功能         | 技术               |
| ------------ | ------------------ |
| 页面框架     | Next.js App Router |
| 类型系统     | TypeScript         |
| 样式         | Tailwind CSS       |
| UI 组件      | shadcn/ui          |
| 表格         | TanStack Table     |
| 小型趋势图   | Recharts           |
| 行业图后续页 | ECharts            |
| 状态管理     | Zustand            |
| 数据请求     | TanStack Query     |
| 图标         | Lucide React       |
| 部署         | Vercel             |

---

# 13. Dashboard 文件结构规划

第一页建议涉及这些文件。

```text
evoalpha-web/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                                  # Dashboard 首页
│   └── api/
│       └── dashboard/
│           └── summary/
│               └── route.ts                      # GET /api/dashboard/summary
│
├── components/
│   ├── layout/
│   │   ├── AppSidebar.tsx
│   │   ├── TopBar.tsx
│   │   └── PageHeader.tsx
│   │
│   ├── dashboard/
│   │   ├── DashboardPage.tsx
│   │   ├── MarketStateCard.tsx
│   │   ├── StrongIndustriesCard.tsx
│   │   ├── AlphaOpportunityCard.tsx
│   │   ├── PortfolioRiskCard.tsx
│   │   ├── IndustryRankPanel.tsx
│   │   ├── TodaySignalsPanel.tsx
│   │   ├── AiDailyBriefPanel.tsx
│   │   ├── ResearchThemesPanel.tsx
│   │   ├── CoreAlphaPoolPanel.tsx
│   │   └── PortfolioRiskAlertsPanel.tsx
│   │
│   ├── shared/
│   │   ├── StatusBadge.tsx
│   │   ├── MetricCard.tsx
│   │   ├── MiniSparkline.tsx
│   │   ├── SectionCard.tsx
│   │   ├── EmptyState.tsx
│   │   └── LoadingSkeleton.tsx
│
├── lib/
│   ├── api/
│   │   └── dashboard.ts
│   ├── db/
│   │   └── dashboard-queries.ts
│   ├── types/
│   │   └── dashboard.ts
│   ├── constants/
│   │   └── states.ts
│   └── format.ts
│
└── styles/
    └── globals.css
```

---

## 13.1 Dashboard 文件数量规划

第一页大概需要：

| 类型                     |      数量 |
| ------------------------ | --------: |
| 页面文件                 |         1 |
| API Route                |         1 |
| Dashboard 专属组件       |        11 |
| Layout 组件              |         3 |
| Shared 组件              |         6 |
| Lib / Types / Query 文件 |         5 |
| 总计                     | 27 个左右 |

但 MVP 阶段可以合并，先控制在 12-15 个文件。

---

# 14. MVP 阶段文件简化版

如果我们先快速开发第一页，可以先这样：

```text
app/page.tsx
app/api/dashboard/summary/route.ts

components/layout/AppShell.tsx
components/dashboard/DashboardSummaryCards.tsx
components/dashboard/IndustryRankPanel.tsx
components/dashboard/TodaySignalsPanel.tsx
components/dashboard/ResearchThemesPanel.tsx
components/dashboard/CoreAlphaPoolPanel.tsx
components/dashboard/AiDailyBriefPanel.tsx

lib/types/dashboard.ts
lib/db/dashboard-queries.ts
lib/format.ts
```

MVP 版约 10 个核心文件即可。

---

# 15. Dashboard 页面开发顺序

## Step 1：先做静态 UI

不接 API，先用 mock 数据。

```text
AppShell
Sidebar
Topbar
4 张状态卡
强行业榜
今日买点
研究池动态
Core Alpha Pool
AI Brief
```

## Step 2：定义 TypeScript 类型

```text
DashboardSummary
IndustrySummary
SignalSummary
ResearchThemeMotion
CoreAlphaItem
PortfolioRiskAlert
```

## Step 3：实现 `/api/dashboard/summary`

先从 mock JSON 返回，后面接数据库。

## Step 4：接入真实数据库查询

从以下表查询：

```text
market_strength_daily
industry_strength_daily
stock_lifecycle_state
timing_signals_daily
research_stock_daily_snapshot
position_tracking_daily
ai_analysis_cache
```

## Step 5：加 loading / empty / error 状态

必须处理：

```text
数据未同步
今日不是交易日
没有 S5 信号
AI Brief 未生成
持仓为空
研究池为空
```

## Step 6：加交互跳转

```text
市场卡 → /market
行业行 → /industries/[sectorCode]
股票行 → /stocks/[symbol]
研究主题 → /research/[themeId]
买点信号 → /signals
持仓风险 → /portfolio
```

---

# 16. Dashboard 最终效果描述

第一页最终应该是这样的体验：

> 用户打开系统，首先看到市场是 M3 偏强，知道今天可以正常参与；
> 接着看到半导体设备和 AI 算力是 I4 核心主线；
> 然后看到今天有 5 只 S5 买点触发，其中 2 只来自自己的研究池；
> 同时看到激光器三大件研究主题中有 1 只股票进入 S4；
> 最后看到持仓里有 1 只股票进入 P2 减仓警告。
>
> 于是用户今天的任务很明确：
> 看两只 S5 机会，跟踪一只研究池 S4 标的，处理一只 P2 风险持仓。

这就是 Dashboard 的真正目的：

> **不是展示数据，而是组织今天的行动。**
>
