# Quant Engine 模块重构架构设计 v3.0

> **版本**: v3.0
> **创建时间**: 2026-01-20
> **状态**: 📝 设计中
> **参考**: data_job v2.1.0

---

## 📋 目录

1. [现状分析](#现状分析)
2. [架构设计原则](#架构设计原则)
3. [模块化重构方案](#模块化重构方案)
4. [因子计算引擎设计](#因子计算引擎设计)
5. [股票池维护方案](#股票池维护方案)
6. [策略体系设计](#策略体系设计)
7. [自动化运行方案](#自动化运行方案)
8. [实施计划](#实施计划)

---

## 现状分析

### ✅ 已有的资源

#### 数据资源
| 数据表 | 记录数 | 状态 | 用途 |
|--------|--------|------|------|
| `stock_daily_prices` | 3,742,987 | ✅ 有数据 | 个股K线 |
| `sector_daily_prices` | ? | ✅ 已采集 | 板块K线 |
| `etf_daily_prices` | 51,399 | ✅ 有数据 | ETF K线 |
| `finance_fund_holdings` | 34,470 | ✅ 有数据 | 基金持股 |
| `stock_northbound_holdings` | 3,068,742 | ✅ 有数据 | 北向持股 |
| `stock_info` | 5,800 | ✅ 有数据 | 股票信息 |
| `stock_sector_map` | 62,151 | ✅ 有数据 | 股票-板块映射 |

#### 代码资源
| 文件 | 功能 | 代码质量 | 重复度 |
|------|------|----------|--------|
| `features/calc_indicators.py` | 个股RPS计算 | ✅ 优秀 | 低 |
| `features/calc_sector_rps.py` | 板块RPS计算 | ✅ 优秀 | 高（与个股重复）|
| `core/tdx_lib.py` | 通达信公式库 | ✅ 完整 | - |
| `strategies/mrgc_strategy.py` | MRGC策略 | ✅ 可用 | - |
| `strategies/base_strategy.py` | 策略基类 | ✅ 可用 | - |
| `pool/maintain_pool.py` | 股票池维护 | ⚠️ 需调整 | - |

#### 存在的问题
1. **代码重复**：个股和板块RPS计算代码高度重复（90%相似）
2. **命名不统一**：
   - 个股RPS表：`quant_feature_rps`
   - 板块RPS表：`quant_feature_sector_rps`
   - ETF RPS表：`etf_feature_rps`
   - 字段名不一致：`rps_250` vs `rps250`
3. **周期不统一**：
   - 个股：[3, 5, 10, 20, 50, 120, 250]
   - 板块：[5, 10, 20, 50, 120, 250]
   - ETF：[20, 50, 250] (目标)
4. **缺少统一的因子计算引擎**
5. **ETF RPS计算器缺失**

---

## 架构设计原则

### 1. 参考 data_job v2.1.0 架构
```
参考成功案例：
- ✅ 分层架构（core/common/config/collectors）
- ✅ 统一基类（BaseCollector）
- ✅ 公共工具模块（network/logger/exception）
- ✅ 配置集中管理
- ✅ 增量更新机制
```

### 2. DRY原则（Don't Repeat Yourself）
```
目标：
- 统一的因子计算基类
- 通用的RPS计算逻辑
- 可复用的数据处理流程
```

### 3. 统一命名规范
```
表命名：
- quant_feature_stock_rps  (个股)
- quant_feature_sector_rps (板块)
- quant_feature_etf_rps   (ETF)

字段命名：
- rps_5, rps_10, rps_20, rps_50, rps_120, rps_250
- chg_5, chg_10, chg_20, chg_50, chg_120, chg_250
- ma_20, ma_50, ma_250
```

### 4. 可扩展性
```
设计方向：
- 新增标的类型只需继承基类
- 新增因子类型只需实现计算方法
- 新增策略只需注册到调度器
```

---

## 模块化重构方案

### 📁 新目录结构

```
backend/quant_engine/
├── core/                          # 核心框架层
│   ├── __init__.py
│   ├── base_feature_calculator.py # 因子计算基类 ⭐
│   ├── base_strategy.py           # 策略基类
│   └── tdx_lib.py                 # 通达信公式库（保持）
│
├── common/                        # 公共工具层
│   ├── __init__.py
│   ├── path_utils.py              # 路径工具
│   ├── logger_utils.py            # 日志工具
│   └── exception_utils.py         # 异常工具
│
├── config/                        # 配置管理层
│   ├── __init__.py
│   ├── calculator_config.py       # 计算器配置
│   └── strategy_config.py         # 策略配置
│
├── calculators/                   # 因子计算器
│   ├── __init__.py
│   ├── stock_rps_calculator.py    # 个股RPS计算器
│   ├── sector_rps_calculator.py   # 板块RPS计算器
│   ├── etf_rps_calculator.py      # ETF RPS计算器 ⭐
│   └── indicator_calculator.py    # 技术指标计算器
│
├── pool/                          # 股票池管理
│   ├── __init__.py
│   └── pool_maintainer.py         # 股票池维护器
│
├── strategies/                    # 策略实现
│   ├── __init__.py
│   ├── mrgc_strategy.py           # MRGC策略
│   └── base_strategy.py           # 策略基类
│
├── runner/                        # 运行器
│   ├── __init__.py
│   ├── feature_runner.py          # 因子计算运行器 ⭐
│   ├── pool_runner.py             # 股票池运行器
│   └── strategy_runner.py         # 策略运行器
│
├── utils/                         # 工具函数
│   ├── __init__.py
│   ├── validator.py               # 数据验证
│   └── backtest.py                # 回测工具
│
└── scripts/                       # 独立脚本
    ├── init_features.py           # 初始化因子计算
    └── quant_scheduler.py         # 量化调度器
```

---

## 因子计算引擎设计

### 🎯 核心设计：统一的因子计算基类

```python
# core/base_feature_calculator.py

class BaseFeatureCalculator(ABC):
    """
    因子计算基类

    核心功能：
    1. 统一的数据加载（支持增量窗口）
    2. 通用的RPS计算逻辑（向量化）
    3. 标准化的保存逻辑（幂等性）
    4. 完整的日志记录
    """

    def __init__(self):
        self.engine = get_engine()

    @abstractmethod
    def get_source_table(self) -> str:
        """返回源表名"""
        pass

    @abstractmethod
    def get_target_table(self) -> str:
        """返回目标表名"""
        pass

    @abstractmethod
    def get_entity_column(self) -> str:
        """返回标的列名（symbol/sector_name）"""
        pass

    @abstractmethod
    def get_periods(self) -> list[int]:
        """返回计算周期"""
        pass

    @abstractmethod
    def should_filter(self, df) -> pd.DataFrame:
        """数据过滤逻辑（可选，如板块黑名单）"""
        return df

    def load_data(self, start_date=None):
        """加载数据（支持增量窗口）"""
        pass

    def compute_rps(self, df):
        """核心RPS计算（向量化，复用）"""
        pass

    def compute_ma(self, df):
        """计算均线因子"""
        pass

    def save_to_db(self, df, mode='append'):
        """保存数据（幂等性）"""
        pass

    def run_init(self):
        """全量初始化"""
        pass

    def run_daily(self):
        """增量更新"""
        pass
```

### 📊 三个计算器继承基类

#### 1. StockRPSCalculator（个股）
```python
class StockRPSCalculator(BaseFeatureCalculator):
    def get_source_table(self):
        return "stock_daily_prices"

    def get_target_table(self):
        return "quant_feature_stock_rps"

    def get_entity_column(self):
        return "symbol"

    def get_periods(self):
        return [5, 10, 20, 50, 120, 250]

    def should_filter(self, df):
        return df  # 个股不过滤
```

#### 2. SectorRPSCalculator（板块）
```python
class SectorRPSCalculator(BaseFeatureCalculator):
    def get_source_table(self):
        return "sector_daily_prices"

    def get_target_table(self):
        return "quant_feature_sector_rps"

    def get_entity_column(self):
        return "sector_name"

    def get_periods(self):
        return [5, 10, 20, 50, 120, 250]

    def should_filter(self, df):
        # 板块黑名单过滤
        blacklist = ["昨日", "连板", "涨停", "ST", "AB股", "昨日涨停"]
        pattern = "|".join(blacklist)
        return df[~df['sector_name'].str.contains(pattern, na=False)]
```

#### 3. ETFRPSCalculator（ETF）
```python
class ETFRPSCalculator(BaseFeatureCalculator):
    def get_source_table(self):
        return "etf_daily_prices"

    def get_target_table(self):
        return "quant_feature_etf_rps"

    def get_entity_column(self):
        return "symbol"

    def get_periods(self):
        # 与板块周期一致
        return [5, 10, 20, 50, 120, 250]

    def should_filter(self, df):
        return df  # ETF不过滤
```

### 🔧 代码复用效果

**重构前**：
- `calc_indicators.py`: 183行
- `calc_sector_rps.py`: 206行
- 重复代码: ~170行
- 代码重复率: ~85%

**重构后**：
- `base_feature_calculator.py`: 150行（基类）
- `stock_rps_calculator.py`: 30行（配置）
- `sector_rps_calculator.py`: 40行（配置+过滤）
- `etf_rps_calculator.py`: 30行（配置）
- 总代码: 250行
- 代码复用率: ~80% ✅

---

## 股票池维护方案

### 🎯 核心思路

基于北向持股和基金持股比例筛选核心股票池

### 📊 数据源

| 表名 | 关键字段 | 用途 |
|------|----------|------|
| `finance_fund_holdings` | symbol, fund_ratio, report_date | 基金持股比例 |
| `stock_northbound_holdings` | symbol, hold_value, hold_date | 北向持股市值 |

### 🏊‍♂️ 股票池筛选逻辑

```python
# 核心筛选条件
FUND_THRESHOLD = 5        # 基金持股 > 5%
NORTH_THRESHOLD = 100000000  # 北向持仓 > 1亿元（单位：元）

WITH LatestFund AS (
    SELECT DISTINCT ON (symbol)
        symbol,
        fund_ratio,
        report_date
    FROM finance_fund_holdings
    ORDER BY symbol, report_date DESC
),
LatestNorth AS (
    SELECT DISTINCT ON (symbol)
        symbol,
        hold_value,
        hold_date
    FROM stock_northbound_holdings
    ORDER BY symbol, hold_date DESC
),
BasicInfo AS (
    SELECT symbol, name
    FROM stock_info
)
SELECT
    b.symbol,
    b.name,
    CASE
        WHEN f.fund_ratio > 5 AND n.hold_value > 100000000 THEN '机构+北向双重仓'
        WHEN f.fund_ratio > 5 THEN '基金重仓(>5%)'
        WHEN n.hold_value > 100000000 THEN '北向重仓(>1亿)'
    END as reason,
    CURRENT_DATE as add_date
FROM BasicInfo b
LEFT JOIN LatestFund f ON b.symbol = f.symbol
LEFT JOIN LatestNorth n ON b.symbol = n.symbol
WHERE
    f.fund_ratio > 5
    OR n.hold_value > 100000000
```

### 📅 更新频率

**一季度一次**（每季度初更新）

---

## 策略体系设计

### 🎯 当前策略

保留MRGC策略作为示例

### 🚀 未来扩展方向

#### 1. 公共逻辑模块

```python
# utils/indicator_utils.py

class IndicatorUtils:
    """公共指标计算工具"""

    @staticmethod
    def is_new_high_250days(df):
        """是否创250日新高"""
        return df['close'] >= df['close'].rolling(250).max()

    @staticmethod
    def count_new_highs_by_sector(trade_date):
        """统计各板块创新高的股票个数"""
        pass

    @staticmethod
    def get_price_position(df, days=250):
        """当前价格在N日内的位置（0-100）"""
        return df['close'].rolling(days).apply(
            lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
        )
```

#### 2. 策略结果表

```sql
CREATE TABLE quant_strategy_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name VARCHAR(50),      -- 策略名称
    trade_date DATE,                -- 交易日期
    symbol VARCHAR(20),              -- 股票代码
    symbol_name VARCHAR(50),         -- 股票名称
    signal_type VARCHAR(20),         -- 信号类型（buy/sell/watch）
    meta_info TEXT,                  -- 元数据（JSON格式）
    confidence FLOAT,                -- 置信度（0-100）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(strategy_name, trade_date, symbol)
);
```

#### 3. 策略示例

**示例1：一年新高筛选**
```python
class NewHighStrategy(BaseStrategy):
    """筛选创250日新高的股票"""

    def run(self, trade_date=None):
        # 1. 加载股票池
        pool = self.get_stock_pool('core_pool')

        # 2. 筛选创新高
        for symbol in pool['symbol']:
            df = self.load_kline(symbol, days=260)
            if is_new_high_250days(df):
                self.save_result({
                    'symbol': symbol,
                    'signal_type': 'new_high_250',
                    'meta_info': {'price': df['close'].iloc[-1]}
                })
```

**示例2：板块共振分析**
```python
class SectorResonanceStrategy(BaseStrategy):
    """板块共振：统计板块内新高股票数量"""

    def run(self, trade_date=None):
        # 1. 获取各板块
        sectors = self.get_all_sectors()

        # 2. 统计每个板块新高股票数
        for sector in sectors:
            stocks = self.get_sector_stocks(sector)
            new_high_count = count_new_highs(stocks, trade_date)

            # 3. 板块共振信号
            if new_high_count >= 5:  # 阈值
                self.save_result({
                    'symbol': sector,
                    'signal_type': 'sector_resonance',
                    'meta_info': {'new_high_count': new_high_count}
                })
```

#### 4. 验证和回测

```python
# utils/backtest.py

class BacktestEngine:
    """简单回测引擎"""

    def backtest_strategy(self, strategy_name, start_date, end_date):
        """回测策略"""
        # 1. 加载历史信号
        signals = self.load_signals(strategy_name, start_date, end_date)

        # 2. 计算收益
        returns = []
        for date, group in signals.groupby('trade_date'):
            # 计算当日选中股票的N日收益
            daily_return = self.calculate_return(group, days=5)
            returns.append(daily_return)

        # 3. 统计结果
        total_return = (1 + pd.Series(returns)).prod() - 1
        win_rate = sum(r > 0 for r in returns) / len(returns)

        return {
            'total_return': total_return,
            'win_rate': win_rate,
            'signal_count': len(signals)
        }
```

---

## 自动化运行方案

### 📅 任务调度规划

| 任务 | 频率 | 触发时间 | 依赖 | 耗时 |
|------|------|----------|------|------|
| 股票池维护 | 每季度 | 每季度1号 08:00 | 数据采集 | 10-15分钟 |
| 个股RPS计算 | 每天 | 15:30 | K线数据 | 10-20分钟 |
| 板块RPS计算 | 每天 | 15:30 | K线数据 | 5-10分钟 |
| ETF RPS计算 | 每天 | 15:30 | K线数据 | 2-5分钟 |
| 技术指标计算 | 每天 | 15:45 | RPS数据 | 10-15分钟 |
| 策略选股 | 每天 | 16:00 | 所有因子 | 15-30分钟 |

### 🔧 调度器设计

```python
# scripts/quant_scheduler.py

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

class QuantScheduler:
    """量化任务调度器"""

    def __init__(self):
        self.scheduler = BlockingScheduler()

    def setup_jobs(self):
        """配置所有定时任务"""

        # 每季度1号 08:00 - 股票池维护
        self.scheduler.add_job(
            self.run_pool_maintenance,
            trigger=CronTrigger(day=1, month='1,4,7,10', hour=8, minute=0),
            id='pool_maintenance',
            name='股票池维护'
        )

        # 每天 15:30 - 因子计算
        self.scheduler.add_job(
            self.run_feature_calculation,
            trigger=CronTrigger(day_of_week='mon-fri', hour=15, minute=30),
            id='feature_calculation',
            name='因子计算'
        )

        # 每天 16:00 - 策略选股
        self.scheduler.add_job(
            self.run_strategy_selection,
            trigger=CronTrigger(day_of_week='mon-fri', hour=16, minute=0),
            id='strategy_selection',
            name='策略选股'
        )

    def run_feature_calculation(self):
        """运行所有因子计算"""
        from quant_engine.calculators.stock_rps_calculator import StockRPSCalculator
        from quant_engine.calculators.sector_rps_calculator import SectorRPSCalculator
        from quant_engine.calculators.etf_rps_calculator import ETFRPSCalculator

        calculators = [
            StockRPSCalculator(),
            SectorRPSCalculator(),
            ETFRPSCalculator()
        ]

        for calc in calculators:
            try:
                calc.run_daily()
            except Exception as e:
                logger.error(f"{calc.__class__.__name__} 失败: {e}")

    def run_strategy_selection(self):
        """运行所有策略"""
        pass
```

---

## 实施计划

### Phase 1: 核心框架搭建（第1周）

**目标**: 建立统一的因子计算框架

**任务清单**:
- [ ] 创建 `core/` 目录结构
- [ ] 创建 `common/` 公共工具模块
- [ ] 创建 `config/` 配置管理
- [ ] 实现 `BaseFeatureCalculator` 基类
- [ ] 单元测试基类核心方法

### Phase 2: 因子计算器迁移（第2周）

**目标**: 迁移现有RPS计算器到新框架

**任务清单**:
- [ ] 迁移 `StockRPSCalculator`
- [ ] 迁移 `SectorRPSCalculator`
- [ ] 新增 `ETFRPSCalculator`
- [ ] 统一表名和字段名
- [ ] 测试所有计算器

### Phase 3: 股票池维护（第3周）

**目标**: 完善股票池维护逻辑

**任务清单**:
- [ ] 更新 `pool_maintainer.py`
- [ ] 调整数据表字段映射
- [ ] 实现季度自动更新
- [ ] 验证股票池数据

### Phase 4: 策略体系完善（第4周）

**目标**: 完善策略框架和MRGC策略

**任务清单**:
- [ ] 更新 `BaseStrategy` 基类
- [ ] 迁移 `MrgcStrategy`
- [ ] 添加策略结果验证
- [ ] 实现简单回测功能

### Phase 5: 自动化调度（第5周）

**目标**: 实现完整的自动化流水线

**任务清单**:
- [ ] 实现量化调度器
- [ ] 集成到数据采集系统
- [ ] 添加监控和日志
- [ ] 编写使用文档

---

## 数据库表结构变更

### 新表结构

#### 1. quant_feature_stock_rps
```sql
CREATE TABLE quant_feature_stock_rps (
    symbol VARCHAR(20),
    trade_date DATE,
    -- 涨幅
    chg_5 FLOAT, chg_10 FLOAT, chg_20 FLOAT,
    chg_50 FLOAT, chg_120 FLOAT, chg_250 FLOAT,
    -- RPS
    rps_5 FLOAT, rps_10 FLOAT, rps_20 FLOAT,
    rps_50 FLOAT, rps_120 FLOAT, rps_250 FLOAT,
    -- 均线
    ma_20 FLOAT, ma_50 FLOAT, ma_250 FLOAT,
    PRIMARY KEY (symbol, trade_date)
);
CREATE INDEX idx_stock_rps_date ON quant_feature_stock_rps(trade_date);
```

#### 2. quant_feature_sector_rps
```sql
CREATE TABLE quant_feature_sector_rps (
    sector_name VARCHAR(50),
    trade_date DATE,
    -- 涨幅
    chg_5 FLOAT, chg_10 FLOAT, chg_20 FLOAT,
    chg_50 FLOAT, chg_120 FLOAT, chg_250 FLOAT,
    -- RPS
    rps_5 FLOAT, rps_10 FLOAT, rps_20 FLOAT,
    rps_50 FLOAT, rps_120 FLOAT, rps_250 FLOAT,
    PRIMARY KEY (sector_name, trade_date)
);
CREATE INDEX idx_sector_rps_date ON quant_feature_sector_rps(trade_date);
```

#### 3. quant_feature_etf_rps
```sql
CREATE TABLE quant_feature_etf_rps (
    symbol VARCHAR(20),
    trade_date DATE,
    -- 涨幅
    chg_5 FLOAT, chg_10 FLOAT, chg_20 FLOAT,
    chg_50 FLOAT, chg_120 FLOAT, chg_250 FLOAT,
    -- RPS
    rps_5 FLOAT, rps_10 FLOAT, rps_20 FLOAT,
    rps_50 FLOAT, rps_120 FLOAT, rps_250 FLOAT,
    PRIMARY KEY (symbol, trade_date)
);
CREATE INDEX idx_etf_rps_date ON quant_feature_etf_rps(trade_date);
```

#### 4. quant_stock_pool
```sql
CREATE TABLE quant_stock_pool (
    pool_name VARCHAR(50),
    symbol VARCHAR(20),
    name VARCHAR(100),
    add_date DATE,
    reason TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (pool_name, symbol, add_date)
);
```

#### 5. quant_strategy_results
```sql
CREATE TABLE quant_strategy_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name VARCHAR(50),
    trade_date DATE,
    symbol VARCHAR(20),
    symbol_name VARCHAR(100),
    signal_type VARCHAR(20),
    meta_info TEXT,
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(strategy_name, trade_date, symbol)
);
CREATE INDEX idx_strategy_date ON quant_strategy_results(trade_date);
CREATE INDEX idx_strategy_name ON quant_strategy_results(strategy_name);
```

---

## 文件迁移清单

### 需要重命名的文件

| 旧文件名 | 新文件名 | 说明 |
|---------|---------|------|
| `features/calc_indicators.py` | `calculators/stock_rps_calculator.py` | 个股RPS |
| `features/calc_sector_rps.py` | `calculators/sector_rps_calculator.py` | 板块RPS |
| - | `calculators/etf_rps_calculator.py` | ETF RPS（新增）|
| `config.py` | `config/calculator_config.py` | 配置文件 |
| `runner.py` | `runner/feature_runner.py` | 运行器 |

### 需要创建的新文件

1. `core/base_feature_calculator.py` - 因子计算基类
2. `common/__init__.py` - 公共工具模块
3. `common/path_utils.py` - 路径工具
4. `common/logger_utils.py` - 日志工具
5. `common/exception_utils.py` - 异常工具
6. `config/__init__.py` - 配置管理
7. `runner/strategy_runner.py` - 策略运行器
8. `scripts/init_features.py` - 初始化脚本
9. `scripts/quant_scheduler.py` - 调度器

---

## 成功指标

### 代码质量
- 代码重复率：从 85% → <10%
- 统一基类覆盖率：100%
- 命名规范性：100%

### 功能完整性
- 3个RPS计算器正常运行
- ETF RPS计算完成
- 股票池自动更新
- 策略自动化运行

### 性能指标
- 因子计算耗时：<30分钟
- 增量更新耗时：<10分钟
- 内存占用：<2GB

---

## 总结

本架构设计旨在：

1. ✅ **消除代码重复**：通过统一基类实现代码复用
2. ✅ **统一命名规范**：表名、字段名、文件名全部标准化
3. ✅ **支持ETF扩展**：新增ETF RPS计算器，周期与板块一致
4. ✅ **完善股票池**：基于北向和基金持股筛选
5. ✅ **自动化运行**：完整的调度系统，支持不同频率任务

**核心优势**：
- 模块化设计，易于扩展
- 代码复用率高，维护成本低
- 命名统一，易于理解
- 自动化程度高，减少人工干预

---

**文档版本**: v3.0
**创建时间**: 2026-01-20
**状态**: ✅ 设计完成，待评审
