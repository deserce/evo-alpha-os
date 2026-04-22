# Backend 脚本说明

> **更新日期**: 2026-02-23

---

## 📁 目录结构

```
backend/
├── main.py                        # ⭐ FastAPI应用入口
├── auto_pipeline.py               # ⭐ 自动化流水线调度器
│
├── *.sh                           # ⭐ Shell便捷脚本（7个）
│   ├── init_data.sh               # 初始化数据采集
│   ├── start_scheduler.sh         # 启动调度器
│   ├── check_scheduler.sh         # 检查调度器状态
│   ├── restart_scheduler.sh       # 重启调度器
│   ├── run_pipeline.sh            # 运行流水线
│   ├── run_daily_collection.sh    # 每日数据采集
│   └── monitor_pipeline.sh        # 监控流水线
│
├── tests/                         # 🧪 测试脚本
│   ├── test_api.py                # API测试
│   ├── test_alternative_apis.py   # 备选API测试
│   ├── test_kline_dates.py        # K线日期测试
│   ├── test_optimized_collection.py # 优化采集测试
│   ├── test_progress_bars.py      # 进度条测试
│   └── test_rps_incremental.py    # RPS增量测试
│
└── scripts/
    └── archive/                   # 📦 存档脚本（17个）
        ├── analyze_all_tables.py
        ├── audit_database.py
        ├── create_simple_sector_list.py
        ├── diagnose_network.py
        ├── fast_stock_kline_collector.py
        ├── fetch_stock_kline_anti_ban.py
        ├── fetch_stock_kline_yahoo.py
        ├── quick_daily_update_parallel.py
        ├── quick_daily_update.py
        ├── rebuild_sector_map_from_history.py
        ├── rebuild_sector_map.py
        ├── run_failed_collectors.py
        ├── sector_kline_with_proxy.py
        ├── simple_update.py
        ├── update_and_verify.py
        └── verify_completeness.py
```

---

## ⭐ 核心脚本说明

### Python 脚本

#### main.py
**FastAPI 应用入口**
- 启动 RESTful API 服务器
- 46个 API 端点
- Swagger 文档: http://localhost:8000/api/docs

```bash
python main.py
```

#### auto_pipeline.py
**自动化交易流水线调度器**
- 每日流程: 数据采集 → RPS计算 → 策略选股
- 季度流程: 季度数据 → 股票池更新 → RPS计算 → 策略选股
- 定时调度: APScheduler

```bash
# 立即运行每日流程
python auto_pipeline.py --mode daily

# 启动定时调度器
python auto_pipeline.py --mode schedule
```

---

### Shell 脚本

#### init_data.sh
**初始化数据采集**
- 首次全量数据采集（7-9小时）
- 分步执行支持

```bash
# 完整初始化
./init_data.sh

# 分步执行
./init_data.sh --step 1  # 基础数据
./init_data.sh --step 2  # 市场数据
./init_data.sh --step 3  # 财务数据
./init_data.sh --step 4  # K线数据（耗时最长）
./init_data.sh --step 5  # 舆情数据
```

#### start_scheduler.sh
**启动定时调度器**
- 每日采集: 工作日 15:30
- 季度采集: 每季度15号 08:00

```bash
./start_scheduler.sh
```

#### check_scheduler.sh
**检查调度器状态**

```bash
./check_scheduler.sh
```

#### restart_scheduler.sh
**重启调度器**

```bash
./restart_scheduler.sh
```

#### run_pipeline.sh
**运行完整流水线**

```bash
# 运行每日流程
./run_pipeline.sh daily

# 运行季度流程
./run_pipeline.sh quarterly
```

#### run_daily_collection.sh
**运行每日数据采集**

```bash
./run_daily_collection.sh
```

#### monitor_pipeline.sh
**监控流水线并通知**

```bash
./monitor_pipeline.sh
```

---

## 🧪 测试脚本

所有测试脚本位于 `tests/` 目录：

| 脚本 | 用途 |
|------|------|
| test_api.py | 测试 API 端点 |
| test_alternative_apis.py | 测试备选数据源 |
| test_kline_dates.py | 测试 K 线日期范围 |
| test_optimized_collection.py | 测试优化后的采集器 |
| test_progress_bars.py | 测试进度条显示 |
| test_rps_incremental.py | 测试 RPS 增量计算 |

**运行测试**:
```bash
# 运行所有测试
pytest tests/

# 运行特定测试
python tests/test_api.py
```

---

## 📦 存档脚本

历史开发过程中的临时、调试和实验脚本已移至 `scripts/archive/`，包括：

### 数据库工具（4个）
- analyze_all_tables.py - 分析数据库表结构
- audit_database.py - 数据库质量审计
- update_and_verify.py - 更新并验证数据
- verify_completeness.py - 验证数据完整性

### 调试工具（2个）
- diagnose_network.py - 网络诊断
- create_simple_sector_list.py - 创建板块列表

### 实验性采集器（5个）
- fast_stock_kline_collector.py - 快速采集实验
- fetch_stock_kline_anti_ban.py - 防封采集旧版
- fetch_stock_kline_yahoo.py - Yahoo数据源
- sector_kline_with_proxy.py - 代理采集实验
- quick_daily_update_parallel.py - 并行采集实验

### 旧版本脚本（6个）
- quick_daily_update.py - 旧快速更新
- rebuild_sector_map.py - 旧重建映射
- rebuild_sector_map_from_history.py - 从历史重建
- run_failed_collectors.py - 失败重试
- simple_update.py - 旧简单更新

> 💡 **提示**: 存档脚本保留用于历史参考和紧急情况回退，日常使用请参考核心脚本。

---

## 🚀 快速开始

### 1. 启动 API 服务器
```bash
python main.py
# 访问: http://localhost:8000/api/docs
```

### 2. 初始化数据（首次使用）
```bash
./init_data.sh
```

### 3. 启动自动化调度
```bash
./start_scheduler.sh
```

### 4. 检查状态
```bash
./check_scheduler.sh
```

---

## 📝 维护说明

### 添加新脚本
- **核心功能**: 放在 `backend/` 根目录
- **测试脚本**: 放在 `tests/` 目录
- **临时/实验**: 放在 `scripts/archive/` 并记录用途

### 脚本命名规范
- **核心脚本**: `name.py` 或 `name.sh`
- **测试脚本**: `test_*.py`
- **Shell脚本**: `*_tool.sh`
- **避免**: 使用 `quick_`, `fast_`, `simple_` 等临时性命名

---

**维护者**: EvoAlpha OS Team
**更新日期**: 2026-02-23
