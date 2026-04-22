# 多数据源架构使用指南

## 概述

本项目已支持多数据源采集架构，可以同时使用AKShare和聚宽等多个数据源，提高数据采集的稳定性和可靠性。

## 快速开始

### 1. 基础使用（仅AKShare）

默认情况下，系统只使用AKShare数据源，无需额外配置：

```bash
cd backend
python3 auto_pipeline.py --mode daily
```

### 2. 启用聚宽数据源

#### 步骤1: 注册聚宽账号

1. 访问 https://www.joinquant.com/
2. 注册账号（个人版或专业版）
3. 登录后查看账号ID（用户名）

#### 步骤2: 安装聚宽SDK

```bash
cd backend
pip3 install jqdatasdk
```

#### 步骤3: 配置环境变量

复制配置示例并修改：

```bash
cp .env.datasource.example .env
```

编辑 `.env` 文件：

```bash
# 启用聚宽
ENABLE_JOINQUANT=true
JOINQUANT_ACCOUNT_ID=your_account_id
JOINQUANT_PASSWORD=your_password

# 设置优先级（聚宽作为备用数据源）
AKSHARE_PRIORITY=10  # 主数据源
JOINQUANT_PRIORITY=5  # 备用数据源

# 采集策略
DATA_SOURCE_STRATEGY=priority  # 按优先级尝试，失败则降级
```

#### 步骤4: 测试聚宽连接

```bash
python3 -c "
import jqdatasdk as jq
jq.auth('your_account', 'your_password')
print(jq.get_query_count())
"
```

如果显示剩余查询次数，说明配置成功。

### 3. 运行采集

配置完成后，正常运行采集命令即可：

```bash
python3 auto_pipeline.py --mode daily
```

系统会自动使用配置的数据源。

## 采集策略说明

系统支持三种采集策略：

### 1. priority（优先级模式）

按优先级顺序尝试数据源，失败则降级到下一个：

```bash
DATA_SOURCE_STRATEGY=priority
AKSHARE_PRIORITY=10  # 先尝试AKShare
JOINQUANT_PRIORITY=5  # AKShare失败后尝试聚宽
```

**适用场景**：希望使用主数据源，备用数据源仅作降级

### 2. parallel（并行模式）

同时调用所有数据源，返回第一个成功的结果：

```bash
DATA_SOURCE_STRATEGY=parallel
```

**适用场景**：希望提高采集速度和成功率

### 3. majority（多数模式，暂未实现）

多源采集，取多数一致的结果：

```bash
DATA_SOURCE_STRATEGY=majority
```

**适用场景**：需要数据一致性验证（开发中）

## 在Collector中使用数据源

### 方式1: 自动使用（推荐）

如果你的collector继承自`BaseCollector`，直接调用`get_data_source_manager()`即可：

```python
from data_job.core.base_collector import BaseCollector

class MyCollector(BaseCollector):
    def __init__(self):
        super().__init__(
            collector_name="my_collector",
            request_timeout=30,
            request_delay=0.5
        )

    def fetch_stock_data(self, symbol, start_date, end_date):
        # 获取数据源管理器
        manager = self.get_data_source_manager()

        if manager is None:
            # 回退到原有方式
            return self._retry_call(ak.stock_zh_a_hist, ...)

        # 使用数据源管理器（支持自动降级）
        df = manager.fetch_with_fallback(
            'get_stock_kline',
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust='qfq'
        )

        return df
```

### 方式2: 手动注入数据源管理器

```python
from data_job.core.data_source import DataSourceManager
from data_job.core.akshare_adapter import AKShareAdapter
from data_job.core.joinquant_adapter import JoinQuantAdapter

# 创建数据源管理器
manager = DataSourceManager(
    sources=[
        AKShareAdapter(priority=10),
        JoinQuantAdapter(
            account_id='your_account',
            password='your_password',
            priority=5
        )
    ],
    strategy='priority'
)

# 注入到collector
collector = MyCollector(data_source_manager=manager)
```

## 配置参数说明

### 完整配置示例

```bash
# ============ 采集策略 ============
DATA_SOURCE_STRATEGY=parallel  # parallel/priority/majority

# ============ 数据源启用 ============
ENABLE_AKSHARE=true
ENABLE_JOINQUENT=false  # 初期建议先禁用，测试通过后再启用

# ============ AKShare配置 ============
AKSHARE_PRIORITY=10      # 优先级（0-100，数字越大越优先）
AKSHARE_TIMEOUT=30       # 请求超时（秒）

# ============ 聚宽配置 ============
JOINQUANT_ACCOUNT_ID=your_account_id
JOINQUANT_PASSWORD=your_password
JOINQUENT_PRIORITY=5     # 建议设为5，作为备用
JOINQUENT_TIMEOUT=30

# ============ 并行采集配置 ============
PARALLEL_TIMEOUT=60      # 并行采集总超时（秒）
VALIDATION_ENABLED=true  # 是否启用数据验证
```

## 数据源对比

| 特性 | AKShare | 聚宽 |
|-----|---------|------|
| 成本 | 免费 | 免费版有限额 |
| 数据完整性 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 稳定性 | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| 速度 | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| K线数据 | ✅ | ✅ |
| 板块K线 | ✅ | ✅（需映射） |
| ETF K线 | ✅ | ✅ |
| 涨跌停 | ✅ | ❌（需另实现） |
| 基金持仓 | ✅ | ❌ |
| 估值数据 | ✅ | ✅ |

## 常见问题

### Q1: 聚宽认证失败

**错误**: `聚宽认证失败`

**解决**:
1. 检查账号密码是否正确
2. 确认账号是否激活
3. 查看是否超出调用限额

```bash
# 检查剩余查询次数
python3 -c "import jqdatasdk as jq; jq.auth('account', 'password'); print(jq.get_query_count())"
```

### Q2: AKShare频繁失败

**解决**:
1. 检查网络连接
2. 增加超时时间: `AKSHARE_TIMEOUT=60`
3. 启用聚宽作为备用: `ENABLE_JOINQUANT=true`

### Q3: 采集速度慢

**解决**:
1. 使用并行模式: `DATA_SOURCE_STRATEGY=parallel`
2. 调整超时时间
3. 检查网络状况

### Q4: 如何只使用某个数据源？

```bash
# 只使用AKShare
ENABLE_AKSHARE=true
ENABLE_JOINQUANT=false

# 只使用聚宽
ENABLE_AKSHARE=false
ENABLE_JOINQUANT=true
```

## 监控和日志

### 查看数据源健康状态

```python
from data_job.core.base_collector import BaseCollector

collector = BaseCollector("test")
manager = collector.get_data_source_manager()

if manager:
    health = manager.check_health()
    print(health)
    # 输出: {'akshare': True, 'joinquant': False}
```

### 日志输出

运行采集时会看到数据源使用情况：

```
2024-01-01 10:00:00 | INFO | 数据源管理器已创建: 策略=parallel, 数据源=['akshare', 'joinquant']
2024-01-01 10:00:01 | INFO | 尝试使用数据源 akshare 调用 get_stock_kline
2024-01-01 10:00:02 | INFO | 数据源 akshare 成功获取数据
```

## 性能优化建议

1. **使用缓存**: 重复查询的数据会被缓存
2. **并行采集**: 使用 `parallel` 策略提高速度
3. **合理设置超时**: 根据网络情况调整 `TIMEOUT` 参数
4. **监控调用次数**: 聚宽有调用限额，合理使用

## 迁移指南

### 从旧版本迁移

如果你有现有的collector，改造步骤：

1. **继承BaseCollector**: 确保你的collector继承自`BaseCollector`
2. **使用数据源管理器**: 调用`self.get_data_source_manager()`
3. **最小改动**: 只替换数据获取部分，其他逻辑保持不变

### 示例改造

**改造前**:
```python
df = self._retry_call(
    ak.stock_zh_a_hist,
    symbol=code,
    start_date=start_date,
    end_date=end_date
)
```

**改造后**:
```python
manager = self.get_data_source_manager()
if manager:
    df = manager.fetch_with_fallback(
        'get_stock_kline',
        symbol=code,
        start_date=start_date,
        end_date=end_date
    )
else:
    # 降级到原有方式
    df = self._retry_call(
        ak.stock_zh_a_hist,
        symbol=code,
        start_date=start_date,
        end_date=end_date
    )
```

## 下一步

- [ ] 注册聚宽账号
- [ ] 安装jqdatasdk
- [ ] 配置.env文件
- [ ] 测试聚宽连接
- [ ] 运行采集验证
- [ ] 根据需要调整优先级和策略

## 技术支持

如有问题，请查看：
- 项目README: `/Users/deserce/Desktop/EvoAlpha-OS/README.md`
- 后端文档: `/Users/deserce/Desktop/EvoAlpha-OS/backend/README.md`
- 计划文档: `/Users/deserce/.claude/plans/federated-hugging-biscuit.md`
