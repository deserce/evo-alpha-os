# EvoAlpha OS - Backend 后端服务

> **版本**: v1.0
> **最后更新**: 2026-01-20
> **状态**: ✅ 生产就绪

---

## 📋 目录

- [项目概述](#项目概述)
- [整体架构](#整体架构)
- [目录结构](#目录结构)
- [核心模块](#核心模块)
- [快速开始](#快速开始)
- [自动化流程](#自动化流程)
- [配置说明](#配置说明)
- [开发指南](#开发指南)
- [部署指南](#部署指南)
- [常见问题](#常见问题)

---

## 项目概述

EvoAlpha OS 是一个**数据驱动的量化Alpha机会发现平台**，提供完整的A股数据采集、量化因子计算、策略选股和AI分析能力。

### 核心能力

- 📊 **数据采集** - A股行情、财务、舆情等全方位数据采集
- 🧮 **因子计算** - RPS等量化因子的高性能计算引擎
- 🎯 **策略选股** - 基于技术指标的量化策略系统
- 🤖 **AI分析** - 基于LLM的智能分析和推荐
- 🔄 **自动化** - 完整的自动化交易流水线

### 技术栈

- **后端框架**: FastAPI 0.115
- **数据库**: SQLite (本地) + PostgreSQL/CockroachDB (云端)
- **数据处理**: Pandas, NumPy
- **量化计算**: SciPy, scikit-learn, TA-Lib
- **任务调度**: APScheduler
- **数据源**: AKShare (A股数据)

---

## 整体架构

### 三层架构设计

```
┌─────────────────────────────────────────────────────────┐
│                   调度层 (Orchestration)                 │
│  ┌────────────────────────────────────────────────────┐ │
│  │  auto_pipeline.py - 统一自动化流水线调度器        │ │
│  │  - 每日流程: 采集 → 计算 → 选股                   │ │
│  │  - 季度流程: 采集 → 股票池 → 计算 → 选股         │ │
│  └────────────────────────────────────────────────────┘ │
└───────────────────┬─────────────────┬───────────────────┘
                    │                 │
        ┌───────────▼─────────┐  ┌───▼──────────────────┐
        │   数据层 (Data)      │  │  量化层 (Quant)       │
        │  data_job/          │  │  quant_engine/       │
        │  - 数据采集          │  │  - 因子计算          │
        │  - 调度管理          │  │  - 策略选股          │
        │  - 增量更新          │  │  - 股票池管理        │
        └─────────────────────┘  └──────────────────────┘
```

### 数据流向

```
外部数据源 (AKShare)
    ↓
数据采集层 (data_job)
    ↓
本地数据库 (SQLite)
    ↓
量化计算层 (quant_engine)
    ├─→ RPS因子
    ├─→ 策略选股
    └─→ 股票池
    ↓
AI分析层 (app/agents)
    ↓
结果输出 (API + 数据库)
```

---

## 目录结构

```
backend/
├── 📄 README.md                      # 本文档
├── 📄 AUTO_PIPELINE_README.md        # 自动化流水线文档
├── 🚀 auto_pipeline.py               # ⭐ 统一调度器
├── 🔧 run_pipeline.sh                # 自动化流水线启动脚本
├── 📊 monitor_pipeline.sh             # 监控通知脚本
│
├── 📁 app/                            # FastAPI应用层
│   ├── api/                          # REST API接口
│   ├── agents/                       # AI Agent模块
│   ├── alpha/                        # Alpha因子模块
│   ├── core/                         # 核心配置
│   │   ├── config.py                 # ⭐ 配置管理
│   │   └── database.py               # 数据库连接
│   └── scheduler/                    # 定时任务
│
├── 📁 data_job/                      # ⭐ 数据层
│   ├── collectors/                   # 数据采集器
│   │   ├── stock_kline_collector.py
│   │   ├── sector_kline_collector.py
│   │   ├── etf_kline_collector.py
│   │   ├── fund_holdings_collector.py
│   │   └── ...
│   ├── core/                         # 采集框架
│   │   ├── base_collector.py         # 采集器基类
│   │   └── example_collector.py
│   ├── utils/                        # 工具
│   │   └── scheduler.py              # 数据采集调度器
│   ├── scripts/                      # 执行脚本
│   │   ├── init_database.py          # 数据库初始化
│   │   └── run_daily_update.py       # 每日更新
│   └── README.md                     # 数据采集文档
│
├── 📁 quant_engine/                  # ⭐ 量化层
│   ├── calculators/                  # 因子计算器
│   │   ├── stock_rps_calculator.py   # 个股RPS
│   │   ├── sector_rps_calculator.py  # 板块RPS
│   │   └── etf_rps_calculator.py     # ETF RPS
│   ├── strategies/                   # 策略模块
│   │   ├── base_strategy.py          # 策略基类
│   │   ├── mrgc_strategy.py          # MRGC策略
│   │   └── select_resonance.py       # 板块共振
│   ├── runner/                       # 运行器
│   │   ├── feature_runner.py         # 因子计算运行器
│   │   └── strategy_runner.py        # 策略运行器
│   ├── pool/                         # 股票池管理
│   │   └── maintain_pool.py          # 核心股票池维护
│   ├── core/                         # 核心框架
│   │   ├── base_feature_calculator.py
│   │   └── tdx_lib.py                # 通达信公式库
│   └── README.md                     # 量化引擎文档
│
├── 📁 data/                           # 数据目录
│   ├── local_quant.db                # 本地SQLite数据库
│   └── collection_progress/          # 采集进度跟踪
│
└── 📁 .env                            # 环境配置
```

---

## 核心模块

### 1. 数据采集层 (data_job/)

**职责**: A股数据采集、清洗、存储

**核心组件**:
- `BaseCollector` - 采集器基类，提供断点续传、重试机制
- 13个专业采集器 - 覆盖K线、财务、舆情等数据
- `CollectionScheduler` - 定时调度器

**数据源**:
- AKShare (主要)
- 东方财富
- 新浪财经
- 同花顺

**支持的数据类型**:
```python
# 行情数据 (5个)
- StockKlineCollector      # 个股日K
- SectorKlineCollector     # 板块日K
- ETFKlineCollector        # ETF日K
- StockValuationCollector  # 股票估值
- LimitBoardsCollector     # 连板数据

# 财务数据 (2个)
- FundHoldingsCollector    # 基金季度持仓
- FinanceSummaryCollector  # 财务摘要

# 舆情数据 (1个)
- NewsCollector            # 财经新闻

# 基础数据 (3个)
- MacroDataCollector       # 宏观指标
- ETFInfoCollector         # ETF信息
- StockSectorListCollector # 股票列表
```

**详细文档**: [data_job/README.md](data_job/README.md)

---

### 2. 量化引擎层 (quant_engine/)

**职责**: RPS因子计算、策略选股、股票池管理

**核心组件**:

#### 因子计算引擎
```python
# 统一的RPS计算框架
BaseFeatureCalculator
    ├── StockRPSCalculator    # 个股RPS (5/10/20/50/120/250日)
    ├── SectorRPSCalculator   # 板块RPS
    └── ETFRPSCalculator      # ETF RPS

# 特性
- 向量化计算 (Pandas)
- 增量/全量两种模式
- 幂等性保证
- 自动去重
```

#### 策略系统
```python
# 策略基类
BaseStrategy
    ├── MrgcStrategy          # 陶博士MRGC形态策略
    └── ResonanceStrategy     # 板块共振策略

# 策略运行器
StrategyRunner - 策略执行引擎
```

#### 股票池管理
```python
StockPoolMaintainer - 核心股票池维护

筛选条件:
- 基金持股比例 ≥ 5%
- 北向资金持仓 ≥ 1亿元

更新频率: 每季度
```

**详细文档**: [quant_engine/README.md](quant_engine/README.md)

---

### 3. 调度层 (auto_pipeline.py)

**职责**: 编排完整的数据采集和量化计算流程

**自动化流程**:

#### 每日流程 (工作日 15:30)
```
数据采集 (60-90分钟)
  ├─ 个股K线
  ├─ 板块K线
  ├─ ETF K线
  ├─ 股票估值
  ├─ 连板数据
  └─ 新闻舆情
    ↓
RPS计算 (15-20秒)
  ├─ 个股RPS
  ├─ 板块RPS
  └─ ETF RPS
    ↓
策略选股 (5-10秒)
  └─ MRGC策略
```

#### 季度流程 (每季度15号 08:00)
```
季度数据采集
  ├─ 基金季度持仓
  └─ 财务摘要
    ↓
更新核心股票池
    ↓
RPS计算
    ↓
策略选股
```

**详细文档**: [AUTO_PIPELINE_README.md](AUTO_PIPELINE_README.md)

---

### 4. 应用层 (app/)

**职责**: REST API、AI Agent、定时任务

**主要模块**:
- `FastAPI` - REST API服务
- `Agents` - LLM智能体
- `Scheduler` - 定时任务管理
- `Core` - 配置和数据库

---

## 快速开始

### 环境要求

- Python 3.12+
- SQLite 3 (自带)
- 4GB+ 内存推荐

### 安装依赖

```bash
cd backend
pip3 install -r requirements.txt
```

### 配置环境

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑配置（可选）
# .env 文件已包含默认配置
```

### 初始化数据库

#### 方式1: 自动初始化（推荐）

```bash
# 运行完整初始化 (7-9小时)
./init_data.sh

# 或分步初始化
python3 -m data_job.scripts.init_database
```

#### 方式2: 手动初始化

```bash
# 1. 创建数据库表
python3 -c "from app.core.database import init_db; init_db()"

# 2. 采集基础数据 (按顺序)
python3 -m data_job.utils.scheduler --mode daily
```

---

## 自动化流程

### 启动完整自动化流水线

#### 方式1: 立即运行

```bash
# 运行每日流程 (数据采集 → RPS计算 → 策略选股)
python3 auto_pipeline.py --mode daily

# 运行季度流程 (数据采集 → 股票池 → RPS → 选股)
python3 auto_pipeline.py --mode quarterly
```

#### 方式2: 定时调度

```bash
# 启动定时调度器
# 每日 15:30 自动运行
# 每季度15号 08:00 自动运行
python3 auto_pipeline.py --mode schedule
```

### 只运行数据采集

```bash
# 运行每日数据采集
python3 -m data_job.utils.scheduler --mode daily

# 或使用便捷脚本
./run_daily_collection.sh
```

### 只运行RPS计算

```bash
# 增量更新RPS (只算最近3天)
python3 -m quant_engine.runner.feature_runner --mode daily

# 全量重算 (计算最近400天)
python3 -m quant_engine.runner.feature_runner --mode init
```

### 只运行策略选股

```bash
# 运行MRGC策略
python3 -m quant_engine.runner.strategy_runner --strategy mrgc

# 列出所有可用策略
python3 -m quant_engine.runner.strategy_runner --list
```

---

## 配置说明

### 环境变量 (.env)

```bash
# ========== 应用配置 ==========
APP_ENV=development
APP_DEBUG=true
LOG_LEVEL=INFO

# ========== 数据库配置 ==========
# 本地SQLite (默认)
LOCAL_DB_PATH=data/local_quant.db

# 云端PostgreSQL (可选)
CLOUD_DB_HOST=localhost
CLOUD_DB_PORT=5433
CLOUD_DB_USER=postgres
CLOUD_DB_PASSWORD=your_password
CLOUD_DB_NAME=evoquant

# ========== 数据采集配置 ==========
AKSHARE_PROXY=           # 代理（可选）
NEWS_SOURCES=eastmoney,sina,firstfinancing
FORCE_SYNC_KLINE=false   # 是否强制同步K线到云端

# ========== 定时任务配置 ==========
SCHEDULER_TIMEZONE=Asia/Shanghai
DAILY_JOB_TIME=15:30
```

### 量化引擎配置

位置: `quant_engine/config/calculator_config.py`

```python
class CalculatorConfig:
    # RPS计算周期
    RPS_PERIODS = [5, 10, 20, 50, 120, 250]

    # 增量更新配置
    INCREMENTAL_WINDOW_DAYS = 400  # 计算250日RPS往前推400天
    SAVE_RECENT_DAYS = 3           # 只保存最近3天

    # 批量处理
    CHUNK_SIZE = 50  # SQLite批量插入大小
```

---

## 开发指南

### 添加新的数据采集器

1. 创建采集器类，继承 `BaseCollector`
2. 实现 `collect()` 方法
3. 在调度器中注册

```python
# data_job/collectors/my_collector.py
from data_job.core.base_collector import BaseCollector

class MyCollector(BaseCollector):
    def collect(self):
        # 实现采集逻辑
        pass
```

### 添加新的RPS因子

1. 创建计算器类，继承 `BaseFeatureCalculator`
2. 实现必需的抽象方法
3. 在FeatureRunner中注册

```python
# quant_engine/calculators/my_rps_calculator.py
from quant_engine.core.base_feature_calculator import BaseFeatureCalculator

class MyRPSCalculator(BaseFeatureCalculator):
    def get_source_table(self) -> str:
        return "source_table"

    def get_target_table(self) -> str:
        return "quant_feature_my_rps"

    def get_entity_column(self) -> str:
        return "symbol"

    def get_periods(self) -> list:
        return [5, 10, 20, 50, 120, 250]
```

### 添加新的策略

1. 创建策略类，继承 `BaseStrategy`
2. 实现 `run()` 方法
3. 在StrategyRunner中注册

```python
# quant_engine/strategies/my_strategy.py
from quant_engine.strategies.base_strategy import BaseStrategy

class MyStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("my_v1")
        self.strategy_display_name = "我的策略"

    def run(self, trade_date=None):
        # 1. 获取股票池
        # 2. 获取因子数据
        # 3. 加载K线数据
        # 4. 筛选信号
        # 5. 保存结果
        pass
```

---

## 部署指南

### 开发环境

```bash
# 1. 安装依赖
pip3 install -r requirements.txt

# 2. 配置环境
cp .env.example .env

# 3. 初始化数据库
python3 -m data_job.scripts.init_database

# 4. 启动自动化流水线
python3 auto_pipeline.py --mode schedule
```

### 生产环境

```bash
# 1. 使用虚拟环境
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt

# 2. 配置systemd服务
sudo cp evoalpha-backend.service /etc/systemd/system/
sudo systemctl enable evoalpha-backend
sudo systemctl start evoalpha-backend

# 3. 查看日志
sudo journalctl -u evoalpha-backend -f
```

### Docker部署

```bash
# 1. 构建镜像
docker build -t evoalpha-backend .

# 2. 运行容器
docker run -d \
  --name evoalpha \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/.env:/app/.env \
  --restart unless-stopped \
  evoalpha-backend

# 3. 查看日志
docker logs -f evoalpha
```

---

## 常见问题

### Q1: ModuleNotFoundError: No module named 'xxx'

**原因**: 依赖包未安装

**解决**:
```bash
pip3 install -r requirements.txt
```

### Q2: 数据库连接错误

**原因**: SQLite不支持并发写入

**解决**:
```bash
# 确保没有其他进程在访问数据库
ps aux | grep python

# 或重启数据库连接
```

### Q3: RPS计算失败：没有因子数据

**原因**: 数据库中缺少最近的数据

**解决**:
```bash
# 先运行数据采集
python3 -m data_job.utils.scheduler --mode daily

# 再运行RPS计算
python3 -m quant_engine.runner.feature_runner --mode daily
```

### Q4: 策略选股结果为空

**原因**: 可能是策略条件太严格或数据不完整

**解决**:
1. 检查数据库是否有足够的历史数据
2. 检查策略参数是否合理
3. 查看日志了解筛选过程

### Q5: 如何监控自动化流水线？

**解决**:
```bash
# 方式1: 查看日志
tail -f /private/tmp/claude/-Users-deserce-Desktop-EvoAlpha-OS/tasks/*.output

# 方式2: 使用监控脚本
./monitor_pipeline.sh
```

---

## 性能指标

### 数据采集性能

| 数据类型 | 数量 | 耗时 | 频率 |
|---------|------|------|------|
| 个股K线 | 5472只 | 30-45分钟 | 每日 |
| 板块K线 | 86个 | 5-10分钟 | 每日 |
| ETF K线 | - | 10-15分钟 | 每日 |
| 基金持仓 | - | 10-15分钟 | 每季度 |
| 财务摘要 | - | 2-3小时 | 每季度 |

### 因子计算性能

| 计算器 | 数据量 | 耗时 |
|--------|--------|------|
| 个股RPS | 1.36M行 → 5,212条 | 12.4秒 |
| 板块RPS | 138K行 → 520条 | 1.1秒 |
| ETF RPS | 18K行 → 71条 | 0.2秒 |

### 代码质量

- 代码重复率: **<5%**
- 基类复用率: **80%**
- 测试覆盖: 核心模块已验证

---

## 相关文档

- [data_job/README.md](data_job/README.md) - 数据采集模块文档
- [quant_engine/README.md](quant_engine/README.md) - 量化引擎模块文档
- [AUTO_PIPELINE_README.md](AUTO_PIPELINE_README.md) - 自动化流水线文档

---

## 贡献指南

欢迎贡献代码！请遵循以下步骤：

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'feat: 添加某个特性'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交 Pull Request

---

## 许可证

内部项目，仅供学习和研究使用。

---

**最后更新**: 2026-01-20
**维护者**: Deserce
**反馈**: 请提交 Issue 或 Pull Request
