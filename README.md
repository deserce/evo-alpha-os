# EvoAlpha OS

> 进化即自由（Evolution is Freedom）

EvoAlpha OS 是面向 A 股的本地数据与量化研究工作台：采集和校验行情，计算强弱、结构与生命周期，跟踪自选股机会，并管理手工交易账本与持仓风险。

当前主线是 **Next.js 前端 + FastAPI API + SQLite + 本地无大模型调度**。云端数据库、R2 同步与 AI 接口仍保留在代码中，其配置和可用性需单独验证。交易由用户手工执行。

最后更新：2026-10-20。

## 当前能力

| 模块 | 当前实现 |
| --- | --- |
| 市场与板块 | 指数、成交与市场情绪、涨停梯队、板块强度及成分股分析 |
| 个股工作台 | 日/周/月线及 30/120 分钟图、量价与指标、信号标记、财务和 F10 资料 |
| 量化策略 | 个股/板块/ETF RPS、强度、结构健康、生命周期、时机与风险计划、MRGC |
| Watchlist | 自选分组、规则配置、历史遍历、当日状态、行动箱和已完成 5 分钟柱监控 |
| Portfolio | 账户与交易记账、截图导入草稿、持仓及可卖数量、收益快照、卖点计划和提醒 |
| 日报与通知 | 策略日报、低吸日报、盘中提醒、邮件去重与失败重试 |
| Data Core | 增量采集、单写入锁、质量门、缺失计算补齐、历史健康检查与备份保留 |

AI 多代理会诊、完整回测平台、商业订阅和多市场扩展不作为当前已交付能力。页面与 API 的存在也不代表外部数据源始终可用。

## 数据与计算链路

```text
TDX / 东方财富 / AKShare 等数据源
                  ↓
       Data Core：采集、来源管理、质量门
                  ↓
          SQLite 本地行情与资料
                  ↓
     RPS → 强度 / 结构 / 生命周期 → 策略
                  ↓
       Watch Engine / Portfolio / 日报
                  ↓
          FastAPI → Next.js 工作台
```

- 沪深正式股票日 K 使用 TDX mootdx/pytdx 协议，并在本地计算前复权；原始价与复权价按用途分别使用。
- 北交所正式日 K 使用 TDX 标准行情 `market=2`，当前按未复权口径写入；扩展行情源用于诊断。
- 估值、财务、新闻及支撑行情按模块使用补充源与降级逻辑。正式写入权限以 [来源注册表](backend/data_core/source_registry.py) 为准。
- 日 K 质量门失败时阻断下游计算；支撑数据的失败与正式行情阻断分开处理。
- 盘中确认使用已完成的 5 分钟柱。历史扫描状态、正式待处理事件与邮件投递分别管理。

## 技术栈与目录

- 前端：Next.js 16、React 19、TypeScript、Tailwind CSS 4、TanStack Query、Zustand、ECharts/Recharts。
- 后端：FastAPI、Python、SQLAlchemy/SQLite、Pandas/NumPy、APScheduler。
- 本地调度：macOS LaunchAgent；正式 Data Core 执行器无需模型 API。

```text
backend/
├── main.py                  # FastAPI 入口
├── app/api/v1/              # 市场、个股、量化、自选与持仓 API
├── app/notifications/       # 通知支持
├── data_core/               # 来源、schema、交易日历和质量门
├── data_job/                # 数据采集与行情适配器
├── quant_engine/            # 因子、策略、研究与 Watch Engine
├── portfolio/               # 账本、快照与持仓退出逻辑
├── scripts/                 # 正式执行器、安装器、迁移与修复工具
└── tests/                   # 后端回归
frontend/
├── src/app/a/               # 工作台路由
├── src/components/          # 页面、图表和业务组件
├── src/lib/                 # API、状态与展示逻辑
└── tests/                   # Node 测试
```

## 本地启动

以下命令以仓库根目录为起点。已有本地环境时优先使用已验证的 Python 环境；Python 依赖包含固定版本，新环境安装仍需验证。前端测试使用支持 TypeScript stripping 的 Node.js 24。

### 后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# 正式 TDX 采集还需这些未列入主依赖清单的包
python -m pip install mootdx pytdx tqdm
```

根据需要安装 EFinance 等补充源依赖。`data_job/requirements.txt` 含有标准库 `sqlite3` 条目，不应直接作为完整安装清单。

默认本地数据库为 `backend/data/local_quant.db`，数据库和行情数据不随 Git 仓库发布。环境配置在后端工作目录的 `.env` 中读取，可参考根目录 [.env.example](.env.example) 和邮件模板 [backend/.env.example](backend/.env.example)，按需合并字段；已有 `.env` 时不要覆盖。默认无需配置云端数据库。

新库需先初始化基础表，再按所需模块应用迁移；以下命令会写数据库，已有库应先备份：

```bash
# 在 backend/ 中
mkdir -p data
python -m data_job.scripts.init_database
python scripts/migrate_data_core_schema.py
python scripts/migrate_portfolio_schema.py
```

建表不等于完成数据初始化。首次历史行情、股票资料、因子和股票池准备需单独执行；日常增量运行入口不能替代完整历史回填。迁移与存量数据口径参见 [Data Core 文档](backend/docs/DATA_CORE_SOURCE_AND_DB_MAP.md)。

```bash
# 在 backend/ 中启动 API；端口占用时选择空闲端口
APP_DEBUG=false OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

健康检查：`http://127.0.0.1:8000/health`；API 文档：`http://127.0.0.1:8000/api/docs`。

### 前端

```bash
cd frontend
npm ci
BACKEND_URL=http://127.0.0.1:8000 npm run build
BACKEND_URL=http://127.0.0.1:8000 npm start -- --port 3000
```

浏览器打开 `http://127.0.0.1:3000`，默认进入 `/a/quant`。`/api/*` 经 Next.js 代理至 `BACKEND_URL`，未设置时默认后端为 `http://127.0.0.1:8000`。自定义端口后，构建与启动时保持代理配置一致。

修改代码期间可运行 `npm run dev`。已有生产实例在线时，使用 `NEXT_DIST_DIR=.next-candidate-<版本>` 在独立目录预构建，验收后再切换并保留回滚包；不要在运行中的 `.next` 目录直接重建。

### 常用页面

| 路由 | 用途 |
| --- | --- |
| `/a/market` | 市场概览 |
| `/a/sector` | 板块分析 |
| `/a/stock`、`/a/stock/[symbol]` | 个股工作台与详情 |
| `/a/quant`、`/a/strategy` | 量化与策略 |
| `/a/watchlist` | 自选监控与行动箱 |
| `/a/portfolio` | 账户、持仓和收益 |
| `/a/report`、`/a/research` | 日报与研究 |
| `/a/data` | 数据管理 |

## 数据更新与调度

正式写库优先使用本地执行器。以下命令在 `backend/` 中运行：

```bash
# 预览日常执行计划
python scripts/run_local_data_skill.py --mode daily --dry-run

# 执行日常更新，或重试缺失任务
python scripts/run_local_data_skill.py --mode daily
python scripts/run_local_data_skill.py --mode retry

# 健康检查：生成状态报告；深度模式检查历史区间与保留策略
python scripts/run_local_data_skill.py --mode health
python scripts/run_local_data_skill.py --mode health --deep-health

# macOS：查看安装计划、安装任务、检查状态
python scripts/install_local_data_launchd.py install --dry-run
python scripts/install_local_data_launchd.py install
python scripts/install_local_data_launchd.py status
```

安装器当前默认时间如下。LaunchAgent 日历触发跟随 macOS 系统时区，使用时应将系统时区设为 `Asia/Shanghai`；实际执行还受交易日与时段检查约束：

| 时间 | 任务 |
| --- | --- |
| 工作日 09:00 | 有时间预算的近期缺失任务补齐 |
| 工作日 09:30 | 轻量健康检查 |
| 工作日 09:35 | 盘中监控；登录时也会检查是否需要补启动 |
| 工作日 15:30 | 盘后 Data Core 主流程 |
| 工作日 16:30 | 数据与计算重试 |
| 工作日 20:00 | 晚间通知兜底 |
| 工作日 22:30 | 通知失败重试 |
| 周六 09:15 | 深度健康检查 |

可信盘后结果生成后可立即幂等投递日报，20:00 任务作为兜底。邮件需配置 `ALERT_EMAIL_ENABLED`、SMTP 发件信息、授权码、收件人及 `EVOALPHA_PUBLIC_URL`，详见 [邮件配置模板](backend/.env.example)。

LaunchAgent 日志统一写入 `~/Library/Logs/EvoAlpha`；Data Core 状态报告位于 `backend/logs/data_core_status/`。正式执行器提供锁、备份和超时控制，避免同时启动多个写库流程。旧 `auto_pipeline.py`、手工调度器和 LaunchAgent 不应重复启用。

## 验证与已知限制

```bash
# backend/：使用隔离测试库，确认测试不会写入正式库后运行
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest tests

# frontend/：串行执行，控制测试进程数量
node --test --test-concurrency=1 tests/*.test.mjs
```

2026-10-20 仓库同步验收：后端覆盖 1,173 项通过，前端生产构建与 TypeScript 检查通过；前端测试 197 项通过、8 项失败。失败涉及界面源码断言及 Node 对无扩展名导入的解析，尚未在此次同步中修复。完整范围、复核方法与资源数据见 [同步验证记录](docs/REPOSITORY_SYNC_2026-10-20.md)。

这份记录验证的是当前本地环境。新机器的依赖安装、全量数据初始化、云端部署和长期服务负载仍需分别验收。

## 文档导航

- [Data Core 来源与数据库映射](backend/docs/DATA_CORE_SOURCE_AND_DB_MAP.md)
- [数据管线架构](docs/DATA_PIPELINE_ARCHITECTURE.md)
- [存储与保留策略](backend/docs/DATA_STORAGE_RETENTION.md)
- [量化引擎](backend/quant_engine/README.md)
- [采集系统](backend/data_job/README.md)
- [结构转折规则 v2](backend/docs/STRUCTURE_TURNING_V2.md)
- [前端设计系统](FRONTEND_DESIGN_SYSTEM.md)
- [蓝图 1.0](BLUEPRINT1.0.md)（规划与设计参考，以当前代码为准）
- [开发约定](AGENTS.md)

部分子模块文档保留历史设计与命令，执行前应与当前入口核对。

Copyright © 2026 dlab (Evolution Lab). All rights reserved.
