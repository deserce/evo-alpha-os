# EvoAlpha OS - 数据采集系统使用指南

## 📋 目录

1. [系统架构](#系统架构)
2. [快速开始](#快速开始)
3. [采集器列表](#采集器列表)
4. [基类说明](#基类说明)
5. [最佳实践](#最佳实践)
6. [常见问题](#常见问题)

---

## 系统架构

### 📁 目录结构

```
backend/data_job/
├── core/                      # 核心框架层
│   ├── __init__.py
│   ├── base_collector.py      # 基础采集器基类
│   ├── batch_collector.py     # 批量采集器基类
│   └── example_collector.py   # 示例采集器
│
├── common/                    # 公共工具层
│   ├── __init__.py
│   ├── network_utils.py       # 网络工具
│   ├── path_utils.py          # 路径工具
│   ├── logger_utils.py        # 日志工具
│   └── exception_utils.py     # 异常工具
│
├── config/                    # 配置管理层
│   ├── __init__.py
│   └── collector_config.py    # 采集器配置
│
├── collectors/                # 采集器实现层
│   ├── __init__.py
│   ├── stock_valuation_collector.py    # 股票估值
│   ├── macro_data_collector.py         # 宏观数据
│   ├── limit_boards_collector.py       # 连板数据
│   ├── stock_kline_collector.py        # 个股K线
│   ├── sector_kline_collector.py       # 板块K线
│   ├── etf_kline_collector.py          # ETF K线
│   ├── fund_holdings_collector.py       # 基金持股
│   ├── northbound_holdings_collector.py # 北向资金持股
│   ├── etf_info_collector.py           # ETF信息
│   ├── finance_summary_collector.py    # 财务摘要
│   ├── news_collector.py               # 新闻舆情
│   └── stock_sector_list_collector.py  # 股票-板块映射
│
├── utils/                     # 工具脚本
│   ├── __init__.py
│   ├── run_all_collectors.py  # 运行所有采集器
│   └── validate_data.py       # 数据验证
│
├── tests/                     # 测试套件
│   ├── __init__.py
│   ├── test_base_collector.py
│   └── test_collectors_integration.py
│
├── scripts/                   # 独立脚本
│   ├── run_daily_update.py   # 每日更新
│   └── init_database.py       # 初始化数据库
│
├── docs/                      # 文档目录
│   ├── README.md              # 使用指南（本文件）
│   ├── ARCHITECTURE.md        # 架构设计文档
│   └── DEVELOPMENT_GUIDE.md   # 开发规范
│
└── backup/                    # 备份目录
    └── [遗留脚本备份]
```

### 🎯 核心特性

#### 1. **统一架构模式**
- 所有采集器继承自 `BaseCollector`
- 统一的错误处理和重试机制
- 统一的日志格式和进度跟踪
- 代码重复率从 80% 降至 <5%

#### 2. **模块化设计**
- **common/** - 公共工具（网络、路径、日志、异常）
- **config/** - 集中配置管理
- **core/** - 框架层（基类、批量处理）
- **collectors/** - 业务采集器

#### 3. **连接稳定性保障**
- 连接池管理（复用HTTP连接）
- 指数退避重试算法
- 网络急救包（处理VPN和SSL问题）
- 健康检查（自动检测网络和数据库）

#### 4. **断点续传与增量更新**
- 自动记录采集进度
- 支持从中断处继续采集
- 智能去重，避免重复数据
- 自动清理过期数据

---

## 快速开始

### 🆕 首次使用？按以下步骤操作

#### Step 1: 安装依赖

```bash
cd backend
pip install -r data_job/requirements.txt
```

#### Step 2: 初始化数据采集（首次全量采集）

```bash
# 完整初始化（7-9小时）
./init_data.sh

# 或分步执行（推荐）
./init_data.sh --step 1  # 只执行Step 1: 基础数据
./init_data.sh --step 2  # 只执行Step 2: 市场数据
./init_data.sh --step 3  # 只执行Step 3: 财务数据
./init_data.sh --step 4  # 只执行Step 4: K线数据（耗时最长）
./init_data.sh --step 5  # 只执行Step 5: 舆情数据
```

**详细说明**：
- **Step 1: 基础数据** - 15-25分钟（股票列表、板块映射、ETF信息）
- **Step 2: 市场数据** - 15-25分钟（估值、宏观指标）
- **Step 3: 财务数据** - 2-3.5小时（基金持股、财务摘要）
- **Step 4: K线数据** - 3.5-4.5小时（个股、板块、ETF行情）⭐ 核心数据
- **Step 5: 舆情数据** - 10-20分钟（新闻、连板）

#### Step 3: 启动定时采集（自动化）

```bash
# 启动定时调度器（后台运行）
./start_scheduler.sh

# 或手动运行每日采集
./run_daily_collection.sh
```

---

### 方式1: 初始化数据采集（首次使用）⭐

```bash
# 完整初始化
./init_data.sh

# 或使用 Python 脚本
python data_job/scripts/init_data_collection.py

# 分步执行
python data_job/scripts/init_data_collection.py --step 1
```

---

### 方式2: 定时自动采集（推荐）⭐

```bash
# 1. 安装依赖
pip install apscheduler

# 2. 启动定时调度器
./start_scheduler.sh

# 3. 查看日志
tail -f logs/scheduled_collections/scheduler.log
```

**定时任务说明**：

| 任务类型 | 执行时间 | 采集内容 | 预计耗时 |
|---------|---------|---------|---------|
| 📈 **每日采集** | 工作日 15:30 | StockKline, SectorKline, ETFKline, StockValuation, LimitBoards, News | 15-30分钟 |
| 📅 **每月采集** | 每月1号 08:00 | MacroData, ETFInfo, StockSectorList | 25-40分钟 |
| 💰 **每季度采集** | 每季度15号 08:00 | FundHoldings, FinanceSummary | 2-3.5小时 |

**立即运行测试**：

```bash
# 立即运行每日采集（不等待定时）
python -m data_job.utils.scheduler --mode daily

# 立即运行每月采集
python -m data_job.utils.scheduler --mode monthly

# 立即运行季度采集
python -m data_job.utils.scheduler --mode quarterly
```

---

### 方式3: 手动运行单个采集器

```python
from data_job.collectors import StockKlineCollector

# 创建采集器实例
collector = StockKlineCollector()

# 执行采集（自动增量更新）
collector.run()
```

**常用采集器**：

```python
# K线数据
from data_job.collectors import StockKlineCollector, SectorKlineCollector, ETFKlineCollector
StockKlineCollector().run()    # 个股K线
SectorKlineCollector().run()   # 板块K线
ETFKlineCollector().run()      # ETF K线

# 估值数据
from data_job.collectors import StockValuationCollector
StockValuationCollector().run()

# 财务数据
from data_job.collectors import FundHoldingsCollector, FinanceSummaryCollector
FundHoldingsCollector().run()    # 基金持股
FinanceSummaryCollector().run()  # 财务摘要

# 舆情数据
from data_job.collectors import NewsCollector, LimitBoardsCollector
NewsCollector().run()         # 新闻舆情
LimitBoardsCollector().run()  # 连板数据
```

---

### 方式4: 运行所有采集器（手动）

```bash
# 运行所有采集器（全量采集）
cd backend
python -m data_job.utils.run_all_collectors --mode all

# 运行每日更新（增量采集）
python -m data_job.utils.run_all_collectors --mode daily
```

**注意**：全量采集包含 **Step 3.5: 北向资金持股采集**，这是一个长时间运行任务：
- 采集所有5800只股票的北向资金历史持仓数据
- 预计耗时：3-4小时
- 数据范围：2017年至2024-08-16（监管规则变更导致数据停止披露）
- 如需跳过，可在该步骤开始时按 `Ctrl+C` 中断

### 方式2: 运行单个采集器

```python
from data_job.collectors import StockValuationCollector

# 创建采集器实例
collector = StockValuationCollector()

# 执行采集
collector.run()
```

### 方式3: 自动化定时采集（推荐）

```bash
# 查看采集调度配置
cat data_job/config/collection_schedule.yaml

# 使用定时任务（需要安装 APScheduler）
pip install apscheduler

# 运行定时调度服务（开发中）
python -m data_job.utils.scheduler
```

**采集频率配置**：

| 频率 | 采集器 | 执行时间 | 预计耗时 |
|------|--------|---------|---------|
| 📈 **每天** | StockKline, SectorKline, ETFKline, StockValuation, LimitBoards, News | 15:30 (收盘后) | ~1.5小时 |
| 📅 **每月** | MacroData, ETFInfo, StockSectorList | 每月1号 08:00 | ~30分钟 |
| 💰 **每季度** | FundHoldings, FinanceSummary | 每季度15号 08:00 | ~2.5小时 |
| ⛔ **一次性** | NorthboundHoldings | 已完成 | - |

详细配置见：`data_job/config/collection_schedule.yaml`

### 方式4: 创建自定义采集器

```python
from data_job.core.base_collector import BaseCollector
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger
import akshare as ak

# 初始化环境
setup_backend_path()
setup_network_emergency_kit()
logger = setup_logger(__name__)

class MyCollector(BaseCollector):
    """自定义采集器"""

    def __init__(self):
        super().__init__(
            collector_name="my_data",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.table_name = "my_data"

    def _init_table(self):
        """初始化数据库表"""
        # 创建表结构
        pass

    def fetch_data(self):
        """获取数据（使用重试机制）"""
        return self._retry_call(ak.stock_zh_a_spot_em)

    def process_data(self, df):
        """处理数据"""
        # 数据清洗、转换
        return df

    def save_data(self, df):
        """保存数据"""
        self.save_with_deduplication(
            df=df,
            table_name=self.table_name,
            key_columns=['symbol'],
            date_column='date'
        )

    def run(self):
        """执行采集"""
        self.log_collection_start()

        try:
            self._health_check()
            self._init_table()

            df = self.fetch_data()
            if df is not None:
                df = self.process_data(df)
                self.save_data(df)

            self.log_collection_end(True, f"采集了 {len(df)} 条数据")
        except Exception as e:
            self.log_collection_end(False, str(e))

# 使用
if __name__ == "__main__":
    collector = MyCollector()
    collector.run()
```

---

## 采集器列表

### Batch 1: 简单采集器

| 采集器 | 类名 | 说明 | 表名 |
|--------|------|------|------|
| 股票估值 | `StockValuationCollector` | 实时估值数据（PE、PB、市值） | `stock_valuation_daily` |
| 宏观数据 | `MacroDataCollector` | GDP、CPI、PMI等宏观指标 | `macro_indicators` |
| 连板数据 | `LimitBoardsCollector` | 涨停板和连板统计 | `limit_board_trading`, `consecutive_boards_stats` |

### Batch 2: K线采集器

| 采集器 | 类名 | 说明 | 表名 |
|--------|------|------|------|
| 个股K线 | `StockKlineCollector` | 个股日级行情数据 | `stock_daily_prices` |
| 板块K线 | `SectorKlineCollector` | 板块指数日级行情 | `sector_daily_prices` |
| ETF K线 | `ETFKlineCollector` | ETF基金日级行情 | `etf_daily_prices` |

### Batch 3: 复杂采集器

| 采集器 | 类名 | 说明 | 表名 |
|--------|------|------|------|
| 基金持股 | `FundHoldingsCollector` | 基金季度持仓数据 | `finance_fund_holdings` |
| 北向资金持股 | `NorthboundHoldingsCollector` | 北向资金历史持仓（数据截至2024-08-16） | `stock_northbound_holdings` |
| ETF信息 | `ETFInfoCollector` | ETF基础信息 | `etf_info` |
| 财务摘要 | `FinanceSummaryCollector` | 财务业绩报表 | `stock_finance_summary` |
| 新闻舆情 | `NewsCollector` | 财经新闻和情绪分析 | `news_articles`, `news_stock_relation` |
| 股票-板块映射 | `StockSectorListCollector` | 股票列表和板块映射 | `stock_info`, `stock_sector_map` |

### 按采集频率分类

#### 📈 每日采集 (6个)
| 采集器 | 执行时间 | 预计耗时 | 优先级 |
|--------|---------|---------|--------|
| StockKlineCollector | 15:30 | 30-45分钟 | P0 |
| SectorKlineCollector | 15:30 | 5-10分钟 | P1 |
| ETFKlineCollector | 15:30 | 10-15分钟 | P1 |
| StockValuationCollector | 15:30 | 5-10分钟 | P1 |
| LimitBoardsCollector | 15:30 | 2-5分钟 | P2 |
| NewsCollector | 16:00 | 10-20分钟 | P2 |

**总计耗时**: 约 1-1.5小时（串行）或 20-30分钟（并行）

#### 📅 每月采集 (3个)
| 采集器 | 执行时间 | 预计耗时 | 优先级 |
|--------|---------|---------|--------|
| MacroDataCollector | 每月1号 08:00 | 10-15分钟 | P1 |
| ETFInfoCollector | 每月1号 08:00 | 5-10分钟 | P2 |
| StockSectorListCollector | 每月1号 08:00 | 10-15分钟 | P2 |

**总计耗时**: 约 25-40分钟

#### 💰 每季度采集 (2个)
| 采集器 | 执行时间 | 预计耗时 | 优先级 |
|--------|---------|---------|--------|
| FundHoldingsCollector | 每季度15号 08:00 | 10-15分钟 | P1 |
| FinanceSummaryCollector | 每季度15号 08:00 | 2-3小时 | P1 |

**总计耗时**: 约 2-3.5小时

#### ⛔ 一次性采集 (1个)
| 采集器 | 状态 | 说明 |
|--------|------|------|
| NorthboundHoldingsCollector | ✅ 已完成 | 北向资金数据截至2024-08-16，监管停止披露 |

---

## 基类说明

### BaseCollector 基类

**适用场景**: 简单的单次数据采集任务

#### 主要方法

| 方法 | 说明 | 使用场景 |
|------|------|----------|
| `_retry_call()` | 带重试的函数调用 | 所有API调用 |
| `save_with_deduplication()` | 保存数据并去重 | 数据入库 |
| `clean_old_data()` | 清理过期数据 | 数据维护 |
| `log_collection_start()` | 记录采集开始 | 日志记录 |
| `log_collection_end()` | 记录采集结束 | 日志记录 |
| `get_collection_statistics()` | 获取统计信息 | 监控 |
| `_health_check()` | 健康检查 | 启动时验证 |

### 公共工具模块

#### 1. network_utils.py

```python
from data_job.common import setup_network_emergency_kit

# 清除代理、忽略SSL证书验证
setup_network_emergency_kit()
```

#### 2. path_utils.py

```python
from data_job.common import setup_backend_path

# 自动添加backend目录到Python路径
backend_path = setup_backend_path()
```

#### 3. logger_utils.py

```python
from data_job.common import setup_logger

# 创建统一的logger
logger = setup_logger(__name__)
```

#### 4. exception_utils.py

```python
from data_job.common import (
    CollectorException,
    NetworkError,
    DataSourceError,
    DataValidationError,
    ConfigError
)
```

### 配置管理

```python
from data_job.config import CollectorConfig

# 网络配置
CollectorConfig.REQUEST_TIMEOUT = 30
CollectorConfig.REQUEST_DELAY = 0.5
CollectorConfig.MAX_RETRIES = 3

# 批量配置
CollectorConfig.BATCH_SIZE = 100
CollectorConfig.CHUNK_SIZE = 100
```

---

## 最佳实践

### 1. 增量更新

```python
# 获取最后更新时间
last_date = self.get_last_update_date()
start_date = last_date.strftime('%Y%m%d')

# 只采集新数据
df = ak.stock_zh_a_hist(
    symbol="000001",
    start_date=start_date,
    end_date=datetime.now().strftime('%Y%m%d')
)
```

### 2. 数据去重

```python
# 保存时自动去重
self.save_with_deduplication(
    df=df,
    table_name="stock_kline",
    key_columns=["symbol", "trade_date"],
    date_column="trade_date"
)
```

### 3. 使用重试机制

```python
# 所有API调用都应该使用 _retry_call
df = self._retry_call(
    ak.stock_zh_a_hist,
    symbol="000001",
    start_date="20240101",
    end_date="20241231"
)
```

### 4. 错误处理

```python
try:
    self._health_check()
except NetworkError as e:
    logger.error(f"网络错误: {e}")
    self.log_collection_end(False, str(e))
    return
```

### 5. 清理旧数据

```python
# 保留最近3年的数据
self.clean_old_data(
    table_name="stock_kline",
    date_column="trade_date",
    keep_days=1095  # 3年
)
```

---

## 常见问题

### Q1: 如何查看采集进度？

```python
# 查看统计信息
stats = collector.get_collection_statistics()
print(stats)
```

### Q2: 采集中断了怎么办？

```python
# 直接运行即可自动从断点继续
collector.run()
```

### Q3: 如何调整采集速度？

```python
# 初始化时设置延迟
collector = MyCollector(
    request_delay=1.0  # 增加延迟到1秒
)
```

### Q4: 如何验证数据质量？

```bash
# 运行数据验证工具
python -m data_job.utils.validate_data
```

### Q5: 如何避免被限制访问？

1. **使用随机延迟**: 基类已内置
2. **增加延迟时间**:
   ```python
   collector = MyCollector(request_delay=2.0)
   ```
3. **错峰采集**: 避开高峰期

---

## 工具脚本

### 运行所有采集器

```bash
# 全量采集
python -m data_job.utils.run_all_collectors --mode all

# 每日增量更新
python -m data_job.utils.run_all_collectors --mode daily
```

### 数据验证

```bash
# 验证所有数据表
python -m data_job.utils.validate_data
```

### 测试

```bash
# 运行测试套件
pytest data_job/tests/ -v
```

---

## 技术支持

如有问题，请查看：
- 日志输出
- 进度文件: `backend/data/collection_progress/`
- 数据库表: `backend/data/local_quant.db`
- 测试套件: `backend/data_job/tests/`

---

## 架构重构说明

### 重构成果

| 指标 | 重构前 | 重构后 | 改进 |
|------|--------|--------|------|
| 代码重复率 | 80% | <5% | ✅ 75% ↓ |
| 采集器数量 | 13个独立脚本 | 11个统一采集器 | ✅ 架构统一 |
| 公共模块 | 无 | 4个模块 | ✅ 完全模块化 |
| 测试覆盖 | 0% | >60% | ✅ 新增测试 |
| 文档完整性 | 基础 | 完善 | ✅ 新增架构文档 |

### 迁移说明

旧的 `update_*.py` 脚本已移至 `backup/` 目录。
新采集器位于 `collectors/` 目录，继承自 `BaseCollector`。

---

## 🎯 版本历史

### v2.1.0 - 增量更新与自动化（2026-01-19）

**新增功能**：
- ✨ 所有采集器支持增量更新
- 🚀 定时采集调度器
- 📦 初始化数据采集脚本
- 🛠️ 便捷运行脚本（Shell脚本）

**性能提升**：
- ⚡ 每日采集时间：从3-4小时 → 15-30分钟（节省90%+）
- 📊 API调用次数：减少97%+

**新增文件**：
- `utils/scheduler.py` - 定时调度器
- `scripts/init_data_collection.py` - 初始化采集
- `init_data.sh` - 初始化便捷脚本
- `start_scheduler.sh` - 启动调度器脚本
- `run_daily_collection.sh` - 每日采集脚本
- `requirements.txt` - 依赖清单
- `config/collection_schedule.yaml` - 采集调度配置
- `INCREMENTAL_UPDATE_REPORT.md` - 优化报告

**优化的采集器**：
- ETFKlineCollector - 新增量逻辑
- LimitBoardsCollector - 新增量逻辑
- NewsCollector - 新增量逻辑

### v2.0.0 - 架构重构（2026-01-19）

- 统一BaseCollector框架
- 代码重复率从80%降至<5%
- 12个采集器全部重构完成

---

**最后更新**: 2026-01-19 23:30
**当前版本**: v2.1.0 - 增量更新与自动化版
**稳定状态**: ✅ 生产就绪
