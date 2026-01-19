# EvoAlpha OS

> **进化即自由 (Evolution is Freedom)**

## 🚧 项目状态

**✅ 数据采集系统 v2.1.0 - 生产就绪！**

- ✅ 项目框架已搭建
- ✅ 核心代码已从 EvoQuant OS 移植
- ✅ **数据采集系统完成**（v2.1.0 - 增量更新与自动化版）
- 🚧 正在整合和调试
- 🚧 AI Agent 待接入

**预计可用时间**：2025年2月

---

## 📖 项目简介

EvoAlpha OS 是一个**数据驱动的 Alpha 机会发现平台**，通过**量化筛选做减法**和**AI Agent 做加法**，帮助个人投资者发现市场中的 Alpha 机会。

**品牌**：dlab (Evolution Lab)

---

## 🎯 核心功能

- **Alpha 机会雷达**：量化筛选个股和板块机会，置信度评分
- **AI 深度分析**：模拟投资大师（陶博士、简放等）进行智能分析
- **ETF 全天候配置**：稳健的五类资产配置建议
- **日报研报系统**：自动生成投资日报和深度研报
- **量化回测系统**：完整的策略回测和性能分析

---

## 🏗️ 技术架构

### 三段式架构

```
┌─────────────────────────────────────────────────┐
│  前端 (Next.js) - Vercel                         │
│  - 公开页面：首页部分机会                        │
│  - 登录后：完整功能                              │
└───────────────────┬─────────────────────────────┘
                      │ REST API
┌───────────────────▼─────────────────────────────┐
│  后端 (FastAPI) - 云端 VPS                       │
│  - API 接口                                      │
│  - 云端数据库 (CockroachDB)                      │
│  - AI 分析 (GLM-4)                               │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  本地工厂 (MBP)                                   │
│  - 数据采集 (AkShare)                            │
│  - 量化计算 (RPS、策略)                          │
│  - 本地存储 (SQLite)                             │
│  - 同步云端 (CSV → R2 → IMPORT)                 │
└─────────────────────────────────────────────────┘
```

### 技术栈

**前端**：
- Next.js 16 + TypeScript
- TailwindCSS 4
- ECharts / Recharts
- Zustand (状态管理)

**后端（云端）**：
- FastAPI + Python 3.10+
- CockroachDB Serverless
- GLM-4 API (AI 分析)

**后端（本地）**：
- Python 3.10+
- SQLite (本地存储)
- Pandas / NumPy (量化计算)

---

## 📂 项目结构

```
EvoAlpha-OS/
├── BLUEPRINT.md              # 📘 技术蓝图（完整系统设计）
├── README.md                 # 📄 项目说明（本文件）
├── MIGRATION_PLAN.md         # 📋 代码移植计划
├── .env                      # 🔐 环境变量配置
├── .gitignore
│
├── backend/                  # 🔧 Python 后端
│   ├── main.py               # FastAPI 应用入口（云端）
│   ├── requirements.txt      # Python 依赖
│   │
│   ├── app/                  # ☁️ 云端后端模块
│   │   ├── core/             # 核心配置（config, database）
│   │   ├── api/              # API 接口层
│   │   ├── agents/           # AI Agent 层
│   │   ├── sync/             # 同步模块
│   │   └── scheduler/        # 定时任务
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
│   ├── init_data.sh          # 🚀 初始化数据采集脚本
│   ├── start_scheduler.sh    # ⏰ 启动定时调度器
│   └── run_daily_collection.sh # 📊 每日数据采集
│   │
│   ├── quant_engine/         # ⚡ 量化引擎（本地工厂）
│   │   ├── core/             # 核心工具（tdx_lib）
│   │   ├── features/         # 因子计算（RPS、技术指标）
│   │   ├── pool/             # 股票池管理
│   │   └── strategies/       # 策略执行（MRGC、板块共振）
│   │
│   └── scripts/              # 🔧 实用脚本
│       └── init_database.py  # 数据库初始化
│
└── frontend/                 # 🎨 Next.js 前端
    ├── package.json
    ├── next.config.ts
    ├── tailwind.config.ts
    │
    └── src/
        ├── app/              # 页面路由
        │   ├── market/       # 市场概览
        │   ├── stock/        # 个股详情
        │   ├── sector/       # 板块分析
        │   ├── quant/        # 量化策略
        │   └── report/       # 研报系统
        │
        ├── components/       # React 组件
        │   ├── layout/       # 布局组件
        │   ├── stock/        # 股票组件
        │   └── ui/           # UI 组件
        │
        └── lib/              # 工具库
            ├── api.ts        # API 客户端
            └── utils.ts      # 工具函数
```

---

## 🗓️ 开发计划

### Phase 1：A股系统（1 个月）

**Week 1**：基础架构 + 数据层 ✅
- 项目初始化
- 数据采集（股票、K线、新闻）
- 量化引擎（RPS、策略）
- 同步模块（CSV → R2 → 云端）

**Week 2**：云端后端 + AI Agent 🚧
- API 接口开发
- AI Agent 实现（大师 Agent、功能 Agent）
- GLM-4 集成

**Week 3**：前端开发 🚧
- 首页（Alpha 机会）
- 个股详情页（K线 + AI 分析）
- 板块详情页
- ETF 配置页面

**Week 4**：日报 + 回测 + 上线 ⏳
- 日报生成系统
- 邮件推送
- 回测系统
- 部署上线

### Phase 2：Web3 系统（后续）
- 空投机会雷达
- 鲸鱼监控
- 机器人集成

### Phase 3：美股系统（可选）
- 美股 K 线数据
- 美股财报分析
- 中概股对比

---

## 🚧 当前进度

### 已完成 ✅
- [x] 项目初始化（Git、文档）
- [x] 技术蓝图设计（BLUEPRINT.md）
- [x] 从 EvoQuant OS 移植核心代码
- [x] 双引擎数据库系统（本地 + 云端）
- [x] **数据采集系统 v2.1.0** ⭐
  - 12个数据采集器（K线、估值、新闻、财务等）
  - 增量更新机制（性能提升90%+）
  - 定时调度系统（每日/每月/每季度自动采集）
  - 初始化数据采集脚本（7-9小时全量采集）
  - 完整文档和使用指南
- [x] 量化引擎（RPS 计算、MRGC 策略）
- [x] 前端页面框架

### 进行中 🚧
- [ ] API 接口开发
- [ ] 数据库表创建
- [ ] AI Agent 接入
- [ ] 前后端联调

### 待开始 ⏳
- [ ] 日报生成系统
- [ ] 邮件推送
- [ ] 用户系统
- [ ] 生产环境部署

---

## 🎓 核心理念

### 量化筛选做减法

从 5000+ 只股票中，通过 RPS、板块效应、资金流向，每日自动筛选出几十只符合逻辑的"信号股"。

### AI Agent 做加法

利用本地大模型深度阅读财报与新闻，模拟不同流派大师（如陶博士、巴菲特）进行多维度会诊，提供高质量决策依据。

---

## 💰 商业模式

| 版本 | 价格 | 权限 |
|------|------|------|
| 免费版 | ¥0 | Top 3 机会 + 基础功能 |
| Pro版 | ¥199/月 | 全部机会 + AI 分析 + 日报推送 |
| VIP版 | ¥999/季 | Pro 全部 + 1对1 咨询 + 定制策略 |

---

## 📞 联系方式

- **品牌**：dlab (Evolution Lab)
- **Slogan**：进化即自由 (Evolution is Freedom)

---

## 📚 文档

### 📖 文档导航 ⭐
- **[文档索引](./DOCS_INDEX.md)** - 完整的文档导航和阅读路径指南

### 核心文档
- **[README.md](./README.md)** - 项目总览（本文件）
- **[技术蓝图](./BLUEPRINT.md)** - 完整的系统设计和技术架构
- **[项目结构](./PROJECT_STRUCTURE.md)** - 详细的目录结构说明

### 数据采集系统文档 (v2.1.0 ✅ 已完成)
- **[快速开始](./backend/data_job/docs/QUICKSTART.md)** - 3分钟快速上手数据采集
- **[系统架构](./backend/data_job/docs/ARCHITECTURE.md)** - v2.1.0 架构设计文档
- **[开发指南](./backend/data_job/docs/DEVELOPMENT_GUIDE.md)** - 新增采集器开发规范
- **[主文档](./backend/data_job/README.md)** - 数据采集系统完整说明
- **[优化报告](./backend/data_job/INCREMENTAL_UPDATE_REPORT.md)** - 增量更新性能报告
- **[采集规划](./backend/data_job/COLLECTION_SCHEDULE.md)** - 数据采集频率规划

### 开发文档
- **[开发日志](./CLAUDE.md)** - 开发过程中的经验、问题和解决方案

### 归档文档
- **[代码移植计划](./docs/archive/MIGRATION_PLAN.md)** - 从 EvoQuant OS 移植代码（已完成）
- **[数据分析报告](./docs/archive/DATA_ANALYSIS.md)** - 数据采集模块分析（已完成）
- **[项目初始化计划](./docs/archive/PROJECT_PLAN.md)** - 项目初始化（已完成）

---

## 🤝 贡献

项目正在开发中，暂不对外开放贡献。

---

## 📄 许可证

Copyright © 2025 dlab (Evolution Lab). All rights reserved.

---

**项目状态**：🚧 开发中 | 📊 数据采集系统 v2.1.0 已完成
**最后更新**：2026-01-19
**预计可用**：2025年2月
