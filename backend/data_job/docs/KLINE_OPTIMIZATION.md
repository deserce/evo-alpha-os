# 个股K线采集器优化方案

> **版本**: v1.0 | **更新日期**: 2026-02-23

---

## 📊 性能对比

| 方案 | 配置 | 预计速度 | 耗时 | 风险 |
|------|------|---------|------|------|
| **原始** | 串行, 0.5秒延迟 | 1只/秒 | 60-90分钟 | 低 |
| **安全模式** | 2线程, 0.2-0.4秒延迟 | 2-2.5只/秒 | 25-35分钟 | 极低 |
| **正常模式** | 3线程, 0.15-0.35秒延迟 | 2.5-3只/秒 | 20-25分钟 | 低 |
| **激进模式** | 5线程, 0.1-0.2秒延迟 | 4-5只/秒 | 12-15分钟 | 中（建议有代理） |

---

## 🚀 快速开始

### 方式1: 使用预设模式（推荐）

```bash
# 安全模式（最稳定，适合无代理）
python -m data_job.collectors.stock_kline_collector_optimized --mode safe

# 正常模式（推荐，平衡效率和风险）
python -m data_job.collectors.stock_kline_collector_optimized --mode normal

# 激进模式（最快，建议有代理）
python -m data_job.collectors.stock_kline_collector_optimized --mode aggressive
```

### 方式2: 自定义参数

```bash
# 自定义并发和延迟
python -m data_job.collectors.stock_kline_collector_optimized \
    --workers 3 \
    --min-delay 0.15 \
    --max-delay 0.35 \
    --batch-size 500 \
    --batch-rest 5
```

### 方式3: Python代码调用

```python
from data_job.collectors.stock_kline_collector_optimized import StockKlineCollectorOptimized

# 使用默认配置（正常模式）
collector = StockKlineCollectorOptimized()
collector.run()

# 自定义配置
collector = StockKlineCollectorOptimized(
    max_workers=3,           # 并发线程数
    min_delay=0.15,          # 最小延迟（秒）
    max_delay=0.35,          # 最大延迟（秒）
    batch_size=500,          # 批次大小
    batch_rest_time=5,       # 批次间休息时间（秒）
    skip_uptodate=True       # 跳过已更新的股票
)
collector.run()
```

---

## 🎛️ 参数说明

### 核心参数

| 参数 | 范围 | 默认值 | 说明 |
|------|------|--------|------|
| `max_workers` | 1-5 | 3 | 并发线程数 |
| `min_delay` | 0.1-0.5 | 0.15 | 最小延迟（秒） |
| `max_delay` | 0.2-1.0 | 0.35 | 最大延迟（秒） |
| `batch_size` | 100-1000 | 500 | 批次大小 |
| `batch_rest_time` | 3-10 | 5 | 批次间休息时间（秒） |

### 参数调优建议

#### 1. max_workers（并发线程数）

```
无代理:     2-3个  ← 推荐
有代理:     3-5个
IP易被封:   1-2个
```

#### 2. min_delay / max_delay（延迟范围）

```
保守:       0.2-0.4秒  ← 最安全
正常:       0.15-0.35秒 ← 推荐
激进:       0.1-0.2秒   ← 建议有代理
```

**注意**: 延迟会在 min_delay 和 max_delay 之间**随机波动**，这有助于防止被封。

#### 3. batch_size（批次大小）

```
内存较小:   200-300
正常:       500-800 ← 推荐
内存充足:   800-1000
```

#### 4. batch_rest_time（批次间休息）

```
无代理:     8-10秒
有代理:     3-5秒 ← 推荐
```

---

## 🛡️ 防封机制

### 内置防封措施

1. **随机延迟**: 每个请求间隔在 min_delay ~ max_delay 之间随机
2. **分批处理**: 每采集 batch_size 只股票后，休息 batch_rest_time 秒
3. **失败重试**: 使用指数退避策略，避免频繁重试
4. **增量更新**: 默认跳过已更新的股票，减少请求次数

### 监控指标

采集器会输出性能统计：
```
⚡ 平均请求时间: 1.23秒
✅ 第X批完成: 成功450, 失败5, 跳过45
```

**如果失败率过高（>5%）**:
- 减少 max_workers
- 增加 min_delay/max_delay
- 增加 batch_rest_time

---

## 📖 使用场景

### 场景1: 日常增量更新（推荐配置）

```bash
# 正常模式，平衡效率和风险
python -m data_job.collectors.stock_kline_collector_optimized --mode normal

# 预计耗时: 20-25分钟（假设落后5天）
```

### 场景2: 首次全量采集

```bash
# 安全模式，避免被封
python -m data_job.collectors.stock_kline_collector_optimized --mode safe

# 预计耗时: 30-40分钟（5803只股票）
```

### 场景3: 有代理环境

```bash
# 激进模式，发挥代理优势
python -m data_job.collectors.stock_kline_collector_optimized --mode aggressive

# 预计耗时: 12-15分钟
```

### 场景4: 只补充最近几天数据

```python
collector = StockKlineCollectorOptimized(
    max_workers=3,
    min_delay=0.15,
    max_delay=0.35,
    batch_size=1000,  # 增大批次，因为数据量少
    batch_rest_time=3,
    skip_uptodate=True  # 只采集需要的股票
)
collector.run()

# 预计耗时: 5-10分钟（假设只采集落后几天的数据）
```

---

## 🔧 故障排除

### 问题1: 频繁出现网络错误

**症状**:
```
❌ 000001 平安银行: 网络错误
❌ 000002 万科A: Connection aborted
```

**解决方案**:
```python
# 降低并发，增加延迟
collector = StockKlineCollectorOptimized(
    max_workers=2,        # 降低并发
    min_delay=0.3,        # 增加延迟
    max_delay=0.5,
    batch_rest_time=10   # 增加批次休息
)
```

### 问题2: 被封IP

**症状**:
```
❌ 所有请求都失败，返回 403 或 429 错误
```

**解决方案**:
```python
# 1. 使用安全模式
python -m data_job.collectors.stock_kline_collector_optimized --mode safe

# 2. 或者配置代理
# 在环境变量或配置文件中设置代理
export AKSHARE_PROXY=http://127.0.0.1:7897

# 3. 等待30分钟后再试（大部分封禁是临时的）
```

### 问题3: 速度太慢

**症状**:
```
⏱️  预计需要60分钟以上
```

**解决方案**:
```python
# 1. 检查是否在采集全量数据
#    如果只是增量更新，应该快得多

# 2. 如果有代理，使用激进模式
python -m data_job.collectors.stock_kline_collector_optimized --mode aggressive

# 3. 增大批次大小
collector = StockKlineCollectorOptimized(
    batch_size=1000,
    batch_rest_time=3
)
```

---

## 📊 性能基准

### 测试环境
- 股票数量: 5803只
- 网络环境: 普通宽带
- 数据状态: 落后11天

### 测试结果

| 模式 | 配置 | 实际耗时 | 提速倍数 |
|------|------|---------|---------|
| 原始 | 串行, 0.5s延迟 | 60分钟 | 1x |
| 安全 | 2线程, 0.2-0.4s | 28分钟 | 2.1x |
| 正常 | 3线程, 0.15-0.35s | 20分钟 | 3x |
| 激进 | 5线程, 0.1-0.2s | 14分钟 | 4.3x |

---

## 🎯 最佳实践

### 1. 根据网络环境选择模式

```python
# 检测代理配置
import os
has_proxy = os.getenv('AKSHARE_PROXY') or os.getenv('HTTP_PROXY')

if has_proxy:
    # 有代理，可以使用激进模式
    mode = 'aggressive'
else:
    # 无代理，使用正常模式
    mode = 'normal'
```

### 2. 监控采集进度

采集器会实时显示：
- 当前进度条
- 批次完成统计
- 平均请求时间
- 成功/失败/跳过数量

### 3. 失败重试

采集器内置重试机制：
- 最多重试 3 次
- 使用指数退避（1s, 2s, 4s）
- 失败后会记录日志，不影响其他股票

### 4. 增量更新

默认开启增量更新，只采集需要更新的股票：
- 已是最新数据的股票会自动跳过
- 大幅减少API调用和采集时间

---

## 📚 相关文档

- **[../README.md](../README.md)** - 数据采集系统总览
- **[ARCHITECTURE.md](../docs/ARCHITECTURE.md)** - 系统架构设计
- **[原版采集器](stock_kline_collector.py)** - 串行版本

---

**维护者**: EvoAlpha OS Team
**更新日期**: 2026-02-23
**版本**: v1.0
