# AGENTS.md


# AGENTS.md - 项目核心宪法（10x Engineer Mode - 基于Boris Cherny & 社区编译）

## 核心原则（永远遵守）
- 简单第一：避免过度工程化，优先最小可行实现。
- 无懒惰：找到根因，不要打补丁。
- 最小改动：只碰必须改的文件，避免引入新bug。
- 防御式：所有输入校验、错误处理、边界case全覆盖。
- 资源克制：功能设计必须同时约束内存、CPU、磁盘、I/O、线程进程和后台唤醒，不得给本机或其他项目造成持续负荷。
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
- 目标：用时越长，Codex 越像你的资深搭档。

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

Lesson learned: 2026-06-02 自动下载失败的根因不是数据库损坏，而是旧 Codex 自动化与本地 launchd 调度抢锁，且非阻断 ETF/指数采集在网络异常时拖住核心策略链。 Rule: 正式 Data Core 写库只能走本地无大模型执行器；旧自动化不得直接执行写库 workflow；所有支撑行情任务必须设置超时并保持 best-effort，不能阻断日K质量门、RPS 和 MRGC。

Lesson learned: 2026-06-10 每日 health 只检查目标日 freshness 不足以证明历史 K 线健全，可能掩盖早期历史区间覆盖不足和 stock_info 上市日期缺失。 Rule: Data Core 巡检必须同时报告当日质量和历史 K 线健康摘要，至少覆盖历史交易日范围、低覆盖日期数、各市场最低覆盖、零数据交易日和 stock_info.list_date 完整性。

Lesson learned: 2026-07-04 个股资料面板的财务图和 F10 不能只依赖单一重资料源，旧财务摘要行也可能因为已有报告期而跳过绝对营收/净利补齐。 Rule: 财务图必须明确区分真实净利润与 EPS 兜底；单只股票刷新必须补齐 stock_finance_summary 缺失字段并给 F10 配置真实备用源，mootdx/AKShare 超时或失败不得让页面长期显示空壳。

Lesson learned: 2026-07-15 处理本项目端口冲突时，直接卸载或停止其它项目的 launchd 服务会越界影响用户现有工作流。 Rule: 遇到端口冲突必须先确认端口归属；未经用户明确同意，不得停止、卸载或改动其它项目的后台服务；优先把当前项目迁移到空闲端口并同步当前项目前端代理配置。

Lesson learned: 2026-07-18 Next.js 开发模式适合修改期间热更新，但会让首次页面切换触发按需编译，不能作为 EvoAlpha 日常使用的交付状态。 Rule: 前端修改完成并验收后，除非用户明确要求继续开发，必须执行生产构建并用 `next start` 启动；交付前验证主要页面响应耗时和后端代理端口。

Lesson learned: 2026-07-19 Watch 历史扫描与正式提醒事件完全隔离时，指定历史日期的行动箱会误显示为空，K 线也无法核对触发时点。 Rule: 指定日期的行动箱必须合并同一历史扫描的只读当日状态；K 线只标记首次 `IN_ZONE`/`TRIGGERED` 转折，不得重复标记冷却日，也不得将历史状态伪装为可确认的正式通知。

Lesson learned: 2026-07-19 将修复路径量能上限作为无方向的硬拦截，并对所有确认方法强制要求当日收阳，会漏掉放量反转后“低点连续抬高 + 缩量小阴线 + MACD 转强”的建设性整理确认。 Rule: 修复路径量能门必须区分放量上涨与放量下跌；结构+MACD 确认应支持受控小阴线，但必须同时满足短期低点抬高、当日缩量、收盘守住 MA10/MA20、MACD 收敛或翻红和有限跌幅，并用 600641 的 2026-06-09 至 2026-06-12 作为回归样本。

Lesson learned: 2026-07-19 EvoAlpha 前端使用登录 shell 会受用户启动脚本影响；但 macOS 26 下 LaunchAgent 直接以 Documents 为 `WorkingDirectory` 启动 Anaconda 又可能触发 `library load mig callout failed` 或隐私预检失败。 Rule: 前端必须直接启动 `next start`；Anaconda 后端允许沿用已验证的 `/bin/zsh -lc` 方式，但 plist 不得设置 Documents `WorkingDirectory`，须在命令内 `cd` 后 `exec` Python，并显式设置 HOME、PATH、locale、PYTHONPATH 和 OPENBLAS/OMP 线程上限；重启后必须验证监听端口、页面响应和其它项目服务仍存活。

Lesson learned: 2026-07-19 低吸 setup 若沿用 WATCHING 阶段的旧触发价、次日因轻微越界立即退出，或只用突破前高/重上 MA10 确认，会漏掉士兰微这类先形成底部结构再反转的机会。 Rule: WATCHING 不得向新 setup 继承触发价；进入回调区后保留可配置确认窗口并以失效价管理；默认确认使用“双底或摆动低点抬高”与“MACD 绿柱收敛或翻红”共振，RPS/结构质量门保持不降级，所有参数变更必须用当前 Watchlist 历史遍历对照后再启用新版本。

Lesson learned: 2026-07-19 邮件日报只读正式 `watch_daily_snapshot` 时，会与 Watchlist 页面默认展示的最新人工历史遍历结果不一致，导致页面有确认触发而邮件显示 0 只。 Rule: 指定历史日期的日报必须优先使用与 Watchlist 相同的最新已完成 MANUAL scan，按 `pool_ids` 分组统计与列出股票，明示数据口径和规则版本；仅在该日无历史遍历时回退到正式盘后快照。

Lesson learned: 2026-07-19 DATA_BLOCKED 事件在数据恢复后若尚未创建投递记录，旧即时邮件查询仍可能补发已失效告警；失败投递也可能在恢复后被 22:30 重试。 Rule: 即时通知和失败重试都必须在发送时重新检查事件的 resolved_at 与确认状态；已恢复、已确认或已删除事件不得创建或继续发送邮件，已有待投递记录统一标记 SKIPPED，并用“阻断后恢复、发送前恢复”的回归样本验证。

Lesson learned: 2026-07-19 路径 A 只按 MA20/MA50 的 3% 距离和 80% 均量筛选，会把大量仅回踩 MA10 的强势股挤入路径 B；同时把全局确认选项放在路径 A 区域、却让建设性整理只对路径 B 生效，会造成设置含义误读。 Rule: 路径 A 支撑均线必须显式版本化并允许独立开启 MA10，参数放宽前后必须对照 setup、触发和总信号膨胀率；规则页必须分开显示路径资格、两路径共享确认与路径专属确认，并明确多种确认方式采用 OR 语义。

Lesson learned: 2026-07-19 Watchlist 生产包重建后，旧 Chrome 标签可能继续持有失效的静态资源哈希；同一个股的主面板和资料面板若使用不同 React Query key，还会重复下载图表、生命周期和信号数据。 Rule: 生产重建后必须以带版本参数的正确路由新开 Chrome 标签；交付前分别测量 HTML、静态资源和业务 API；同一语义的请求必须共用 query key，不得因组件名不同而重复拉取大数据。

Lesson learned: 2026-07-19 历史遍历转折与正式提醒同日同股同类时，若后端用已确认的正式事件压掉历史结果，而前端又隐藏已确认事件，行动箱会错误显示为零；只展示首次转折也会与历史结果的“当日状态”口径不一致。 Rule: “扫描状态是否展示”与“正式提醒是否待处理”必须分离；行动箱展示所选日期全部 `IN_ZONE`/`TRIGGERED`/`INVALIDATED`/`UNKNOWN` 状态，持续 `TRIGGERED` 明确标注“冷却中”，但只有首次转折生成事件和邮件；已确认/已解决的正式事件不得压掉同日扫描状态；交付前必须对最新扫描的池成员×交易日覆盖率和当日各状态数做一致性对账。

Lesson learned: 2026-07-19 在用户正在使用 Watchlist 时先停掉 `next start` 再进行十余秒生产构建，浏览器会将部署空窗表现为前端崩溃，即使服务日志没有运行时异常。 Rule: EvoAlpha 前端必须使用 `NEXT_DIST_DIR` 在独立目录完成预构建，构建和 TypeScript 验证通过后才停服务并原子切换 `.next`；切换失败必须立即回滚上一生产包，不得再使用“先停后构建”流程。

Lesson learned: 2026-07-20 将低吸日报固定到 20:00，会让 15:30 盘后流水线已完成且可信的结果无谓延迟数小时。 Rule: daily workflow 中 Watch Engine 成功且当日正式快照非空、全部 `data_status=OK` 且无 `UNKNOWN` 后，必须立即执行幂等日报投递；20:00 任务只作同去重键兜底；可信门未通过时不得创建日报投递记录，修复后仍可重试。

Lesson learned: 2026-07-20 盘中监控只取通达信返回的“最新一根”时，形成中的下一根 5 分钟柱会掩盖已完成柱并造成数据误报；调度循环每分钟醒来后直接运行，还会对同一根完成柱重复请求。 Rule: 盘中监控必须按期望收盘时间精确选择已完成 5 分钟柱，每根完成柱只轮询一次；触发事件必须携带股票名称、分组和同轮完整观察列表，邮件不得只显示代码。

Lesson learned: 2026-07-21 盘后 Watch Engine 的日线 `TRIGGERED` 与盘中 5 分钟确认共用即时事件队列，会在重试计算完成后逐股连发通用模板邮件；同时 TDX 单例首次连接失败后会永久返回不可用，导致后续轮询无法自愈。 Rule: `TRIGGERED` 只有事件载荷明确标记 `source=intraday_5min` 才能即时发送，盘后确认只进入行动箱和单封日报，失败重试必须再次检查来源；缓存的 TDX 单例不可用时，下一轮必须主动重连，仍失败则保持数据阻断且禁止使用旧报价。

Lesson learned: 2026-07-22 用户所说的“两邮箱监控配置”来自 moonimprint，而不是 wx-research-hub；未经核对项目名就沿用相邻参考项目会导致错误判断。 Rule: 跨项目复制非敏感通知配置前必须先确认用户指定的源项目，并只读取、合并收件人字段；不得复制授权码、发件凭证或在日志中输出邮箱地址。

Lesson learned: 2026-07-22 盘中 LaunchAgent 只配置 09:35 日历触发时，用户在交易时段注销并重新登录会终止旧 GUI 会话中的监控，而新会话因错过启动点不会补跑；同时整五分钟零秒立即请求可能早于通达信发布完成柱，误报 `no_new_completed_bar`。 Rule: 盘中任务必须独立启用 `RunAtLoad`，启动后先检查交易日、收盘后和休市日立即退出，交易时段重登则补查上一根完成柱；常规五分钟轮询必须在边界后保留发布缓冲，并在安装或登录后验证活动 PID、完成柱时间和数据阻断数量未异常增长。

Lesson learned: 2026-07-23 通达信完成柱偶尔晚于 15 秒发布时，单只股票一次 `no_new_completed_bar` 会被立即升级为系统邮件；盘后 Watch Engine 随后又会无差别清除所有未解决 `DATA_BLOCKED`，造成“短暂误报后假恢复”。 Rule: `no_new_completed_bar` 必须连续两轮缺失才升级告警，完成柱发布缓冲不得低于 30 秒；盘中与盘后阻断必须使用独立去重前缀，并且只能由所属链路在真实成功取数后确认恢复。

Lesson learned: 2026-07-23 长驻盘中任务使用 `with sqlite3.connect(...)` 只提交事务而不会保证立即关闭连接，五分钟轮询会累积数据库、WAL 和 SHM 句柄；每 60 秒唤醒与 `caffeinate -dimsu` 还会产生无意义的后台和电源负荷。 Rule: 长驻 SQLite 读写必须用 `contextlib.closing` 或显式 `close`，休眠应直接对齐下一计划窗口；本地 LaunchAgent 仅使用 `caffeinate -i`，允许显示器和磁盘休眠，并将 BLAS、OMP、MKL、NumExpr 和 Accelerate 原生线程限制为 1，安装后必须复测 RSS、线程、CPU 和数据库句柄。

Lesson learned: 2026-07-23 功能正确不等于项目可长期运行；忽略内存、硬盘、I/O、日志缓存、句柄和唤醒频率，会让本地自动化逐渐拖慢电脑并干扰其他项目。 Rule: 后续所有功能的方案、实现和验收必须包含资源预算，至少检查峰值与稳定态内存、CPU/线程、磁盘与日志增长、数据库/WAL 体积、连接句柄、网络请求量和后台唤醒频率；优先采用有界队列、增量处理、及时释放、定期清理和低频调度，并在交付前给出实测数据，不得以用户电脑持续高负荷换取功能完成。

Lesson learned: 2026-07-27 通达信 `get_security_bars` 返回空列表时不会抛异常，旧适配器直接返回 `None` 但仍保持 `connected=True`，盘中监控会继续复用同一条退化连接并把单股临时空响应升级为 `no_new_completed_bar`；恢复又要求同轮全部观察股都有完成柱，导致事件长期悬挂。 Rule: TDX 空响应必须执行有界重连并只额外重试一次，避免增加常规请求负荷；阻断事件必须记录失败股票和失败范围，单股事件由该股票下一次成功完成柱独立恢复，全局连接事件由连接健康恢复；`None`/`NaT` 不得写成有效行情时间。

Lesson learned: 2026-07-27 仅看到 LaunchAgent 的 `EX_CONFIG(78)` 就修改 plist 不足以证明根因，macOS 用户会话可能在进程创建前全局拒绝 `posix_spawn`，此时连 `/bin/zsh` 和其他项目任务也会同时失败。 Rule: 调度安装后必须同时检查 `launchctl print`、日志更新时间和 macOS 统一日志；若系统返回 `Operation not permitted`，不得反复重启、改动其他项目或用高频守护绕过，当前交易日只允许用同等资源上限的单实例临时进程补位，并明确提示用户通过安全的注销/重启恢复用户级 launchd。

Lesson learned: 2026-07-29 盘中任务改用 `/tmp` 后能够运行，但同一安装器生成的盘后、摘要和重试任务仍把 stdout/stderr 指向 Documents，造成只有盘中进程存活、其余任务统一 `EX_CONFIG(78)`，表面上像邮件系统失效。 Rule: 所有 EvoAlpha 数据与提醒 LaunchAgent 必须统一使用 `~/Library/Logs/EvoAlpha`，安装器在写入前拒绝 Documents/项目内日志和 `WorkingDirectory`，活跃任务也必须先原子更新磁盘 plist；状态命令必须显示配置健康度与最后退出码，日志单文件启动前限制为 20MB，并以失败测试、真实 spawn、数据日期和邮件投递四层对账验收。

Lesson learned: 2026-07-29 MRGC 明确请求 7 月 28 日时，接口因当日生命周期缺失而静默回退到 7 月 24 日，掩盖了 27、28 日已有日 K 但下游计算未运行的真实空洞；调度任务继承 `APP_DEBUG=true` 还把 SQL 明细写成百兆日志。 Rule: 所有显式策略日期必须按 exact-date 语义返回，缺失时显示 `not_run` 而不得借用前一交易日；正常盘后与 retry 都必须从最近源交易日中以 `missing-only` 补齐 RPS、强度、结构、生命周期和 MRGC；前端必须保留用户所选日期；本地无人值守计算必须强制 `APP_DEBUG=false`，并以源行情、稠密因子、策略运行记录和接口返回四层日期对账验收。

Lesson learned: 2026-08-03 Portfolio 手工确认依赖浏览器原生 `crypto.randomUUID()` 时，部分运行环境会在预览后抛出未捕获异常，导致确认按钮看似无响应；交易成功后只清空表单、没有明确反馈，也会让用户误判为未记账。 Rule: 所有关键写操作的幂等请求号必须提供有界、无异常的兼容降级；确认类按钮必须捕获同步异常，并在服务端成功后显示包含业务对象和数量的明确回执；交付前需分别验证预览、确认按钮可用状态、POST 回归和正式库零测试写入。

Lesson learned: 2026-08-05 盘中 `tdx_unavailable` 阻断在行情源恢复后没有被销账，失败邮件又缺少交易日有效性门，导致 8 月 4 日的临时故障在 8 月 5 日跨日补发，用户误以为系统仍在反复阻断。 Rule: 所有盘中瞬态数据事件（连接不可用、请求失败、完成柱缺失）必须由同链路成功行情独立恢复；盘中失败投递跨交易日后必须标记 `SKIPPED`，除非当前交易时段重新复现同一根因；邮件主题与正文必须显示事件交易日和根因，交付前用“当日失败后恢复、邮件失败后次日重试、次日再次真实失败”三类回归样本验收。

Lesson learned: 2026-08-06 连续把 601899、601958 加入启用监控的自选池时，成员记录先于后台生命周期补算可见；第一个串行补算结束后运行全局 Watch Engine，读到了第二只尚未轮到补算的成员，误发 `lifecycle_missing`，35 秒后第二个补算完成又自动恢复。 Rule: 自动成员补算不得直接投递系统级即时 `DATA_BLOCKED`；全局 Watch Engine 在成员初始化链路中必须忽略本轮开始后新加入且仍在补算的成员，或采用明确的 pending/ready 两阶段可见性；只有盘后正式质量门或超过稳定等待窗口仍缺失时才允许发送系统阻断，并用“两只监控股在数秒内连续加入”的并发回归样本验收。

Lesson learned: 2026-08-06 Portfolio 卖点事件只保存 symbol，日报直接读事件表会让邮件只显示代码、账户编号和简略动作，即使本地已有股票名称和持仓上下文。 Rule: Portfolio 卖点通知必须在读取时一次性关联 `stock_info`、`portfolio_accounts` 和当前 revision 的 `portfolio_positions`，展示股票名称与代码、账户名、现价、成本/浮盈、持仓/可卖/建议数量、T+1 状态和可解释触发依据；名称缺失时安全回退代码，所有外部文本必须 HTML 转义，查询与邮件行数必须有界，并用真实持仓事件、缺失资料和恶意名称夹具做渲染回归。

Lesson learned: 2026-08-06 盘中 Portfolio 卖点邮件直接复用盘后日报列表卡片时，用户看不到 5 分钟完成柱时间、卖点线和明确执行结论，与低吸盘中提醒的信息层级不一致。 Rule: 盘中买卖提醒必须共用“状态徽标 → 完成柱时间 → 四个关键价位/指标 → 可执行行动 → 为什么现在 → 详情入口”的视觉层级；卖点必须额外展示账户、持仓、可卖数量与 T+1 状态，仅当 `executable`、可卖数量和建议数量同时有效时才使用“建议卖出”文案，不得改变原有 5 分钟信号来源、去重和手工下单边界。

Lesson learned: 2026-08-08 pytdx 连接在事务失败后仍被复用，逐股请求前的固定 600519 探针响应与下一请求串线；采集器又覆盖返回 symbol，导致 368 只股票被写成贵州茅台行情，并继续污染 RPS、板块合成、生命周期和 MRGC。 Rule: TDX 事务失败必须销毁连接后重建，正式请求前不得在同一流上发送未隔离探针；批量落库前必须检测跨股票相同 OHLCV 指纹，板块合成必须过滤异常成分股收益并按上一交易日收盘核验连续性，质量门发现克隆或量纲跳变时必须阻断下游计算。

Lesson learned: 2026-08-08 Portfolio 快照生成后，上游历史收盘价即使已修复，既有快照仍可能保留旧错价；本次 603067 的 2026-08-07 快照继续引用了串线后的 600519 收盘价 1309.22 元，而修复后的真实收盘价为 33.00 元。 Rule: Portfolio 正式快照重建与展示前必须对持仓逐日快照价格和当前 `stock_daily_prices_raw` 做全区间一致性对账；行情修复命中持仓股票时必须原子重建受影响日期及后续收益链，并验证交易日连续、逐仓零价差、账户汇总和 DAY/WEEK/MONTH/YEAR 统计一致。

Lesson learned: 2026-08-25 个股资料同步接口即使返回 HTTP 200，内部模块仍可能部分失败；默认全量刷新又没有页面回执时，用户会合理地判断按钮没有生效。 Rule: 个股资料刷新必须按当前页签提交有界模块，逐模块解析状态，并在 UI 明确显示同步中、全成功、部分失败、空响应和请求异常；交付前必须从生产页面实点单只股票并核对可见回执。

Lesson learned: 2026-08-25 在已将 `workdir` 设为 `backend/` 后仍给 pytest 传入 `backend/tests/...`，会叠加目录前缀并造成无效的“测试文件不存在”。 Rule: 执行目录内的测试前必须先对齐 `workdir` 与相对路径；从 `backend/` 运行时统一使用 `tests/...`，从仓库根目录运行时才使用 `backend/tests/...`。

Lesson learned: 2026-08-25 将两段相互重叠的 `sed` 行号区间连续输出时，边界行会显示两次，容易误判为源文件存在重复代码。 Rule: 修改代码前必须用单一、不重叠的带行号片段复核精确上下文，不得仅根据拼接输出判定重复行。

Lesson learned: 2026-08-25 前端测试使用 Node 24 内置 TypeScript stripping，仓库未安装 `tsx`；直接假设 `tsx` 存在会让测试在执行用例前失败。 Rule: 运行现有前端 Node 测试前必须先核对 `package.json` 与既有验证命令；本项目 `.ts` 单测统一使用 `node --experimental-strip-types --test`，不得为测试临时引入新运行器。

Lesson learned: 2026-08-25 即使 shell 的 `rm -rf` 只出现在 `mktemp` 目录的 `trap` 清理中，统一执行安全规则仍会在创建进程前拒绝整条命令。 Rule: 临时数据库端到端验证应使用 Python `TemporaryDirectory` 或等价的有界生命周期管理，不在 shell 命令中出现递归删除。

Lesson learned: 2026-08-25 Browser 文档的通用类型列出 `networkidle`，但当前 in-app Browser 的 `waitForLoadState` 实际不支持该状态，首次本地页面验收因此在导航后停止。 Rule: 本项目的 in-app Browser 本地页面验收统一等待 `load` 或 `domcontentloaded`，再通过目标元素 `waitFor` 验证业务就绪，不再使用 `networkidle`。

Lesson learned: 2026-08-25 调整浏览器视口并重载个股详情后，立即等待包含动态报告日期的精确文本会偶发超时，即使页面随后已正确渲染。 Rule: 前端视觉回归应先等待“业绩表现”等稳定结构锚点，再轮询动态状态或日期；不得把单次精确文本等待超时直接判定为页面失败。

Lesson learned: 2026-08-25 in-app Browser 的标签对象只负责导航、截图等标签级操作，语义定位和加载等待位于其 `playwright` 能力上；直接对标签调用 `getByText` 或 `waitForLoadState` 会失败。 Rule: 本项目浏览器验收中，标签级方法使用 `tab.goto/reload/screenshot`，DOM 定位、点击和等待统一使用 `tab.playwright.*`，调用前先核对对象原型。

Lesson learned: 2026-08-25 macOS 自带 `ps` 不支持 Linux 的 `nlwp` 输出关键字，资源验收命令因此打印了无效字段警告。 Rule: macOS 进程线程数统一用 `ps -M <pid>` 计数或 `top -stats threads`，基础 `ps -o` 只使用已验证的 `pid,ppid,state,%cpu,%mem,rss,etime,command` 字段。

Lesson learned: 2026-08-25 资源验收时把两个独立 `ps`/`curl` 命令用分号放进同一 shell 调用，虽然执行成功，但降低了输出与单项退出码的可审计性。 Rule: 后续独立诊断命令必须通过并行工具调用分别执行，不用分号串联；只有单个不可拆分的 shell 控制结构才允许内部命令分隔。

Lesson learned: 2026-08-26 板块净值与 RPS 即使计算正确，净值折线也不能替代用户明确要求的 K 线；用开收包络生成 high/low 同样不具备真实日内 OHLC 含义。 Rule: 板块主图必须使用同一有效成分集合的实际 open/high/low/close 相对前收生成明确标注的等权合成 OHLC，或使用经过质量门的供应商原始 OHLC；不得用净值线或开收包络冒充 K 线，页面必须显示来源与方法版本，RPS 固定使用本地稳定 close 主轨。

Lesson learned: 2026-08-26 仅用分块或共享 DataFrame 优化单个计算器，没有同时核算跨阶段重复读取、macOS 文件缓存、SQLite 索引写放大、备份空间和调度碰撞，可能把磁盘 I/O 问题转成常驻内存压力。 Rule: Data Core 性能方案必须先基于真实阶段耗时与查询计划建立完整数据依赖图；优先复用有版本指纹的目标日派生状态，所有历史读取必须有界，并以峰值 RSS、数据库读取量、WAL/备份增长、可用磁盘和任务重叠共同验收。

Lesson learned: 2026-09-05 Watch 失效事件只保存代码和价格时，即使同日快照已有股票名称与监控分组，即时邮件仍会退化为纯代码；失效日重新计算的止损价还可能覆盖真正触发判断的上一 setup 失效价。 Rule: Watch 状态事件必须持久化股票名称、代码、分组和实际生效的失效价；邮件发送与失败重试必须从同日快照和 `stock_info` 补齐旧事件，并从上一有效 `IN_ZONE`/`TRIGGERED` 快照恢复旧止损线，展示状态变化、关键价位、原因和下一步；所有外部文本必须 HTML 转义，补充查询必须命中日期代码主键或符号日期索引，并用旧载荷、恶意分组和止损线变化样本回归。

Lesson learned: 2026-09-14 TDX 节点失效时直接考虑跨源补数，会绕过正式前复权主链并引入数据口径风险。 Rule: mootdx/pytdx 正式主源故障时，必须先验证协议、刷新可返回日线的服务器池、修复缓存与探针并跑通主链；只有主链被证实不可恢复且用户明确同意时，才允许启用跨源修复，并须先做价格连续性对账。

Lesson learned: 2026-09-15 mootdx 同时返回 vol 和 volume 时，无条件重命名产生两个 volume 列，导致沪深前复权全部失败，而北交所成功又掩盖了采集阶段退出状态。 Rule: 所有行情入口必须先统一且校验字段别名，禁止猜测成交量单位；整市场失败必须返回失败，最终以分市场质量门和逐日下游结果验收。直连必须同时覆盖 Session 显式代理和子进程环境，保留 TLS 验证；人工历史补算应升序执行并显式禁止通知投递。

Lesson learned: 2026-09-22 日线适配器更新节点后，盘中 TdxAdapter 仍保留旧服务器池，空响应重连又从同一失效节点开始，导致全天完成柱缺失；连接类事件也未加入成功行情恢复路径。 Rule: 通达信修复必须分别验证日线与盘中实际入口；盘中正式请求不得发送共享流探针，失败必须关闭连接并有界轮换节点，连接超时和重试次数均设上限；三类瞬态阻断必须跟踪全部失败股票，仅在各股票真实完成柱与有效价格通过后恢复。

Lesson learned: 2026-09-22 用户指出固定跌幅比例过于死板，信号后10日平均涨跌幅也不能单独判断买卖提示好坏；此前据此否定参数方案，混淆了结构提示质量与固定持有期收益。 Rule: 买卖提示研究必须先明确提示目标（结构观察、入场确认、风险预警或持仓退出），按当时可见的趋势阶段、相对波动、支撑与低点结构、量价动能及后续失效路径评价及时性和有效性；固定回撤阈值与固定期限收益仅作辅助，不得单独决定信号优劣或参数取舍。

Lesson learned: 2026-09-22 Watchlist K线标记仅读取该股最新历史扫描时，扫描结束日之后的正式确认与失效会漏画；600641的扫描止于8月6日，导致8月17日正式确认不可见。 Rule: 默认图表标记须保留历史扫描版本语义，并在覆盖范围外合并正式快照首次转折，注明来源、版本和扫描覆盖日期；显式scan_id保持纯扫描，跨源衔接不得重复绘制冷却状态，低吸失效不得冒充持仓卖出。

Lesson learned: 2026-09-22 独立结构候选若以请求开始日前一年作为初始化起点，长期持有状态可能随图表窗口移动而丢失，导致同日退出标记变化。 Rule: 同一候选版本必须使用公开固定历史锚点并限制最大输入量；测试既覆盖追加未来行情不改写过去，也覆盖仅改变可视开始日期不改变同日信号。

Lesson learned: 2026-09-22 在frontend工作目录下写入frontend/tests仍重复了路径前缀，且后续命令成功掩盖首步失败。 Rule: workdir与相对路径一致性同样适用于写文件和前端命令；编辑与验证分开调用，确认编辑成功后再执行测试，不用后一步成功覆盖前一步退出错误。

Lesson learned: 2026-09-22 用户要求按现有交割单修正持仓并明确不再追溯更早流水时，反复坚持旧账现金校验会把未经核实的历史起点当成事实，阻断已经有券商凭据的核算。 Rule: 尊重用户指定的对账起点，将期初持仓、实际转账、逐笔成交、当前现金和市值分别核对；可由当前余额倒推期初现金，但必须标注为对账基准，不得伪装为入金或已实现收益；不能凭截图成本虚构历史成交，不能反复索要用户已明确不追溯的旧流水。

Lesson learned: 2026-09-22 前端生产包已预构建，但bootout后立即bootstrap仍出现短暂重新注册失败；展开候选过程表也会改变图表容器尺寸。 Rule: 已注册的本项目前端服务优先保持launchd任务注册，短暂停止进程后切换已验证生产包并kickstart，失败立即恢复上一包；可折叠区域影响图表时必须观察容器尺寸并及时resize，验收须覆盖展开与收起后的所有副图。

Lesson learned: 2026-09-23 MACD负柱收敛与DIF背离是不同证据，等右侧摆动确认会漏掉用户指定的早期反转，但仅凭事后上涨也不能证明提前买点有效。 Rule: 独立C策略必须区分柱背离与DIF背离，按当日价格止跌、辅助动能及ATR风险共振确认，保留失败退出样本及前缀不变测试；页面逐项解释必须与当前路径实际条件一致，不得沿用其他路径的等待文案。

Lesson learned: 2026-09-23 C 策略与盘后监控上线后，如果图表仍默认开启 A/B、关闭 C，用户进入页面时仍看不到当前重点策略，误以为监控没有接通。 Rule: Watchlist 图表默认显示 C 的观察、买入、风险和卖出/失效标记，默认隐藏 A/B；盘后 C 监控必须与所选日期、分组和股票联动，并在页面重新聚焦或手动刷新时更新，不得额外增加常驻轮询。

Lesson learned: 2026-09-23 Watchlist 默认视口高度约827 CSS像素时，上方行动箱和底部买点/生命周期面板占用固定高度，主图 flex 剩余空间仅94像素，实际K线及副图接近不可读；此前只在1100像素高窗口验收未发现。 Rule: 图表必须保留可读最小高度；高度不足时仅让中间内容列纵向滚动，不能裁掉图表或下方面板；交付前至少用用户截图对应逻辑尺寸与较大窗口分别测量画布高度、滚动可达性和运行时错误。

Lesson learned: 2026-09-23 Watchlist右侧资料面板在1476×827逻辑视口高约410像素，但固定展开的10项行情卡占270像素，导致财务与作战检查的滚动可视区仅64像素；只修主图高度仍留下资料面板挤压。 Rule: 小窗Watchlist资料面板默认只展示四项核心行情，六项次要行情必须可明确展开且数值不能丢；保留资料正文独立滚动，完整个股详情页保持全量行情；交付前分别测量默认/展开资料区高度并验收两种页面。

Lesson learned: 2026-09-23 通达信连接退化后，688 股票请求与返回行情发生跨代码串线；单日行内 OHLC 合法且同日无完全克隆时，原质量门仍放行，错误行情又污染估值、RPS、板块、Watch 和 MRGC 预选。 Rule: K 线落库前必须核对源端重叠原始收盘价与本地同代码历史锚点，连接事务失败须销毁底层流；盘后质量门必须核对跨日收盘涨幅与记录涨幅，异常时阻断下游；修复须同时核验历史回写与所有依赖表，并在重算自动筛选前保存受影响行。

Lesson learned: 2026-09-27 仅用工作日推断交易日，把 9 月 25 日中秋休市误判为缺失行情，触发无效下载、mootdx 无数据告警和失败流水线。 Rule: 自动调度、支撑行情采集与盘中监控必须共用官方休市日规则；休市日不得备份或下载当日行情，显式休市目标必须在写库前拒绝；报告必须区分休市与真实数据源故障，并在年度交易所休市安排发布后更新离线日历。

Lesson learned: 2026-10-01 仅检查最新日期会把 143 条局部行情当成整日完成，历史因子的覆盖率阈值又掩盖了 9 条已有日 K 的结构计算缺口。 Rule: 下载跳过必须核对目标日三市场覆盖与已接受的源端例外；补数验收必须在最近 7 个交易日按日期和股票逐键对账源表与稠密计算表，历史行情补入后应有界刷新受影响日期及后续板块净值、RPS、结构、生命周期和 MRGC，不得用总体覆盖率代替逐股完整性，也不得为此重跑全历史。

Lesson learned: 2026-10-01 资料面板压缩行情时只保留价格和交易指标，把用户常用的市值与 PE 隐藏到了展开区。 Rule: 行情摘要默认必须同时展示价格、涨跌幅、换手率、成交额、总/流通市值与 PE(TTM)，以紧凑两行布局保留业绩展示空间。

Lesson learned: 2026-10-01 预构建前的备份命令再次叠加 frontend 路径，且构建继续执行掩盖了首步失败。 Rule: 预构建备份必须作为独立成功步骤执行，禁止将备份与构建串在同一 shell 命令中；目录内操作使用相对路径前先核对工作目录。

Lesson learned: 2026-10-03 UI 预构建早于全部布局文件保存时，可能出现组件已更新而新增尺寸样式尚未进入生产包，权益图容器高度为零。 Rule: 所有编辑完成后才能开始最终预构建；上线验收必须测量真实逻辑视口与图表容器尺寸，不能仅凭构建和源码测试通过判断布局成功。

Lesson learned: 2026-10-03 移动嵌套 Task 配置时按首个 `),` 截取会误把 command 元组结束当成整个任务结束，造成括号不匹配。 Rule: 任务配置重排必须以完整任务块边界编辑，修改后先验证语法，再运行顺序与分支回归测试。

Lesson learned: 2026-10-03 在 frontend 工作目录执行编辑脚本时再次添加 frontend 前缀，会使编辑失败。 Rule: 所有文件编辑脚本与验证命令都必须先对齐工作目录，优先使用明确的绝对路径或当前目录相对路径。

Lesson learned: 2026-10-03 Portfolio 只收紧间距却保留重复账户身份行、纵向收益卡和多层图表标题，仍把持仓推到首屏下方，没有满足用户同时查看盈亏、曲线与持仓的目标。 Rule: Portfolio 首屏验收必须在截图对应逻辑视口和 1366×768 下实测资产/盈亏、完整曲线及持仓表头与首批记录同屏可见；先合并重复信息层级，再分配图表与表格高度，不能只凭缩小卡片或图表判断完成。

Lesson learned: 2026-10-03 策略页固定扣减内容区高度未计入完整工具栏和留白，默认窗口仍有裁切。 Rule: 单屏页面应由有界 flex 容器分配剩余高度，工具栏禁止收缩，并验证默认缩放下首尾边界与面板内部滚动。

Lesson learned: 2026-10-03 策略股池表格总宽大于各列宽度之和且固定列偏移不一致，导致默认涨跌幅裁切和价格被遮挡。 Rule: 股池默认分栏至少容纳代码、名称、行业、价格和涨跌幅及滚动条；固定列偏移必须等于前序列宽之和，表格总宽必须与列宽总和一致。

Lesson learned: 2026-10-03 策略与自选页对同一个详情入口使用了不同文案。 Rule: 相同个股详情入口统一使用“个股详情”，包括按钮、title 和无障碍标签。

Lesson learned: 2026-10-03 验收脚本用 SQLite Row 对象的 repr 计算摘要会因对象地址变化误报账本变化，且账户结果与整体结果的事件字段不同。 Rule: 数据一致性摘要必须基于稳定排序的真实字段值；运行正式验收前须核对返回类型，整体 ExitRunResult 使用 created_event_count，账户结果才使用 created_event_ids。

Lesson learned: 2026-10-03 系统 python3 未安装项目 pytest，且测试路径必须与执行目录一致。 Rule: 测试前先确认项目运行时和 cwd；本机后端使用已验证的 /opt/anaconda3/bin/python3，从仓库根目录运行 backend/tests/...，不得误用系统 Python。

Lesson learned: 2026-10-03 智能分组若用首次数据日作为通知基线、或在历史补算中重写当前成员，会补发启用前信号并倒退当前窗口。 Rule: 自动 MRGC 分组必须按完整20个交易日维护，首次通知基线不得早于上海实际启用日；历史补算不得倒退分组，超限/缺数保留旧成员，邮件按原始事件数校验容量且过期失败日报退出重试队列。

Lesson learned: 2026-10-03 日报直接将全部 HOLDING 作为既有持有，会把当日 BUY_CONFIRM 重复算入旧持有；页面与邮件若分别拼接结论也会产生口径差异。 Rule: MRGC日报必须共享同一交易日事实报告，既有模拟持有排除当日新增确认，核心行情/C信号缺失与可选背景指标缺失分级处理；未配置模型必须明示规则版，禁止伪装AI输出或自动启用邮件。

Lesson learned: 2026-10-03 日报用固定右侧浮层遮住图表，整张个股卡片点击又自动筛选主列表，导致阅读与行情核对互相干扰。 Rule: Watchlist 助手必须占据独立布局位置，窄屏使用行情/分析视图切换；默认用快捷提问进入，股票选择与整组筛选分开，规则问答不得伪装为模型对话，切换股票不得自动发起模型请求。

Lesson learned: 2026-10-03 AI助手入口仅允许Watchlist原地打开，会把其它个股详情导航回自选页并丢失查看上下文。 Rule: 每个详情宿主必须显式传入当前股票、日期和原页选股回调，助手不得为了分析跳回Watchlist；无列表的详情隐藏整组筛选动作，显式历史日期不得静默回退。

Lesson learned: 2026-10-03 资料面板加宿主包装层后，包装层未建立有界flex高度链，内部overflow-y-auto随内容撑到视口外，无法滚动。 Rule: 包装可滚动面板时必须逐层传递min-h-0与剩余高度约束，面板作为flex子项占据剩余空间；验收必须实际滚轮滚动并检查scrollHeight大于clientHeight、面板底边在视口内，以及AI切回资料后的滚动。

Lesson learned: 2026-10-03 lru_cache 的线程安全不保证冷启动只执行一次初始化，分钟行情服务曾可被并发构造多个实例，突破原定的连接与缓存预算。 Rule: 进程共享的资源信号量与有界缓存必须用显式初始化锁建立单例，并以多线程冷启动回归验证只创建一个实例；不得把缓存装饰器等同于单次初始化保证。

Lesson learned: 2026-10-03 新增板块分钟图入口遗漏既有直连约定，requests.Session 继承失效本地代理，导致 120 分钟行情在生产接口中返回不可用。 Rule: 新增只读行情接口也必须复用既有直连设置，显式关闭 Session 环境代理并清空显式代理，保留 TLS 校验；以故障代理夹具和真实接口各验证一次，不得因为相邻采集器已处理代理而假设新入口自动继承。

Lesson learned: 2026-10-03 缠论线段端点经过浮点插值后再以 C2=C1 判定极点，会因舍入误差漏掉中枢；仅验收线段可见，也不能证明矩形条件和图形语义正确。 Rule: DRAWLINE 起终点必须直接保留原始价，并用小数端点失败回归、四类中枢成立及未成立手算夹具和真实页面矩形验收闭环；截图中出现而源码未定义的历史框或买卖文字必须标为可选扩展，不得冒充严格公式输出。

Lesson learned: 2026-10-03 临时 TypeScript 测试加载器使用默认 ES5 转译时，Map 迭代器展开被静默转成空数组，曾造成真实历史框误判为零。 Rule: 动态转译测试必须显式对齐项目与运行时的 target，并用非空 Map/Set 输出断言核验迭代行为；不得把加载器造成的空结果归因到生产公式。

Lesson learned: 2026-10-03 个股助手选股使用动态股票路由切换时，详情宿主随page重挂载，图表指标/周期及助手回答/草稿一并回到默认。 Rule: 连续个股浏览的详情宿主必须位于稳定共享layout中，换股只更新股票参数，不以symbol作为组件key；回归须验证指标、周期、回答和输入状态跨连续换股保持，同时URL和行情准确更新。

Lesson learned: 2026-10-03 用户指出中枢普遍不显示时，先索要单只股票容易把排查收窄为局部样本解释。 Rule: 指标通用显示问题必须先以独立手算夹具、多形态行情及缩放裁剪测试排查计算到渲染全链路，不得以单股条件未满足代替验证，也不得按单股放宽公式；具体股票仅作为补充复现。

Lesson learned: 2026-10-03 ECharts dataZoom 默认 filter 会因中枢起点在窗口外而过滤整条矩形数据，即使区间横跨可见窗口；只检查全窗截图和计算框数会漏掉这一问题。 Rule: 区间型图形必须使用 weakFilter 保留跨窗区间，并独立测试左跨界、右跨界、覆盖窗口和完全不可见四种情况；验收同时核对计算数据与实际图形节点，不能以条件未满足解释渲染缺失。

Lesson learned: 2026-10-03 均线开关的浏览器验收用 MA 前缀匹配时误把 MACD 算成均线，导致测试假失败。 Rule: 图表指标分类必须使用精确名称或带数字的 MA 模式，不能用宽泛字符串前缀；均线开关验收必须确认 MACD/KDJ 和缠论结构保持不变。

Lesson learned: 2026-10-03 新增自选股/MRGC 自动资料下载只接入未来触发，遗漏已有 MRGC 结果；单股样本成功不能证明当前列表完整。 Rule: 自动补资料功能上线必须补齐当前自选股及最新 MRGC 结果，并在每日资料任务中重新发现该集合；验收必须逐股对账最新报告期、真实营收/净利与队列状态，不得只验证单股；已完成策略日期被 missing-only 跳过时也不得漏掉资料补齐。

Lesson learned: 2026-10-03 知乎公式条件已对应，但统一线宽、近似色、简化标签和额外三角形仍让图形不符合源码；验收还曾要求已被缩放隐藏的历史文字出现在当前窗口。 Rule: 通达信复现必须逐输出记录颜色、线型、线宽、文字与图标语义，分别验证计算和真实图形；文字断言应按当前可见区间核对，不得把样式近似或未取得的初始化真值宣称完全一致。

Lesson learned: 2026-10-03 资料子进程仅传 LOCAL_DB_PATH 时，配置类的 LOCAL_DATABASE_URL 默认值仍可能指向正式库，导致临时数据库测试错误访问正式资料队列。 Rule: 所有独立写库子进程必须同时显式传入数据库路径和数据库 URL，并验收实际打开的数据库与锁文件一致；API后台任务测试必须 mock worker 启动，不得调用真实行情源。

Lesson learned: 2026-10-03 自动业绩仅补两年而页面默认展示六年，最新中报覆盖率达标仍会掩盖历史柱形图缺口；旧成功缓存还会阻止完整补齐。 Rule: 自动财务下载范围必须与业绩图默认六年范围一致，下载范围变化必须版本化并使旧成功缓存失效；验收须包含用户指定股票的完整年度/单季序列和页面实际使用接口，不得用最新一期非空替代全图完整性。

Lesson learned: 2026-10-03 日报只看 MRGC 20日会漏掉公司组和自选股；将含 ZIG 未来函数的缠论回绘位置直接当确认日会制造历史提前信号。 Rule: 日报问答、完整报告与邮件预览必须共用三组去重事实和明确的当前/历史成员口径；来源交集与策略共振分开展示，缠论候选按当日截断行情记录首次可见日期并保留回绘提示，C 风险/失效独立保留；跨组联动使用只读报告列表，不冒充 MRGC 成员。

Lesson learned: 2026-10-03 后台资料已落库并不意味着已打开的业绩图会更新；五分钟 staleTime 和关闭窗口聚焦刷新会保留下载前空柱。 Rule: 业绩页签重新进入时必须重新取数，窗口重新聚焦时应检查过期数据；不采用无限轮询；验收同时对账数据库、前端代理 API 和真实累计/单季柱形数据，浏览器不可用时必须明示未完成视觉验收。

Lesson learned: 2026-10-03 多周期前端测试追加和修正命令在 frontend 工作目录内仍重复写 frontend 前缀，导致写入失败但后续旧测试通过。 Rule: 测试与夹具写入命令必须按当前 workdir 使用相对路径或绝对路径；写入失败时不得把随后旧用例通过当作新增回归已执行，必须核对新增测试名称和总数。

Lesson learned: 2026-10-03 Watchlist 的 AI 页签、分组、筛选和问答使用组件局部状态，进入个股详情和返回时重挂载会恢复默认。 Rule: 跨 Watchlist/个股详情共享的 AI 会话状态必须保存在有界会话状态中；同交易日路由切换或关闭重开不得重置，仅交易日变化时清理旧问答与日期相关筛选。

Lesson learned: 2026-10-03 风华高科9月29日的缠论“类2买”在加入9月30日行情后消失；固定相同起点仍可复现，证明原式重绘。260根滚动窗口起点移动还会改变更早标签。 Rule: 原式缠论买卖文字必须明确标示可撤销的回绘性质；历史图上的极点日期不得冒充当时可知的触发日期。验证信号必须逐柱比较首次可见与后续撤销，并将未来函数重绘与窗口初始化影响分别审计；不得通过冻结撤销信号来伪造已确认信号。

Lesson learned: 2026-10-03 历史 AI 日报的 MRGC 近20日票池随目标交易日变化；仅补齐9月29日或复用当前自动池无法保证其它历史日期正确，当前快照缺失还会连带误报缠论行情不足。 Rule: 历史日报必须以目标日截至当日的20个交易日MRGC命中集合为唯一范围，成员解析由日报与历史计算共用且禁止读取未来命中；历史补算不得改变当前池、正式事件或邮件队列，缓存必须绑定日期、成员、规则与数据来源；C与缠论质量独立判断，缺失项按股票及根因去重，并验证相邻历史日期成员变化。

Lesson learned: 2026-10-03 主图显示模式新增后遗漏 ECharts useMemo 依赖会让标签切换使用旧配置；React Compiler/ESLint 可检出。 Rule: 新增图表显示状态必须同步加入生成 option 的依赖，并实际切换模式核对画布标签；检查命令须在 frontend 工作目录运行以读取项目 ESLint 配置。

Lesson learned: 2026-10-03 Codex CLI 的 JSONL `error` item可能是配置诊断，把所有未知item当工具会在真正生成前误杀请求；详细的30行业与20股票解释又可能超过单次90秒预算。 Rule: CLI事件审计必须区分诊断与工具执行，禁止打印原始诊断或凭证；日报模型输入与输出都要有界，重点行业/个股采用简短解释、未覆盖项明确留空，并以一次真实生成、缓存复读、超时回收和三视图一致性验收。

Lesson learned: 2026-10-03 ECharts 的 emptyCircle 会使用默认白色圆心，散点入场动画也会让初次悬停偏离最终坐标；只核对 option 无法证明用户看到真正空心圆和可用 tooltip。 Rule: 撤销记录使用普通 circle 加透明填充和显式描边，并关闭入场动画；交付必须检查实际画布和真实鼠标悬停，不能只调用 formatter 判定可用。

Lesson learned: 2026-10-03 历史 C 回放除买入、退出、失效、风险外还会生成 OBSERVE 标记，直接交给日报标签映射会在真实103股范围内抛 KeyError，单股买点夹具不足以覆盖。 Rule: 历史日报事件进入汇总前必须采用明确的事件类型白名单，观察标记保留为状态而不冒充交易事件；验收须包含真实多股历史日期、未确认观察日、重复存档读取以及正式票池和通知记录零变化。

Lesson learned: 2026-10-03 将缠论版本、文字、撤销圈、扩展中枢与策略信号筛选全部平铺在主图工具栏，会挤占图表并让用户难以识别常用开关。 Rule: 指标主工具栏只保留周期、样式和常用启停；次要参数及说明收进对应指标设置，明确共享筛选语义；设置须支持外部点击/Esc关闭且保留已选值。


Lesson learned: 2026-10-03 新日报复用 notification_deliveries 时，旧重试器会将 event_id 为空的独立日报误判为失效事件。 Rule: 新增投递类型必须明确所属重试链路；旧 dispatcher 排除该类型，新链路冻结首次发送正文和事实版本，SMTP 结果不确定必须标 UNKNOWN，不得盲目自动重发。


Lesson learned: 2026-10-03 在 backend 工作目录下仍给脚本路径加 backend 前缀，会让备份读取失败；将备份和后续写入放在同一 shell 又可能在失败后继续。 Rule: 保护性备份使用绝对路径并单独执行，明确验证退出码与产物后才调用依赖写操作；不可依赖多行 shell 自动遇错停止。

Lesson learned: 2026-10-03 图表内设置弹层固定左对齐或右对齐，在不同工具栏宽度下仍可能被 overflow 边界裁切。 Rule: 设置弹层展开时必须按可视区域和所有裁剪祖先约束宽度与位置；验收同时检查缠论与 C 设置的完整边界，不只检查控件是否存在。

Lesson learned: 2026-10-03 分钟图使用两行日期/时间刻度而底部仅预留百分比空间，导致标签密集并与缩放条遮挡；多副图十字轴又重复显示同一时间。 Rule: 个股共享图表的时间轴必须使用单行自适应刻度和标签避让，按固定像素为日期与缩放条分隔留白，滑块隐藏重复日期，悬停仅保留一处完整时间；验收覆盖分钟线、日线及不同图表尺寸。

Lesson learned: 2026-10-03 AI 日报按历史日期取 MRGC 20日成员，但左侧分组接口仍读取当前 stock_pool_members，只替换行情日期，导致9月29日日报68只而分组69只。 Rule: 历史日期验收必须同时对账分组下拉数量、成员列表、日报和20日窗口的股票集合；智能MRGC分组元数据与成员接口必须共用目标日只读解析器，前端查询key和参数都包含日期，不得只验证日报接口后宣布日期口径一致。

Lesson learned: 2026-10-03 前端测试从仓库根目录执行时漏写 frontend 前缀，导致测试未启动。 Rule: 前后端测试都须先核对执行目录与测试路径，前端 tests/... 统一从 frontend/ 执行。

Lesson learned: 2026-10-03 混合编辑与前端验证时，相对路径可能随 workdir 改变而重复项目目录。 Rule: 跨目录批量编辑使用仓库绝对路径；测试和构建单独在 frontend 工作目录执行，调用前复核路径。

Lesson learned: 2026-10-03 为完整显示业绩图直接压缩图表高度，虽露出年份轴却降低柱线可读性。 Rule: 业绩面板优先合并标题元信息、用紧凑等宽指标排布释放空间，保留图表可读的最小高度；矮窗口允许小幅滚动，不以过度缩图换取完整显示。

Lesson learned: 2026-10-03 浏览器验收等待表达式返回 DOM 对象会触发 CDP 序列化错误，Watchlist 日期也不能假设由 URL 参数驱动。 Rule: UI 等待条件必须返回布尔值；日期隔离验收应操作真实日期控件，并等待分组与来源加载完成再断言。

Lesson learned: 2026-10-03 将当日提示与历史提醒放在 Watchlist 顶部，即使默认折叠，展开后仍会挤压 K 线与个股资料空间。 Rule: Watchlist 提示和提醒处理应放在右侧“个股资料 / AI 助手 / 当日提示”页签内独立滚动，选股后保持提示页签，不能让展开提醒改变主图高度。

Lesson learned: 2026-10-03 用户希望业绩指标控件放在图表下方，上方仅保留标题信息。 Rule: 业绩面板按“标题、图表、两行指标”排布，默认选中归母净利，后续布局优化保持这一顺序。

Lesson learned: 2026-10-03 布局回归测试把真实“业绩”页签假设成“财务”，且共用 CDP 端口可能被另一轮测试改变窗口尺寸。 Rule: 浏览器断言使用实际可见控件文本；每轮独立 UI 验收使用独立 CDP 端口和临时浏览器目录，确保窗口尺寸、选股及日期状态不被其它任务干扰。

Lesson learned: 2026-10-04 Watchlist 与个股详情若各自维护侧栏局部状态，路由往返会重置提示范围、资料页签、财务指标、展开和滚动位置；详情换股也可能返回旧股票。 Rule: 两个入口必须复用同一提示组件、query key及有界会话UI状态；分组与日期明确隔离，选股同步全局上下文，异步内容就绪后恢复滚动，导航验收必须实点详情与返回而不是全页重载。

Lesson learned: 2026-10-04 从仓库根目录执行 npm build 会因前端 package.json 位于 frontend 而失败。 Rule: npm 构建与检查前必须确认 package.json 所属目录，并将 workdir 明确设为 frontend，不能沿用上一步编辑命令的仓库根目录。

Lesson learned: 2026-10-04 AI侧栏恢复滚动后，晚到的共享提示会触发浏览器滚动锚定，让位置再次偏移。 Rule: 异步侧栏必须等待最终内容就绪再恢复滚动，并禁用该滚动容器的自动锚定；往返验收必须核对实际非零滚动位置，不得通过放宽容差掩盖偏移。

Lesson learned: 2026-10-04 当日提示一味压缩会隐藏买点依据，C确认与近期ZIG缠论候选又可能被误当成同日确认共振。 Rule: 买点卡片必须保留真实依据、关键价位与时间，其他背景折叠；共振只接受同日同向已确认且有效的独立C与缠论信号，候选与回绘须显式标注，不得把低吸A/B混作C，AI联动不得覆盖已有草稿。

Lesson learned: 2026-10-04 提示数量放在details summary中会让用户误以为是分类按钮，点击反而收起整个列表；跟随左侧分组也会遗漏Watchlist其他股票机会。 Rule: 当日提示固定使用Watchlist真实分组去重并集，与左侧分组隔离；分类必须是真正可筛选的按钮并明确显示选中状态，分类计数与可见卡片一致，默认买点优先，验收必须逐分类点击并切换左侧分组验证范围不变。

Lesson learned: 2026-10-04 将提示改成全Watchlist范围后，旧的组内选股自动回退仍会覆盖从全局提示点选的股票。 Rule: 全局提示或AI联动选股不得被左侧当前分组成员约束覆盖；验收必须点击一个不属于当前左侧分组的真实机会，并核对图表与AI保持同一股票。

Lesson learned: 2026-10-04 当日提示卡片默认展开两套完整依据仍过于冗长，低吸输入顺序会遮住C和缠论重点。 Rule: 筛选结果连续编号，按明确来源优先级降序；默认只显示标签、关键价位和一句依据，完整条件放入更多依据与AI，候选优先关注不得写成确认共振。

Lesson learned: 2026-10-04 资料、AI和提示各自保留外框与标题样式，详情页又使用比例侧栏，导致切换入口后宽度和视觉层级不一致。 Rule: 三页签必须复用统一侧栏外框、选中态、标题栏高度和内容间距；Watchlist与详情使用同一响应式宽度规则，验收需在至少两种屏宽逐页签测量实际宽度、标题高度与横向溢出。

Lesson learned: 2026-10-04 资料面板内的隐藏按钮只隐藏内容而保留整个侧栏宽度，用户期望的是统一收起右侧三页签。 Rule: 侧栏收起按钮必须位于共用页签栏并控制整体布局，收起后释放图表空间且保留展开入口，折叠不应清空页签、内容或滚动状态，两入口共用行为。

Lesson learned: 2026-10-04 当日提示选股没有高亮回执，底部操作行又重复占用卡片空间。 Rule: 提示卡片以当前全局股票显示唯一选中高亮，标题、代码与看图/AI操作共用首行，长名称截断但代码和操作保持可见，两入口联动后都要核对高亮一致。

Lesson learned: 2026-10-04 全部提示总数与首个买入分组数量缺少明确层级，容易被理解成筛选结果不一致。 Rule: 全部筛选必须明确显示总条数与优先顺序，分组标题使用与筛选相同的名称并显示本组条数。

Lesson learned: 2026-10-04 用户所指重复是全部结果内再次分分类板块，而非总数文案。 Rule: 全部提示只用连续排序列表，分类仅作为筛选入口，每条用类型标签说明归属，不再重复分类标题。

Lesson learned: 2026-10-04 样式扫描范围写在 CSS 相对 source 中会遗漏布局类，而默认全目录扫描会触发 WASI 内存分配失败。 Rule: 本项目前端 PostCSS 扫描 base 明确限制为 process.cwd()/src；生产包必须截图核对侧栏宽度和布局，不得只凭类型与筛选测试部署。

Lesson learned: 2026-10-04 提示跳转 AI 后缺少返回原卡片入口，用户需要重选页签和重新翻找。 Rule: 个股 AI 面板提供固定返回提示按钮及股票上下文，进入 AI 前保存原滚动位置；返回必须保持筛选、展开状态和当前股票，并同时验收 Watchlist 与个股详情页。

Lesson learned: 2026-10-04 AI 助手重复当日提示的全局数量和信号价位，会掩盖解释内容。 Rule: 当日提示负责机会事实，AI 默认只解释信号关系与核对条件，日报背景保留行业信息；图表复用同股票同日期的有界提示价位，确认与候选严格区分，邮件预览不随展示去重改变。

Lesson learned: 2026-10-04 图内来源和确认日期长标签会挤压K线，右轴标签还必须兼容图表百分比grid及异步初始化。 Rule: 三处股票图共用右轴数字标签与有界避让，详细含义仅悬停展示，确认点不常驻长文字；按chart ready、缩放与resize投影，并测试百分比布局与切回日K。

Lesson learned: 2026-10-04 点击个股 AI 解读却先显示通用规则，会让用户找不到该股票的详细解释。 Rule: AI 解读直接展示同日期同股票的详细分析卡片，通用口径折叠；缺少存档时仅展示真实提示依据，换股不得沿用上一只股票分析。

Lesson learned: 2026-10-04 AI 问答沿用三组存档筛选，与全部 Watchlist 当日提示的范围和分类不一致。 Rule: 机会问答复用同日期提示列表，结果可定位提示并返回原回答；行业背景明确存档口径，不得把存档独有股票当作当日机会。

Lesson learned: 2026-10-06 磁盘巡检发现前端回滚包与浏览器验收目录长期累积，进程命令行还可能携带凭证。 Rule: 前端只保留当前生产包与最近两份可靠回滚包；临时验收浏览器必须在退出后清理；瘦身只处理白名单生成物，删除前检查活动进程、路径和时间，保留数据库与唯一备份并实测释放空间；进程排查优先使用 PID 与进程名，不得原样输出带环境变量或密钥的完整命令行。

Lesson learned: 2026-10-04 Strategy 独立侧栏与 Watchlist 逐渐分叉，详情页仅依赖浏览器 back 无法保证返回来源页。 Rule: Strategy 复用完整个股侧栏及提示上下文，详情链接携带白名单来源并在换股时保留；验收两页往返、日期、选股及侧栏状态。
