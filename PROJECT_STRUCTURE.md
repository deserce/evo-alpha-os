# EvoAlpha OS - 项目结构说明

> **版本**: v2.1.0
> **最后更新**: 2026-01-19
> **状态**: ✅ 数据采集系统已完成

## 📂 完整目录树

```
EvoAlpha-OS/
├── BLUEPRINT.md              # 📘 技术蓝图（完整系统设计）
├── README.md                 # 📄 项目说明
├── DOCS_INDEX.md             # 📚 文档索引（新增）
├── CLAUDE.md                 # 📝 开发日志
├── PROJECT_STRUCTURE.md      # 📁 本文件
├── .gitignore                # Git忽略配置
│
├── docs/                     # 📖 文档目录
│   └── archive/              # 归档文档
│       ├── README.md         # 归档说明
│       ├── PROJECT_PLAN.md   # 项目初始化计划（已归档）
│       ├── MIGRATION_PLAN.md # 代码移植计划（已归档）
│       └── DATA_ANALYSIS.md  # 数据分析报告（已归档）
│
├── .env.example              # 环境变量模板
│
├── backend/                  # 🔧 Python 后端
│   ├── main.py               # FastAPI 应用入口（云端）
│   ├── requirements.txt      # Python 依赖
│   │
│   ├── app/                  # ☁️ 云端后端模块
│   │   ├── __init__.py
│   │   ├── api/              # API 接口层
│   │   │   ├── __init__.py
│   │   │   ├── alpha.py      # Alpha 机会接口
│   │   │   ├── stock.py      # 个股接口
│   │   │   ├── sector.py     # 板块接口
│   │   │   ├── etf.py        # ETF 接口
│   │   │   ├── report.py     # 日报接口
│   │   │   └── ai.py         # AI 分析接口
│   │   │
│   │   ├── agents/           # 🤖 AI Agent 层
│   │   │   ├── __init__.py
│   │   │   ├── master_agents.py      # 大师 Agent
│   │   │   ├── functional_agents.py  # 功能 Agent
│   │   │   └── report_agents.py      # 报告生成 Agent
│   │   │
│   │   ├── core/             # 🔒 核心配置
│   │   │   ├── __init__.py
│   │   │   ├── config.py     # 配置管理 ✅
│   │   │   └── cloud_db.py   # 云端数据库 ✅
│   │   │
│   │   ├── sync/             # 🔄 同步模块（本地工厂）
│   │   │   ├── __init__.py
│   │   │   ├── csv_exporter.py      # CSV 导出 ✅
│   │   │   ├── r2_manager.py        # R2 上传 ✅
│   │   │   └── cloud_importer.py    # 云端导入 ✅
│   │   │
│   │   └── scheduler/        # ⏰ 定时任务
│   │       ├── __init__.py
│   │       ├── daily_job.py  # 每日任务
│   │       └── email_sender.py  # 邮件发送
│   │
│   ├── data_job/             # 📊 数据采集系统 v2.1.0 ✅
│   │   ├── README.md         # 数据采集系统主文档
│   │   ├── requirements.txt  # Python 依赖清单
│   │   │
│   │   ├── core/             # 核心框架层
│   │   │   └── base_collector.py
│   │   ├── common/           # 公共工具层
│   │   │   ├── network_utils.py
│   │   │   ├── path_utils.py
│   │   │   ├── logger_utils.py
│   │   │   └── exception_utils.py
│   │   ├── config/           # 配置管理层
│   │   │   ├── collector_config.py
│   │   │   └── collection_schedule.yaml
│   │   ├── collectors/       # 12个数据采集器
│   │   │   ├── stock_kline_collector.py
│   │   │   ├── sector_kline_collector.py
│   │   │   ├── etf_kline_collector.py
│   │   │   ├── stock_valuation_collector.py
│   │   │   ├── limit_boards_collector.py
│   │   │   ├── news_collector.py
│   │   │   ├── fund_holdings_collector.py
│   │   │   ├── northbound_holdings_collector.py
│   │   │   ├── etf_info_collector.py
│   │   │   ├── finance_summary_collector.py
│   │   │   ├── macro_data_collector.py
│   │   │   └── stock_sector_list_collector.py
│   │   ├── utils/            # 工具脚本
│   │   │   └── scheduler.py  # 定时调度器
│   │   ├── scripts/          # 独立脚本
│   │   │   └── init_data_collection.py
│   │   ├── docs/             # 数据采集文档
│   │   │   ├── QUICKSTART.md
│   │   │   ├── ARCHITECTURE.md
│   │   │   └── DEVELOPMENT_GUIDE.md
│   │   └── backup/           # 归档文件
│   │
│   ├── quant_engine/         # ⚡ 量化引擎（本地工厂）
│   │   ├── __init__.py
│   │   ├── core/             # 核心工具
│   │   │   ├── __init__.py
│   │   │   ├── db_init.py    # 数据库初始化
│   │   │   └── tdx_lib.py    # 通达信公式库
│   │   │
│   │   ├── features/         # 因子计算
│   │   │   ├── __init__.py
│   │   │   ├── calc_rps.py   # RPS 因子 ✅
│   │   │   ├── calc_indicators.py  # 技术指标
│   │   │   └── calc_sector_rps.py  # 板块 RPS
│   │   │
│   │   ├── pool/             # 股票池管理
│   │   │   ├── __init__.py
│   │   │   └── maintain_pool.py    # 核心池维护
│   │   │
│   │   └── strategies/       # 策略执行
│   │       ├── __init__.py
│   │       ├── base_strategy.py    # 策略基类
│   │       ├── mrgc_strategy.py     # MRGC 策略 ✅
│   │       └── sector_resonance.py # 板块共振
│   │
│   └── scripts/              # 🔧 实用脚本
│       ├── __init__.py
│       ├── init_db.py        # 初始化数据库
│       └── test_sync.py      # 测试同步
│
└── frontend/                 # 🎨 Next.js 前端
    ├── package.json          # Node 依赖 ✅
    ├── next.config.ts        # Next.js 配置 ✅
    ├── tsconfig.json         # TypeScript 配置 ✅
    ├── tailwind.config.ts    # Tailwind 配置 ✅
    │
    └── src/
        ├── app/              # 页面路由
        │   ├── layout.tsx    # 根布局 ✅
        │   ├── page.tsx      # 首页 ✅
        │   └── globals.css   # 全局样式 ✅
        │
        ├── components/       # React 组件
        │   ├── alpha/        # Alpha 机会组件
        │   ├── stock/        # 个股组件
        │   ├── sector/       # 板块组件
        │   ├── etf/          # ETF 组件
        │   ├── common/       # 通用组件
        │   └── ui/           # UI 组件
        │
        ├── lib/              # 工具库
        │   ├── api.ts        # API 客户端
        │   ├── utils.ts      # 工具函数
        │   └── store.ts      # 状态管理
        │
        └── styles/           # 样式文件
            └── globals.css
```

---

## 📌 文件说明

### ✅ 已创建（占位文件）

| 文件 | 说明 | 状态 |
|------|------|------|
| `.env.example` | 环境变量模板 | ✅ 已创建 |
| `backend/main.py` | FastAPI 应用入口 | ✅ 已创建 |
| `backend/requirements.txt` | Python 依赖 | ✅ 已创建 |
| `backend/app/core/config.py` | 配置管理 | ✅ 已创建 |
| `backend/app/core/cloud_db.py` | 云端数据库连接 | ✅ 已创建 |
| `backend/app/sync/csv_exporter.py` | CSV 导出器 | ✅ 已创建 |
| `backend/app/sync/r2_manager.py` | R2 上传管理器 | ✅ 已创建 |
| `backend/app/sync/cloud_importer.py` | 云端导入器 | ✅ 已创建 |
| `backend/data_job/update_stock_list.py` | 股票名单更新 | ✅ 已创建 |
| `backend/data_job/update_kline.py` | K 线数据更新 | ✅ 已创建 |
| `backend/quant_engine/features/calc_rps.py` | RPS 因子计算 | ✅ 已创建 |
| `backend/quant_engine/strategies/mrgc_strategy.py` | MRGC 策略 | ✅ 已创建 |
| `frontend/package.json` | Node 依赖 | ✅ 已创建 |
| `frontend/next.config.ts` | Next.js 配置 | ✅ 已创建 |
| `frontend/tsconfig.json` | TypeScript 配置 | ✅ 已创建 |
| `frontend/tailwind.config.ts` | Tailwind 配置 | ✅ 已创建 |
| `frontend/src/app/layout.tsx` | 根布局 | ✅ 已创建 |
| `frontend/src/app/page.tsx` | 首页 | ✅ 已创建 |
| `frontend/src/app/globals.css` | 全局样式 | ✅ 已创建 |

### 🚧 待创建（后续开发）

| 模块 | 文件 | 说明 |
|------|------|------|
| **API 接口** | `backend/app/api/*.py` | 各模块 API 接口 |
| **AI Agent** | `backend/app/agents/*.py` | AI 分析 Agent |
| **定时任务** | `backend/app/scheduler/*.py` | 定时任务调度 |
| **新闻采集** | `backend/data_job/update_news.py` | 新闻舆情采集 |
| **技术指标** | `backend/quant_engine/features/calc_indicators.py` | 技术指标计算 |
| **股票池** | `backend/quant_engine/pool/maintain_pool.py` | 核心池维护 |
| **前端组件** | `frontend/src/components/**/*.tsx` | React 组件 |

---

## 🔄 数据流转

### 本地工厂（MBP）工作流

```
1. 数据采集（data_job/）
   ├── update_stock_list.py → 股票/板块名单
   ├── update_kline.py → K线数据
   └── update_news.py → 新闻舆情

2. 量化计算（quant_engine/）
   ├── features/calc_rps.py → RPS 因子
   ├── pool/maintain_pool.py → 股票池筛选
   └── strategies/mrgc_strategy.py → 策略信号

3. 同步云端（app/sync/）
   ├── csv_exporter.py → 导出 CSV
   ├── r2_manager.py → 上传 R2
   └── cloud_importer.py → 触发云端 IMPORT
```

### 云端后端（VPS）工作流

```
1. 接收前端请求（app/api/）
   ├── alpha.py → Alpha 机会
   ├── stock.py → 个股详情
   └── ai.py → AI 分析

2. AI 分析（app/agents/）
   ├── master_agents.py → 大师 Agent
   └── report_agents.py → 日报生成

3. 定时任务（app/scheduler/）
   ├── daily_job.py → 每日任务
   └── email_sender.py → 邮件推送
```

---

## 🚀 快速开始

### 本地工厂初始化

```bash
# 1. 安装依赖
cd backend
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入真实值

# 3. 初始化数据库
python -m scripts.init_db

# 4. 首次数据采集
python -m data_job.update_stock_list
python -m data_job.update_kline

# 5. 计算量化因子
python -m quant_engine.features.calc_rps

# 6. 运行策略
python -m quant_engine.strategies.mrgc_strategy
```

### 云端后端部署

```bash
# 1. 推送到云端
git push origin main

# 2. Railway 部署（自动）
# 或使用 Render、其他 VPS

# 3. 配置环境变量
# 在 Railway/Render 中设置 .env 中的变量

# 4. 运行 API
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 前端开发

```bash
# 1. 安装依赖
cd frontend
npm install

# 2. 本地开发
npm run dev

# 3. 构建生产版本
npm run build

# 4. 部署到 Vercel
vercel deploy
```

---

## 📝 开发规范

### 后端开发规范

- 使用 `loguru` 记录日志
- 使用 `async/await` 异步编程
- API 接口使用 FastAPI 依赖注入
- 所有数据库操作使用 SQLAlchemy Core

### 前端开发规范

- 使用 TypeScript 严格模式
- 组件使用函数式组件 + Hooks
- 状态管理使用 Zustand
- 样式使用 TailwindCSS

### Git 提交规范

```
feat: 新功能
fix: 修复 bug
docs: 文档更新
style: 代码格式调整
refactor: 重构
test: 测试相关
chore: 构建/工具相关
```

---

**更新时间**: 2025-01-18
**版本**: v1.0
