# K线采集器集成完成报告

> **集成日期**: 2026-02-23
> **状态**: ✅ 已完成并集成到采集系统

---

## 📋 集成概述

已将优化版个股K线采集器 (`StockKlineCollectorOptimized`) 完整集成到现有数据采集系统中，支持通过环境变量灵活配置采集参数。

---

## 🔧 集成内容

### 1. 导出优化版采集器

**文件**: `data_job/collectors/__init__.py`

```python
from .stock_kline_collector_optimized import StockKlineCollectorOptimized

__all__ = [
    # ...
    'StockKlineCollectorOptimized',  # 优化版K线采集器（可配置）
    # ...
]
```

### 2. 采集脚本集成

**文件**: `data_job/utils/run_all_collectors.py`

#### 新增配置函数

```python
def get_kline_collector_config():
    """
    获取K线采集器配置（从环境变量或使用默认值）
    支持 KLINE_MODE 环境变量选择预设模式
    """
    import os
    mode = os.getenv('KLINE_MODE', 'normal')

    if mode == 'safe':
        config = {'max_workers': 2, 'min_delay': 0.2, ...}
    elif mode == 'aggressive':
        config = {'max_workers': 5, 'min_delay': 0.1, ...}
    else:  # normal
        config = {'max_workers': 3, 'min_delay': 0.15, ...}

    # 环境变量覆盖
    # ...
    return config
```

#### 更新采集器使用

**全量采集 (`run_all_collectors()`)**:
```python
# 获取配置并使用优化版
kline_config = get_kline_collector_config()
collectors_step4 = [
    ('StockKline', StockKlineCollectorOptimized(**kline_config)),
    ('SectorKline', SectorKlineCollector()),
    ('ETFKline', ETFKlineCollector()),
]
```

**每日更新 (`run_daily_update()`)**:
```python
kline_config = get_kline_collector_config()
daily_collectors = [
    # ...
    ('StockKline', StockKlineCollectorOptimized(**kline_config)),
    # ...
]
```

### 3. 调度器兼容

**文件**: `data_job/utils/scheduler.py`

调度器已导入优化版采集器，会自动使用（通过调用 `run_daily_update()`）。

### 4. 配置文件

**文件**: `.env.kline.example`

提供配置示例，用户可复制为 `.env` 并根据需求修改：

```bash
# 预设模式选择
KLINE_MODE=normal  # safe/normal/aggressive

# 高级配置（可选）
# KLINE_MAX_WORKERS=3
# KLINE_MIN_DELAY=0.15
# KLINE_MAX_DELAY=0.35
# KLINE_BATCH_SIZE=500
# KLINE_BATCH_REST=5
```

---

## 🚀 使用方式

### 方式1: 默认配置（推荐）

直接运行，使用 normal 模式：

```bash
# 每日更新
python -m data_job.utils.run_all_collectors --mode daily

# 或使用调度器
python -m data_job.utils.scheduler --mode daily
```

### 方式2: 环境变量配置

```bash
# 使用安全模式（无代理）
KLINE_MODE=safe python -m data_job.utils.run_all_collectors --mode daily

# 使用激进模式（有代理）
KLINE_MODE=aggressive python -m data_job.utils.run_all_collectors --mode daily

# 自定义参数
KLINE_MAX_WORKERS=4 KLINE_MIN_DELAY=0.2 python -m data_job.utils.run_all_collectors --mode daily
```

### 方式3: .env 文件配置

```bash
# 1. 复制配置文件
cp .env.kline.example .env

# 2. 编辑 .env 修改配置
# KLINE_MODE=aggressive

# 3. 运行（自动加载 .env）
python -m data_job.utils.run_all_collectors --mode daily
```

### 方式4: 直接运行优化版采集器

```bash
# 使用预设模式
python -m data_job.collectors.stock_kline_collector_optimized --mode normal

# 自定义参数
python -m data_job.collectors.stock_kline_collector_optimized \
    --workers 3 \
    --min-delay 0.15 \
    --max-delay 0.35 \
    --batch-size 500
```

---

## 📊 性能对比

| 模式 | 线程数 | 延迟范围 | 预计耗时 | 适用场景 |
|------|--------|----------|----------|----------|
| 原版 | 1 | 0.5s固定 | 60-90分钟 | 串行采集 |
| safe | 2 | 0.2-0.4s | 25-35分钟 | 无代理 |
| **normal** | **3** | **0.15-0.35s** | **20-25分钟** | **默认推荐** |
| aggressive | 5 | 0.1-0.2s | 12-15分钟 | 有代理 |

> 基于 5803 只股票的估算，实际时间可能因网络状况有所不同

---

## ⚙️ 配置参数说明

### 参数列表

| 参数 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `max_workers` | int | 3 | 1-5 | 并发线程数 |
| `min_delay` | float | 0.15 | 0.1-0.5 | 最小延迟（秒） |
| `max_delay` | float | 0.35 | 0.2-1.0 | 最大延迟（秒） |
| `batch_size` | int | 500 | 100-1000 | 批次大小 |
| `batch_rest_time` | int | 5 | 3-10 | 批次间休息（秒） |
| `skip_uptodate` | bool | True | - | 跳过已更新的股票 |

### 预设模式

#### safe (安全模式)
- **并发**: 2 线程
- **延迟**: 0.2-0.4 秒
- **批次**: 200 只
- **休息**: 8 秒
- **适用**: 无代理，稳定性优先

#### normal (正常模式) ⭐ 默认
- **并发**: 3 线程
- **延迟**: 0.15-0.35 秒
- **批次**: 500 只
- **休息**: 5 秒
- **适用**: 平衡效率和风险

#### aggressive (激进模式)
- **并发**: 5 线程
- **延迟**: 0.1-0.2 秒
- **批次**: 1000 只
- **休息**: 3 秒
- **适用**: 有代理，速度优先

---

## 🛡️ 防封机制

优化版采集器内置多层防封机制：

1. **随机延迟**: 每次请求前随机延迟（min_delay ~ max_delay）
2. **批次处理**: 分批采集，达到批次大小后保存数据并休息
3. **批次休息**: 每批次之间休息 `batch_rest_time` 秒
4. **指数退避**: 网络错误时自动增加重试延迟
5. **线程池控制**: 限制最大并发数，避免瞬间大量请求

---

## 📝 向后兼容性

- ✅ 原版 `StockKlineCollector` 仍然可用
- ✅ 默认使用优化版，无需修改现有代码
- ✅ 可通过环境变量灵活切换配置

---

## 🔍 相关文件

| 文件 | 说明 |
|------|------|
| `data_job/collectors/stock_kline_collector_optimized.py` | 优化版采集器实现 |
| `data_job/collectors/stock_kline_collector.py` | 原版采集器（保留） |
| `data_job/utils/run_all_collectors.py` | 采集脚本（已更新） |
| `data_job/utils/scheduler.py` | 调度器（已更新） |
| `.env.kline.example` | 配置示例 |
| `data_job/docs/KLINE_OPTIMIZATION.md` | 优化文档 |
| `run_kline_simple.py` | 独立运行脚本 |
| `run_kline_interactive.py` | 交互式配置脚本 |
| `run_kline_collection.sh` | Shell 脚本 |

---

## ✅ 集成检查清单

- [x] 导出 `StockKlineCollectorOptimized`
- [x] 更新 `run_all_collectors.py` 使用优化版
- [x] 更新 `run_daily_update()` 使用优化版
- [x] 更新 `scheduler.py` 导入优化版
- [x] 添加配置函数 `get_kline_collector_config()`
- [x] 创建配置示例 `.env.kline.example`
- [x] 保持向后兼容性

---

## 🎯 下一步

1. **测试验证**: 运行一次小规模采集测试
   ```bash
   KLINE_MODE=safe python -m data_job.utils.run_all_collectors --mode daily
   ```

2. **监控效果**: 观察采集速度和稳定性

3. **调整配置**: 根据实际效果调整参数

4. **应用到其他采集器**: 考虑将相同优化模式应用到板块K线、ETF K线等采集器

---

**集成完成**: 2026-02-23
