可以。你现在这页已经有雏形了，但它现在更像  **Settings 设置页** ，还没有变成我们真正需要的  **DataOps Center / 数据状态与任务编排中心** 。

我建议把这个页面重新定义为：

# Page 00｜DataOps Center 数据状态与生产流水线控制台

它不是普通设置页，而是整个系统的  **数据工厂驾驶舱** 。

它要回答 5 个问题：

```text
1. 当前数据是否可用？
2. 哪些数据源已配置、哪些未配置？
3. 今天的数据采集和计算跑到哪一步了？
4. 哪个任务失败了，失败原因是什么？
5. 一键执行完整生产流水线时，后台实际做了哪些步骤？
```

---

# 1. 你当前页面的问题和保留点

## 可以保留的部分

你现在这版已经有几个方向是对的：

```text
顶部状态卡：本地库日期、数据库体积、健康表、后台任务、R2同步、云端库
数据源管理：mootdx / 多源自动 / AkShare
任务控制台：当日K线下载、RPS计算、完整生产流水线
数据表健康：这个方向非常关键
```

这些都应该保留。

## 主要问题

现在的问题是：

```text
1. 页面叫 Settings，但实际承担的是数据状态页，定位不清。
2. 顶部状态卡有了，但没有告诉用户“系统今天是否可用”。
3. 数据源按钮和后端任务没有形成明确映射。
4. 任务列表像菜单，但没有“流水线依赖关系”。
5. 执行任务后缺少统一进度条、步骤进度、日志、失败原因。
6. 数据流没有可视化，用户不知道“采集 → 计算 → 同步 → 前端展示”之间的关系。
```

所以这页要改成  **数据生产流水线控制台** 。

---

# 2. 页面新的核心定位

建议页面名称改成：

```text
DataOps / 数据中枢
```

或者：

```text
Data Center / 数据状态
```

侧边栏可以从现在的 Settings 调整为：

```text
DataOps
数据中枢
```

Settings 可以保留给以后做 API Key、主题、用户权限、策略参数配置。

---

# 3. 整体数据流设计

我们先把系统的数据流统一清楚。

完整链路应该是：

```text
P0 数据源配置与预检查
    ↓
P1 行情数据采集
    ↓
P2 基础数据清洗与补齐
    ↓
P3 技术指标与 RPS 计算
    ↓
P4 市场强度 / 行业强度计算
    ↓
P5 结构健康 / 买点信号 / 生命周期状态
    ↓
P6 研究池快照 / Alpha 机会池
    ↓
P7 云端同步 / 前端展示
```

展开来看：

```text
数据源
  ├─ mootdx 前复权
  ├─ AkShare
  ├─ 东方财富 / efinance
  └─ 手动 CSV / YAML 研究池

采集层
  ├─ 股票日线
  ├─ 指数日线
  ├─ 行业日线
  ├─ 股票行业映射
  ├─ 基金持股 / 北向快照
  └─ 新闻公告，可后置

计算层
  ├─ 均线 / 成交额 / 涨跌幅
  ├─ RPS5 / 20 / 50 / 120 / 250
  ├─ 市场强度 M0-M4
  ├─ 行业强度 I0-I4
  ├─ 结构健康 S4
  ├─ 买点信号 S5
  └─ 生命周期 S0-S7

输出层
  ├─ market_strength_daily
  ├─ industry_strength_daily
  ├─ stock_strength_daily
  ├─ structure_health_daily
  ├─ timing_signals_daily
  ├─ stock_lifecycle_state
  ├─ research_stock_daily_snapshot
  └─ alpha_opportunities

同步层
  ├─ 本地 SQLite
  ├─ CSV / Parquet 导出
  ├─ R2 上传
  └─ CockroachDB / 云端库导入

前端
  ├─ Market 市场温度
  ├─ Industries 行业战役
  ├─ Alpha Pool 机会池
  ├─ Research 产业研究池
  └─ Stock War Room 个股作战室
```

---

# 4. 页面布局重新规划

建议把现在这一页改成 6 个区域。

```text
┌──────────────────────────────────────────────────────────────┐
│ A. DataOps Header：数据中枢 / 运行状态 / 当前交易日 / 刷新       │
├──────────────────────────────────────────────────────────────┤
│ B. System Readiness Hero：今日系统是否可用                      │
├──────────────────────────────────────────────────────────────┤
│ C. 数据源与环境状态卡                                           │
├──────────────────────────────┬───────────────────────────────┤
│ D. 生产流水线 Pipeline         │ E. 任务执行面板                  │
├──────────────────────────────┴───────────────────────────────┤
│ F. 当前任务进度 / 日志 / 错误                                   │
├──────────────────────────────────────────────────────────────┤
│ G. 数据表健康检查                                               │
└──────────────────────────────────────────────────────────────┘
```

---

# 5. A 区：DataOps Header

## 位置

顶部标题区。

## 内容

```text
DATAOPS / 数据中枢
数据源、生产流水线、计算任务、同步状态与执行日志
```

右侧按钮：

```text
当前交易日：2026-04-29
刷新状态
一键检查
运行完整流水线
```

## 交互

| 按钮           | 后端动作                                                |
| -------------- | ------------------------------------------------------- |
| 刷新状态       | GET `/api/data/status`                                |
| 一键检查       | POST `/api/data/health/check`                         |
| 运行完整流水线 | POST `/api/jobs/run`，job_key=`daily_full_pipeline` |

---

# 6. B 区：System Readiness Hero

这个区域要代替现在单纯的状态卡。

它的核心结论是：

```text
今日系统状态：可用 / 部分可用 / 不可用
```

## 建议展示

```text
系统可用性：部分可用
原因：本地行情已更新，RPS 已完成，云端库未配置，R2 已配置但长线同步关闭

当前交易日：2026-04-29
本地最新数据：2026-04-29
市场强度数据：已生成
行业强度数据：已生成
Alpha 机会池：已生成
云端同步：未配置
```

## 状态分级

```text
READY：今日系统可用
PARTIAL：核心数据可用，但部分同步/研究池/云端未完成
BLOCKED：核心行情或 RPS 未完成，系统不可用
ERROR：任务失败，需要处理
```

## API

```http
GET /api/data/status
```

返回：

```ts
type DataSystemStatus = {
  status: 'READY' | 'PARTIAL' | 'BLOCKED' | 'ERROR'
  tradeDate: string
  latestLocalDate: string
  marketReady: boolean
  industryReady: boolean
  rpsReady: boolean
  lifecycleReady: boolean
  cloudSyncReady: boolean
  summary: string
  blockers: string[]
  warnings: string[]
}
```

---

# 7. C 区：数据源与环境状态卡

你现在的顶部卡片可以改成这一组。

## 7.1 状态卡列表

```text
本地库日期
数据库体积
数据表健康
后台任务
R2 同步
云端库
主数据源
最近流水线
```

## 7.2 每张卡的含义

| 卡片       | 显示内容                | 用途                     |
| ---------- | ----------------------- | ------------------------ |
| 本地库日期 | 2026-04-29              | 判断本地行情是否最新     |
| 数据库体积 | 151G                    | 监控本地库膨胀           |
| 数据表健康 | 14张表，异常3           | 快速发现缺表/断更        |
| 后台任务   | 0运行 / 1失败           | 判断是否有任务卡住       |
| R2 同步    | 已配置 / 未配置         | 判断文件层是否可用       |
| 云端库     | 已配置 / 未配置         | 判断前端真实展示是否可用 |
| 主数据源   | mootdx / auto / akshare | 当前任务默认源           |
| 最近流水线 | 成功 / 失败 / 部分成功  | 判断上次运行结果         |

---

# 8. D 区：生产流水线 Pipeline

这是最关键的改造点。

不要只做任务列表，要做成  **流水线步骤图** 。

## 8.1 Pipeline 阶段

```text
P0 预检查
P1 行情采集
P2 数据补齐
P3 RPS计算
P4 市场/行业强度
P5 结构/买点/生命周期
P6 研究池快照
P7 云端同步
```

每个阶段展示：

```text
状态：未运行 / 运行中 / 成功 / 警告 / 失败 / 跳过
耗时
进度
输出表
失败原因
```

## 8.2 页面展示方式

建议用横向 Stepper 或竖向 Timeline。

示例：

```text
P0 预检查              ✅ 成功  8s
P1 行情采集            ✅ 成功  12m
P2 数据补齐            ✅ 成功  2m
P3 RPS计算             ✅ 成功  4m
P4 市场/行业强度        ✅ 成功  1m
P5 结构/买点/生命周期   ⚠️ 警告  S5为空
P6 研究池快照          ✅ 成功  20s
P7 云端同步            ⏭ 跳过  云端库未配置
```

## 8.3 API

```http
GET /api/jobs/latest?job_key=daily_full_pipeline
```

或者：

```http
GET /api/jobs/runs/{runId}
```

返回：

```ts
type JobRun = {
  runId: string
  jobKey: string
  jobName: string
  status: 'PENDING' | 'RUNNING' | 'SUCCESS' | 'WARNING' | 'FAILED' | 'CANCELLED'
  progress: number
  startedAt: string
  endedAt?: string
  currentStep?: string
  steps: JobStep[]
}

type JobStep = {
  stepKey: string
  stepName: string
  status: 'PENDING' | 'RUNNING' | 'SUCCESS' | 'WARNING' | 'FAILED' | 'SKIPPED'
  progress: number
  startedAt?: string
  endedAt?: string
  durationMs?: number
  outputTables: string[]
  message?: string
  error?: string
}
```

---

# 9. E 区：任务执行面板

现在你这块已经有任务列表和右侧参数表单，方向是对的，但要更系统化。

## 9.1 任务分组

建议分成 4 组：

```text
数据采集任务
计算任务
组合流水线
同步任务
```

---

## 9.2 任务列表

### 数据采集任务

```text
当日股票K线下载
区间股票K线补齐
指数K线下载
行业K线下载
股票行业映射更新
机构持股快照更新
```

### 计算任务

```text
基础指标计算
RPS 计算
市场强度计算
行业强度计算
结构健康计算
买点信号计算
生命周期状态生成
研究池每日快照
```

### 组合流水线

```text
P0 核心计算
P1 买点与风控
K线 + RPS 快速链路
完整生产流水线
```

### 同步任务

```text
导出 CSV / Parquet
上传 R2
导入云端数据库
生成日报缓存
```

---

## 9.3 右侧任务参数面板

用户点击左侧任务后，右侧显示参数。

例如：当日 K 线下载

```text
任务名称：当日K线下载
数据源：mootdx 前复权 / auto / akshare
交易日：2026-04-29
回看天数：7
模式：增量 / 覆盖 / 校验
股票范围：全市场 / 指定股票 / 研究池
```

底部按钮：

```text
执行任务
模拟运行
查看上次日志
```

---

## 9.4 按钮和后端操作映射

| 前端按钮       | job_key                      | 后端函数                                   |
| -------------- | ---------------------------- | ------------------------------------------ |
| 当日K线下载    | `download_daily_kline`     | `update_stock_kline(mode='incremental')` |
| K线区间补齐    | `backfill_kline`           | `update_stock_kline(mode='backfill')`    |
| 指数K线下载    | `download_index_kline`     | `update_index_kline()`                   |
| RPS计算        | `calc_rps`                 | `calc_rps(periods=[5,20,50,120,250])`    |
| 市场强度计算   | `calc_market_strength`     | `calc_market_strength()`                 |
| 行业强度计算   | `calc_industry_strength`   | `calc_industry_strength()`               |
| 结构健康计算   | `calc_structure_health`    | `calc_structure_health()`                |
| 买点信号计算   | `calc_timing_signals`      | `calc_timing_signals()`                  |
| 生命周期生成   | `update_lifecycle_state`   | `update_lifecycle_state()`               |
| 研究池快照     | `update_research_snapshot` | `update_research_snapshot()`             |
| 完整生产流水线 | `daily_full_pipeline`      | `run_daily_data_workflow()`              |
| 上传 R2        | `sync_r2`                  | `export_and_upload_r2()`                 |
| 同步云端库     | `sync_cloud_db`            | `import_to_cloud_db()`                   |

---

# 10. F 区：当前任务进度 / 日志 / 错误

这是现在最缺的部分。

执行任务后，页面必须出现一个统一的任务运行面板。

## 10.1 显示内容

```text
当前任务：完整生产流水线
状态：运行中
总进度：42%
当前步骤：RPS计算
当前处理：3021 / 5362 股票
已耗时：03:42
预计剩余：04:15
```

## 10.2 进度条分三层

### 第一层：总进度

```text
完整生产流水线：42%
```

### 第二层：阶段进度

```text
P3 RPS计算：67%
```

### 第三层：当前子任务进度

```text
当前股票：300750 宁德时代
处理进度：3021 / 5362
```

---

## 10.3 实时日志

日志格式：

```text
[15:31:02] 开始任务 daily_full_pipeline
[15:31:08] P0 预检查完成
[15:31:10] 开始下载当日K线，数据源=mootdx
[15:34:22] 股票K线下载完成，成功 5312，失败 50
[15:34:23] 开始 RPS 计算
```

## 10.4 错误展示

失败时必须清楚显示：

```text
失败步骤：P1 行情采集
错误原因：mootdx 连接超时
失败股票：600519, 300750...
建议动作：切换到 auto 数据源重试
```

---

# 11. 实时进度实现方案

为了尽快可用，我建议分两阶段。

## 阶段一：轮询

前端每 2 秒请求：

```http
GET /api/jobs/runs/{runId}
```

优点：

```text
实现简单
适合本地开发
稳定可靠
```

## 阶段二：SSE

后续升级为：

```http
GET /api/jobs/runs/{runId}/events
```

用 Server-Sent Events 推送进度。

## 阶段三：WebSocket

只有等任务系统复杂后再考虑。

我建议现在先用  **轮询** ，不要一开始上 WebSocket。

---

# 12. G 区：数据表健康检查

这个区域现在已经有雏形，但要更明确。

## 12.1 表格字段

```text
表名
用途
最新交易日
行数
今日是否更新
缺失天数
异常数量
状态
操作
```

## 12.2 核心表列表

```text
stock_daily_prices
index_daily_prices
sector_daily_prices
stock_sector_map
stock_strength_daily
market_strength_daily
industry_strength_daily
structure_health_daily
timing_signals_daily
stock_lifecycle_state
research_stock_daily_snapshot
alpha_opportunities
position_tracking_daily
trade_plans
```

## 12.3 状态规则

```text
OK：最新日期等于当前交易日
STALE：落后 1-3 个交易日
MISSING：表为空或关键日期缺失
ERROR：数据异常
```

## 12.4 API

```http
GET /api/data/tables/health
```

返回：

```ts
type DataTableHealth = {
  tableName: string
  displayName: string
  latestTradeDate?: string
  rowCount: number
  expectedLatestDate?: string
  missingDays: number
  anomalyCount: number
  status: 'OK' | 'STALE' | 'MISSING' | 'ERROR'
  lastUpdatedAt: string
}
```

---

# 13. 数据源管理重新设计

你现在有：

```text
mootdx 前复权
多源自动
AkShare 旧采集器
```

这个模块要保留，但按钮含义要更清楚。

## 13.1 数据源卡片字段

```text
数据源名称
适用任务
速度
稳定性
是否已配置
是否为主数据源
最近成功时间
失败次数
```

## 13.2 按钮

不要只写“设为任务源”，建议改成：

```text
设为主源
设为备用源
测试连接
查看日志
```

## 13.3 数据源策略

建议默认：

```text
主源：mootdx 前复权
备用源：AkShare
调度策略：auto fallback
```

## 13.4 API

```http
GET /api/data/sources
PATCH /api/data/sources/{sourceKey}
POST /api/data/sources/{sourceKey}/test
```

---

# 14. 后端任务系统设计

这里是最关键的底座。

## 14.1 不建议由 Next.js 直接执行长任务

Next.js / Vercel 适合前端和轻 API，不适合长时间量化任务。

正确方式：

```text
前端按钮
  ↓
Next.js API Route
  ↓
本地 Python Worker / FastAPI
  ↓
任务执行
  ↓
写入 job_runs / job_steps / logs
  ↓
前端轮询状态
```

本地开发可以这样：

```text
Next.js: localhost:3000
Python Worker: localhost:8000
SQLite: local_quant.db
```

---

## 14.2 任务表设计

### job_runs

```sql
CREATE TABLE job_runs (
    run_id TEXT PRIMARY KEY,
    job_key TEXT NOT NULL,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL,
    progress NUMERIC DEFAULT 0,

    params JSON,
    current_step TEXT,

    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_ms INTEGER,

    message TEXT,
    error TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### job_run_steps

```sql
CREATE TABLE job_run_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    step_name TEXT NOT NULL,

    status TEXT NOT NULL,
    progress NUMERIC DEFAULT 0,

    current_item TEXT,
    processed_count INTEGER DEFAULT 0,
    total_count INTEGER DEFAULT 0,

    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_ms INTEGER,

    output_tables JSON,
    message TEXT,
    error TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### job_logs

```sql
CREATE TABLE job_logs (
    log_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_key TEXT,
    level TEXT,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

# 15. 前端 API 设计

## 15.1 数据状态

```http
GET /api/data/status
GET /api/data/tables/health
GET /api/data/sources
```

## 15.2 任务执行

```http
GET /api/jobs/templates
POST /api/jobs/run
GET /api/jobs/runs
GET /api/jobs/runs/{runId}
POST /api/jobs/runs/{runId}/cancel
```

## 15.3 日志

```http
GET /api/jobs/runs/{runId}/logs
```

## 15.4 示例：执行任务

```http
POST /api/jobs/run
```

请求：

```json
{
  "jobKey": "daily_full_pipeline",
  "params": {
    "tradeDate": "2026-04-29",
    "dataSource": "auto",
    "lookbackDays": 7,
    "syncCloud": false
  }
}
```

返回：

```json
{
  "runId": "run_20260429_153001",
  "status": "RUNNING"
}
```

---

# 16. 前端组件规划

当前页面可以拆成这些组件：

```text
DataOpsPage
├── DataOpsHeader
├── SystemReadinessHero
├── DataStatusCards
├── DataSourceManager
├── PipelineStepper
├── TaskConsole
│   ├── TaskTemplateList
│   └── TaskParamPanel
├── CurrentJobProgress
│   ├── JobProgressBar
│   ├── StepProgressList
│   └── JobLogViewer
└── DataTableHealthPanel
```

---

# 17. 文件结构规划

```text
evoalpha-web/
├── app/
│   ├── data/
│   │   └── page.tsx
│   └── api/
│       ├── data/
│       │   ├── status/
│       │   │   └── route.ts
│       │   ├── sources/
│       │   │   └── route.ts
│       │   └── tables/
│       │       └── health/
│       │           └── route.ts
│       └── jobs/
│           ├── templates/
│           │   └── route.ts
│           ├── run/
│           │   └── route.ts
│           └── runs/
│               └── [runId]/
│                   ├── route.ts
│                   ├── logs/
│                   │   └── route.ts
│                   └── cancel/
│                       └── route.ts
│
├── components/
│   ├── dataops/
│   │   ├── DataOpsPage.tsx
│   │   ├── DataOpsHeader.tsx
│   │   ├── SystemReadinessHero.tsx
│   │   ├── DataStatusCards.tsx
│   │   ├── DataSourceManager.tsx
│   │   ├── PipelineStepper.tsx
│   │   ├── TaskConsole.tsx
│   │   ├── TaskTemplateList.tsx
│   │   ├── TaskParamPanel.tsx
│   │   ├── CurrentJobProgress.tsx
│   │   ├── JobLogViewer.tsx
│   │   └── DataTableHealthPanel.tsx
│
├── lib/
│   ├── api/
│   │   ├── dataops.ts
│   │   └── jobs.ts
│   ├── types/
│   │   ├── dataops.ts
│   │   └── jobs.ts
│   └── constants/
│       └── job-templates.ts
```

---

# 18. 第一版 MVP 要做到什么

为了尽快可用，这页不要一上来做太复杂。

## MVP 必须完成

```text
1. 顶部系统可用性 Hero
2. 数据状态卡
3. 数据源管理卡
4. 任务模板列表
5. 点击任务后显示参数
6. 点击执行后创建 job_run
7. 前端轮询显示任务进度
8. 显示 job_steps
9. 显示 job_logs
10. 数据表健康检查表
```

## 暂时可以不做

```text
SSE 实时推送
复杂权限系统
任务定时调度
任务依赖可视化编辑器
云端部署任务执行
高级错误自动修复
```

---

# 19. 你现在这个页面的具体改造建议

从截图看，我建议这样改：

## 19.1 标题改掉

当前：

```text
09 Settings 系统设置
```

建议改为：

```text
00 DataOps 数据中枢
```

副标题：

```text
数据源、生产流水线、计算任务、同步状态与执行日志
```

---

## 19.2 顶部状态卡增加“系统可用性”

现在卡片是散的。建议最左边新增一张大卡：

```text
今日系统状态：部分可用
核心数据：已完成
云端同步：未配置
下一步：可运行 Market / Industries / Alpha Pool
```

---

## 19.3 任务控制台改成左右结构

左侧不要只是列表，要分组：

```text
数据采集
计算任务
组合流水线
同步任务
```

右侧显示：

```text
任务说明
参数
依赖
输出表
执行按钮
```

---

## 19.4 中间必须出现 Pipeline

现在你没有“完整生产流水线”的视觉结构。建议加一个横向 Pipeline：

```text
预检查 → K线采集 → RPS → 市场强度 → 行业强度 → 结构买点 → 生命周期 → 同步
```

---

## 19.5 数据表健康放到下方完整展示

不要只露出一个标题。它应该是这一页的核心结果之一。

---

# 20. 给开发模型的完整指令

你可以把下面这段直接给 Claude Code / Cursor：

```text
请重构 /data 页面，将其从 Settings 页面升级为 DataOps 数据中枢页面。

目标：
这个页面用于管理 EvoAlpha OS 的数据源、生产流水线、任务执行、进度日志和数据表健康状态。

技术栈：
Next.js App Router + TypeScript + Tailwind CSS + shadcn/ui + TanStack Query。

页面结构：
1. 页面标题改为：00 DataOps 数据中枢
   副标题：数据源、生产流水线、计算任务、同步状态与执行日志。
2. 顶部增加 SystemReadinessHero，展示：
   - 今日系统状态：READY / PARTIAL / BLOCKED / ERROR
   - 当前交易日
   - 本地最新数据日期
   - RPS是否完成
   - 市场强度是否完成
   - 行业强度是否完成
   - 生命周期是否完成
   - 云端同步是否完成
   - blockers 和 warnings
3. 保留并优化顶部状态卡：
   本地库日期、数据库体积、数据表健康、后台任务、R2同步、云端库、主数据源、最近流水线。
4. 增加 PipelineStepper：
   P0预检查 → P1行情采集 → P2数据补齐 → P3 RPS计算 → P4市场/行业强度 → P5结构/买点/生命周期 → P6研究池快照 → P7云端同步。
   每一步展示状态、进度、耗时和输出表。
5. 重构任务控制台：
   左侧 TaskTemplateList 分组展示：
   - 数据采集任务
   - 计算任务
   - 组合流水线
   - 同步任务
   右侧 TaskParamPanel 展示所选任务的参数、依赖、输出表和执行按钮。
6. 点击执行任务后调用 POST /api/jobs/run，返回 runId。
7. 前端每2秒轮询 GET /api/jobs/runs/{runId}，更新 CurrentJobProgress。
8. CurrentJobProgress 展示：
   - 总进度条
   - 当前步骤
   - 当前处理数量 / 总数量
   - Step 列表
   - 实时日志
   - 错误原因
9. 增加 DataTableHealthPanel，展示核心数据表：
   stock_daily_prices、index_daily_prices、sector_daily_prices、stock_strength_daily、market_strength_daily、industry_strength_daily、structure_health_daily、timing_signals_daily、stock_lifecycle_state、research_stock_daily_snapshot。
   字段包括：表名、最新交易日、行数、缺失天数、异常数、状态。
10. 卡片风格保持现有深色现代风格：
    bg-slate-950/60 border border-white/10 rounded-2xl。
11. 先用 mock API 数据实现完整 UI，后续再接真实 Python Worker。
```

---

# 21. 最终效果目标

这个页面完成后，你每天的第一步应该是：

```text
打开 DataOps
    ↓
确认系统状态 READY / PARTIAL
    ↓
如果数据没更新，点击完整生产流水线
    ↓
观察进度条和日志
    ↓
确认 market_strength / industry_strength / stock_lifecycle_state 已生成
    ↓
再进入 Market、Industries、Alpha Pool 页面
```

也就是说：

> **DataOps 页是系统能不能工作的总开关。**

现在你已经有了 Settings 页的雏形，下一步不是换风格，而是把它升级成真正的：

```text
数据状态 + 任务编排 + 实时进度 + 数据健康
```

这个页面做好，后面 Market、Industries、Alpha Pool 的数据可信度才有保障。
