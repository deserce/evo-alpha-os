# 🚀 EvoAlpha OS 数据采集系统 - 快速开始

> **版本**: v2.1.0 - 增量更新与自动化版
> **更新时间**: 2026-01-19
> **状态**: ✅ 生产就绪

---

## 🎯 三分钟快速上手

### 1️⃣ 安装依赖

```bash
cd backend
pip install -r data_job/requirements.txt
```

### 2️⃣ 初始化数据（首次使用）

```bash
# 完整初始化（7-9小时）
./init_data.sh

# 或分步执行（推荐）
./init_data.sh --step 1   # 基础数据
./init_data.sh --step 2   # 市场数据
./init_data.sh --step 3   # 财务数据
./init_data.sh --step 4   # K线数据（核心，耗时最长）
./init_data.sh --step 5   # 舆情数据
```

### 3️⃣ 启动自动采集

```bash
# 启动定时调度器
./start_scheduler.sh

# 或手动运行每日采集
./run_daily_collection.sh
```

---

## 📚 完整文档导航

### 📖 主要文档

| 文档 | 说明 | 适用场景 |
|------|------|---------|
| [README.md](../README.md) | 系统总览和使用指南 | 了解完整功能 |
| [QUICKSTART.md](./QUICKSTART.md) | 快速开始指南 | ⭐ **推荐首先阅读** |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 架构设计文档 | 理解系统设计 |
| [DEVELOPMENT_GUIDE.md](./DEVELOPMENT_GUIDE.md) | 开发规范指南 | 开发新采集器 |
| [COLLECTION_SCHEDULE.md](../COLLECTION_SCHEDULE.md) | 采集频率规划 | 了解采集计划 |
| [INCREMENTAL_UPDATE_REPORT.md](../INCREMENTAL_UPDATE_REPORT.md) | 增量更新报告 | 了解性能优化 |

---

## 🎓 使用场景

### 场景1: 首次部署系统

```bash
# Step 1: 安装依赖
pip install -r data_job/requirements.txt

# Step 2: 初始化数据
./init_data.sh

# Step 3: 启动自动采集
./start_scheduler.sh
```

### 场景2: 日常数据更新

**自动模式**（推荐）:
```bash
# 启动后自动按计划运行
./start_scheduler.sh
```

**手动模式**:
```bash
# 立即运行每日采集
./run_daily_collection.sh

# 或测试单个采集器
python -m data_job.utils.scheduler --mode daily
```

### 场景3: 开发新采集器

```bash
# 1. 创建新采集器
# 参考 data_job/collectors/example_collector.py

# 2. 运行测试
python -m data_job.collectors.your_new_collector

# 3. 添加到调度器（可选）
# 编辑 data_job/utils/scheduler.py
```

### 场景4: 查看数据状态

```bash
# 预览数据库表数据
python data_job/scripts/preview_database.py

# 验证数据质量
python data_job/utils/validate_data.py
```

---

## 📊 采集器清单

### 📈 每日采集（6个）⭐

| 采集器 | 数据表 | 执行时间 | 耗时 |
|--------|--------|---------|------|
| StockKlineCollector | stock_daily_prices | 15:30 | 5-10分钟 |
| SectorKlineCollector | sector_daily_prices | 15:30 | 2-5分钟 |
| ETFKlineCollector | etf_daily_prices | 15:30 | 2-5分钟 |
| StockValuationCollector | stock_valuation_daily | 15:30 | 2-3分钟 |
| LimitBoardsCollector | limit_board_trading | 15:30 | 1-2分钟 |
| NewsCollector | news_articles | 16:00 | 2-3分钟 |

**总计**: 15-30分钟（增量模式）

### 📅 每月采集（3个）

| 采集器 | 数据表 | 执行时间 | 耗时 |
|--------|--------|---------|------|
| MacroDataCollector | macro_indicators | 每月1号 08:00 | 10-15分钟 |
| ETFInfoCollector | etf_info | 每月1号 08:00 | 5-10分钟 |
| StockSectorListCollector | stock_info | 每月1号 08:00 | 10-15分钟 |

**总计**: 25-40分钟

### 💰 每季度采集（2个）

| 采集器 | 数据表 | 执行时间 | 耗时 |
|--------|--------|---------|------|
| FundHoldingsCollector | finance_fund_holdings | 每季度15号 08:00 | 10-15分钟 |
| FinanceSummaryCollector | stock_finance_summary | 每季度15号 08:00 | 2-3小时 |

**总计**: 2-3.5小时

### ⛔ 一次性采集（1个）

| 采集器 | 数据表 | 状态 | 说明 |
|--------|--------|------|------|
| NorthboundHoldingsCollector | stock_northbound_holdings | ✅ 已完成 | 数据截至2024-08-16 |

---

## 🛠️ 常用命令

### 运行采集器

```bash
# 运行单个采集器（Python方式）
python -m data_job.collectors.stock_kline_collector
python -m data_job.collectors.news_collector

# 使用便捷脚本
./run_daily_collection.sh              # 每日采集
./init_data.sh --step 1                # 初始化Step 1
```

### 调度器管理

```bash
# 启动调度器
./start_scheduler.sh

# 或使用Python
python -m data_job.utils.scheduler --mode schedule      # 定时模式
python -m data_job.utils.scheduler --mode daily         # 立即运行每日任务
python -m data_job.utils.scheduler --mode monthly        # 立即运行每月任务
python -m data_job.utils.scheduler --mode quarterly     # 立即运行季度任务
```

### 数据验证

```bash
# 预览数据库
python data_job/scripts/preview_database.py

# 验证数据质量
python data_job/utils/validate_data.py
```

---

## ⚡ 增量更新说明

### 什么是增量更新？

**增量更新** = 只采集缺失的新数据，不重复采集已有数据

**优势**:
- ⚡ **速度快**: 每日采集从3-4小时 → 15-30分钟
- 📉 **API少**: 减少97%的API调用
- 💰 **省资源**: 降低网络和服务器负载

### 工作原理

```
首次采集: 采集历史3年数据 → 3-4小时
日常更新: 只采集今天数据 → 5-10分钟
```

### 自动检测

所有采集器自动检测：
- ✅ 数据库最后日期
- ✅ 是否需要更新
- ✅ 只采集缺失部分

---

## 📂 项目结构

```
backend/data_job/
├── 📘 文档
│   ├── README.md                  ← 主文档
│   ├── QUICKSTART.md              ← 快速开始（本文件）
│   ├── ARCHITECTURE.md            ← 架构设计
│   ├── DEVELOPMENT_GUIDE.md       ← 开发指南
│   ├── COLLECTION_SCHEDULE.md     ← 采集频率
│   └── INCREMENTAL_UPDATE_REPORT.md ← 优化报告
│
├── 🔧 核心框架
│   ├── core/                      ← 框架层
│   ├── common/                    ← 公共工具
│   ├── config/                    ← 配置管理
│   └── collectors/                ← 12个采集器
│
├── 🛠️ 工具脚本
│   ├── utils/                     ← 工具集
│   │   ├── scheduler.py           ← 定时调度器 ⭐
│   │   └── validate_data.py       ← 数据验证
│   └── scripts/                   ← 独立脚本
│       └── init_data_collection.py ← 初始化采集 ⭐
│
└── 📦 备份
    └── backup/                    ← 归档文件
```

---

## 🔍 故障排除

### 常见问题

**Q1: APScheduler 未安装**
```bash
pip install apscheduler
```

**Q2: 数据库表不存在**
```bash
# 先运行初始化
./init_data.sh --step 1
```

**Q3: 采集失败**
```bash
# 查看日志
tail -f logs/*.log

# 重新运行
python -m data_job.collectors.xxx_collector
```

---

## 📞 获取帮助

### 文档

- 📖 [README.md](../README.md) - 完整使用指南
- 🏗️ [ARCHITECTURE.md](ARCHITECTURE.md) - 架构设计
- 💻 [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) - 开发规范

### 日志

- 📋 采集日志: `logs/` 目录
- 🔍 调试模式: 查看日志文件

---

## 🎉 下一步

1. ✅ 完成初始化数据采集
2. ✅ 启动定时自动采集
3. ✅ 开始使用数据进行分析

**需要帮助？** 查看完整文档或查看日志文件。

---

**版本**: v2.1.0 | **状态**: ✅ 生产就绪 | **更新**: 2026-01-19
