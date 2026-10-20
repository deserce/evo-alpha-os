# CLAUDE.md

# CLAUDE.md - 项目核心宪法（10x Engineer Mode - 基于Boris Cherny & 社区编译）

## 核心原则（永远遵守）
- 简单第一：避免过度工程化，优先最小可行实现。
- 无懒惰：找到根因，不要打补丁。
- 最小改动：只碰必须改的文件，避免引入新bug。
- 防御式：所有输入校验、错误处理、边界case全覆盖。
- 学习型：每次我纠正你，立即在末尾添加规则到本文件（并建议commit）。
- 中文回复

## 工作流编排（必须的思考顺序）
1. 复杂任务 → 强制Plan模式：先完整描述方案、文件列表、风险、步骤，等我批准。
2. 如果>3文件改动 → 拆成小任务，逐个完成。
3. 执行前 → 确认上下文干净（必要时 /clear 或总结旧上下文）。
4. 写代码后 → 列出可能破坏点 + 建议测试。
5. Bug修复 → 先写 failing test → 修到通过 → 再写 regression test。
6. 任务结束前 → 验证闭环：运行测试/命令/截图/日志，证明成功。

## 子代理策略（Subagent）
- 遇到重负载（如研究、测试、大文件分析）→ 召唤子代理：e.g. "Spawn a subagent to verify this" 或用 parallel worktree。
- 子代理类型示例：code-reviewer, test-writer, security-auditor, simplifier。
- 主代理只协调，别自己干脏活。

## 自我提升循环（Self-Improvement Loop）
- 每次出错/我反馈 → 你自己写："Lesson learned: [描述] Rule: [新规则]" → 加到本文件末尾。
- 定期审视：/summarize lessons → 精简重复规则。
- 目标：用时越长，Claude 越像你的资深搭档。

## 自主修复 & 安全
- Bug时：自己debug → 贴日志给我看过程 → 修好前别说完成。
- 安全检查：扫描硬编码secret、SQL injection、路径穿越等。
- 从不：console.log 生产、any类型、全局变量、忽略错误。

## 项目特定（你来填）
- 技术栈： [e.g. Next.js 14 + TypeScript + Tailwind]
- 命名规范：camelCase 函数，PascalCase 组件
- 测试要求：每个新功能至少3个测试（happy + edge + error）
- 其他：...

Last updated: [2/23] - 继续迭代！

## Project Overview

EvoAlpha OS is a data-driven quantitative Alpha opportunity discovery platform for A-share (Chinese) stocks. The system combines quantitative screening (RPS factors, technical strategies) with AI agent analysis to identify trading opportunities.

**Tech Stack:**
- **Backend**: FastAPI + Python 3.10+, SQLite (local) + PostgreSQL/CockroachDB (cloud)
- **Frontend**: Next.js 16 + TypeScript + TailwindCSS 4
- **Data Source**: Multi-source architecture (pytdx, EFinance, AKShare, Tushare)
- **Scheduling**: APScheduler
- **Quantitative Computing**: Pandas, NumPy, SciPy, TA-Lib

## Data Source Architecture (v2.0 - 2026/03/08)

### Multi-Source Priority System

| Data Type | Primary | Fallback 1 | Fallback 2 |
|-----------|---------|-----------|-----------|
| **K-line** | pytdx (通达信协议) | EFinance | AKShare |
| **Financial** | EFinance | AKShare | - |
| **Fund Holdings** | EFinance | AKShare | - |
| **Limit Boards** | AKShare (独有) | EFinance | - |
| **News** | AKShare (独有) | - | - |

### Key Changes from v1.0

1. **pytdx (通达信协议) added as primary K-line source**
   - Stability: ~99% (binary protocol, rarely banned)
   - Speed: Fast (0.01s delay)
   - Platform: Pure Python, perfect Mac support

2. **Automatic source fallback**
   - Primary source failure → auto-switch to next
   - Success rate tracking for each source

3. **New files added:**
   - `data_job/core/tdx_adapter.py` - pytdx adapter
   - `data_job/collectors/multi_source_kline_collector.py` - Multi-source K-line collector
   - `DATA_SOURCE_ARCHITECTURE.md` - Design document
   - `DATA_SOURCE_UPGRADE_REPORT.md` - Implementation report

### Using Multi-Source Adapters

```python
# K-line data (pytdx priority)
from data_job.core.multi_source_adapter import MultiSourceAdapter
adapter = MultiSourceAdapter(data_type='kline')
df = adapter.get_stock_kline('600519', '2024-01-01', '2024-03-08')

# Or use collector directly
from data_job.collectors.multi_source_kline_collector import StockKlineCollectorV2
collector = StockKlineCollectorV2()
collector.run()
```

### Dependencies

```bash
# Install pytdx
pip install pytdx
```

### Verified pytdx Servers

- `218.75.126.9:7709` - 招商证券上海 ✅
- `119.147.212.81:7709` - 招商证券深圳
- `101.71.170.10:7709` - 华泰证券

## Architecture

### Three-Layer Design

```
┌─────────────────────────────────────────────────┐
│  Orchestration Layer (调度层)                     │
│  auto_pipeline.py - Unified scheduler           │
│  - Daily flow: Collection → RPS → Strategy      │
│  - Quarterly flow: Collection → Pool → RPS      │
└───────────────────┬─────────────────┬───────────┘
                    │                 │
        ┌───────────▼─────────┐  ┌───▼──────────────┐
        │  Data Layer (数据层) │  │  Quant Layer     │
        │  data_job/          │  │  quant_engine/   │
        │  - 13 collectors    │  │  - RPS calc      │
        │  - Scheduling       │  │  - Strategies    │
        │  - Incremental      │  │  - Stock pool    │
        └─────────────────────┘  └──────────────────┘
```

### Key Modules

**data_job/** - Data collection system with 13 collectors:
- Stock/Sector/ETF K-lines (daily OHLCV)
- Valuation metrics, limit boards, news
- Fund holdings, financial summaries
- All inherit from `BaseCollector` with retry/progress tracking

**quant_engine/** - Quantitative analysis engine:
- RPS (Relative Price Strength) calculators for stocks/sectors/ETFs
- Strategy execution (MRGC pattern strategy)
- Stock pool maintenance (filters by fund/northbound holdings)
- All calculators inherit from `BaseFeatureCalculator`

**auto_pipeline.py** - Main orchestrator:
- Runs daily pipeline (weekdays 15:30): data → RPS → stock selection
- Runs quarterly pipeline: quarterly data → stock pool update → RPS → selection
- Scheduling via APScheduler with cron triggers

## Common Commands

### Backend Development

```bash
# Navigate to backend
cd backend

# Install dependencies
pip3 install -r requirements.txt

# Run FastAPI development server
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Run daily data collection only
python3 -m data_job.utils.scheduler --mode daily

# Run RPS calculation only (incremental - last 3 days)
python3 -m quant_engine.runner.feature_runner --mode daily

# Run RPS calculation (full - last 400 days)
python3 -m quant_engine.runner.feature_runner --mode init

# Run strategy selection
python3 -m quant_engine.runner.strategy_runner --strategy mrgc

# Run complete automated pipeline (data → RPS → strategy)
python3 auto_pipeline.py --mode daily  # Run once immediately
python3 auto_pipeline.py --mode schedule  # Start scheduler

# Monitor scheduler status
./check_scheduler.sh
```

### Database Operations

```bash
# Initialize database tables
python3 -c "from app.core.database import init_db; init_db()"

# Full data initialization (7-9 hours for historical data)
./init_data.sh
```

### Frontend Development

```bash
# Navigate to frontend
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev

# Build for production
npm run build

# Start production server
npm start

# Run linting
npm run lint
```

## Path Adaptation Pattern

**CRITICAL**: Python scripts must add backend to sys.path:

```python
# At the top of scripts in backend/ subdirectories
import sys
import os
from pathlib import Path

backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
```

This is already done in existing scripts - maintain this pattern when creating new scripts.

## Database Considerations

**SQLite Limitations**:
- No concurrent writes - ensure only one process writes at a time
- Use transactions for atomic operations
- Kill existing Python processes if database is locked

```bash
# Find and kill Python processes accessing the database
ps aux | grep python
pkill -f python
```

## Adding New Collectors

1. Create file in `backend/data_job/collectors/new_collector.py`
2. Inherit from `BaseCollector`:

```python
from data_job.core.base_collector import BaseCollector

class NewCollector(BaseCollector):
    def __init__(self):
        super().__init__(
            collector_name="new_collector",
            request_delay=1.0,
            max_retries=3
        )

    def collect(self):
        # Implement collection logic
        # Use self.engine for database
        # Use self.logger for logging
        pass
```

3. Add to `backend/data_job/collectors/__init__.py`
4. Register in `CollectionScheduler` if needed

## Adding New RPS Calculators

1. Create file in `backend/quant_engine/calculators/new_rps_calculator.py`
2. Inherit from `BaseFeatureCalculator`:

```python
from quant_engine.core.base_feature_calculator import BaseFeatureCalculator

class NewRPSCalculator(BaseFeatureCalculator):
    def get_source_table(self) -> str:
        return "source_kline_table"

    def get_target_table(self) -> str:
        return "quant_feature_new_rps"

    def get_entity_column(self) -> str:
        return "symbol"  # or "sector_name"

    def get_periods(self) -> list[int]:
        return [5, 10, 20, 50, 120, 250]
```

3. Register in `FeatureRunner`

## Logging Requirements

**Use standard logging only** - NOT loguru:

```python
import logging

# Correct
logger = logging.getLogger(__name__)
logger.info("Message")
logger.warning("Warning message")
logger.error("Error message")

# WRONG - logger.success() doesn't exist in standard logging
# logger.success("Message")  # ❌ Don't use
```

## Incremental Update Pattern

Data collectors support incremental updates:

```python
# In BaseCollector subclasses
# Get last update date from progress file
last_date = self.progress.get("last_date")

# Fetch only new data
df = ak.stock_zh_a_daily(symbol="000001", start_date=last_date)

# Save with automatic deduplication
self._save_to_db(df, table_name)
```

## Environment Configuration

Key environment variables in `backend/.env`:

```bash
# Database
LOCAL_DB_PATH=data/local_quant.db
CLOUD_DB_HOST=localhost
CLOUD_DB_PORT=5433

# Data collection
AKSHARE_PROXY=          # Optional proxy
FORCE_SYNC_KLINE=false  # Whether to sync K-line to cloud

# Scheduler
SCHEDULER_TIMEZONE=Asia/Shanghai
DAILY_JOB_TIME=15:30
```

## File Locations

- Database: `backend/data/local_quant.db`
- Progress tracking: `backend/data/collection_progress/*.json`
- Logs: Check stdout/stderr or configure file logging
- Static data: `backend/data/`

## Common Issues

1. **Database locked**: Multiple processes accessing SQLite
   - Kill other Python processes: `pkill -f python`

2. **Module not found**: Wrong working directory
   - Ensure you're in `backend/` directory or use path adaptation

3. **No data in tables**: Missing initial data collection
   - Run: `./init_data.sh` or `python3 auto_pipeline.py --mode daily`

4. **RPS calculation fails**: Missing source data
   - Run data collection first: `python3 -m data_job.utils.scheduler --mode daily`

## Code Style Notes

- Use 4 spaces for indentation
- Max line length: 100
- Follow PEP 8 for Python code
- Use type hints where appropriate
- Add docstrings for classes and public methods
- Use f-strings for string formatting

## Testing

```bash
# Backend tests
cd backend
pytest

# Run specific test file
pytest tests/test_base_collector.py

# Frontend tests
cd frontend
npm test
```

## Documentation References

- Main README: `README.md`
- Backend: `backend/README.md`
- Data collection: `backend/data_job/README.md`
- Quant engine: `backend/quant_engine/README.md`
- Auto pipeline: `backend/AUTO_PIPELINE_README.md`
- Data source architecture: `backend/DATA_SOURCE_ARCHITECTURE.md` ⭐ NEW
- Data source upgrade: `backend/DATA_SOURCE_UPGRADE_REPORT.md` ⭐ NEW

---

Last updated: 2026/03/08 - 数据源架构升级 v2.0
