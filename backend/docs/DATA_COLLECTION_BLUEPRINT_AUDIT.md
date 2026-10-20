# EvoAlpha OS 数据采集代码梳理

> 基于 `BLUEPRINTnew.md` v2 蓝图整理。当前结论：采集底座已能支撑行情、估值、板块、ETF、财务、基金、新闻、涨停和旧北向持仓；RPS、市场强度、行业强度 MVP 表已落地。下一阶段重点是结构/买点/生命周期结果表，以及降低非关键 AkShare 接口失败时的耗时。

## 1. 蓝图数据需求映射

| 蓝图层级 | 需要的数据 | 当前主要来源/表 | 当前状态 |
| --- | --- | --- | --- |
| L0 市场强度 | 指数趋势、全市场涨跌家数、成交额、涨停/连板、新高扩散、RPS90 扩散 | `index_daily`, `stock_daily_prices`, `limit_board_trading`, `consecutive_boards_stats`, `quant_feature_rps`, `market_strength_daily` | MVP 已落地；最新日期受 `index_daily` 限制，当前到 `2026-04-24` |
| L1 行业强度 | 板块 K 线、板块 RPS、行业内强股比例、新高比例、龙头表现 | `sector_daily_prices`, `stock_sector_map`, `stock_daily_prices`, `quant_feature_sector_rps`, `industry_strength_daily` | MVP 已落地；真实板块映射不可用时会用 `stock_sector_map` 成分股合成行业指数，当前到 `2026-04-27` |
| L2 个股强度 | 个股 qfq K 线、成交额、换手率、五周期 RPS、财务/基金/北向/新闻辅助验证 | `stock_daily_prices`, `quant_feature_rps`, `stock_finance_summary`, `finance_fund_holdings`, `stock_northbound_holdings`, `news_articles` | qfq K 线、换手率、个股 RPS 已补齐 |
| L3 结构质量 | MA10/20/50/120/250、阶段新高、缩量回调、缺口结构 | `stock_daily_prices`, `stock_finance_summary` | 原始数据具备；`structure_signals_daily` 尚未落地 |
| L4 买点质量 | 放量突破、回踩转强、止损距离、成交量比 | `stock_daily_prices`, 结构信号表 | 原始数据具备；`timing_signals_daily` 尚未落地 |
| L5 风控复盘 | 持仓、交易计划、退出原因、复盘记录 | 未来 `trade_plans`, `stock_lifecycle_state` | 采集侧不负责；需要策略/交易计划模块补表 |

## 2. 当前推荐入口

日常 16:00 自动采集以这个脚本为唯一生产入口：

```bash
cd /Users/deserce/Documents/项目/EvoAlpha-OS/backend
python3 scripts/run_daily_data_workflow.py
```

这个入口已经按“核心必跑 + 非核心尽量跑”拆分：

| 顺序 | 任务 | 代码入口 | 关键性 |
| --- | --- | --- | --- |
| 1 | 个股 qfq K 线 | `scripts/standalone/collect_stock_kline_mootdx_qfq.py` | 必跑 |
| 2 | 股票估值 | `data_job.collectors.stock_valuation_collector` | 必跑 |
| 3 | 指数 K 线 | `data_job.collectors.index_data_collector` | 尽量跑 |
| 4 | 板块 K 线 | `data_job.collectors.sector_kline_collector` | 尽量跑 |
| 5 | ETF K 线 | `data_job.collectors.etf_kline_collector` | 尽量跑 |
| 6 | 涨停/连板 | `LimitBoardsCollector().run(days=1)` | 尽量跑 |
| 7 | 新闻 | `NewsCollector().run(days=1)` | 尽量跑 |
| 8 | 宏观 | `data_job.collectors.macro_data_collector` | 尽量跑 |
| 9 | RPS 因子 | `quant_engine.runner.feature_runner --mode daily` | 必跑（加 `--include-rps`） |
| 10 | 市场/行业强度 | `scripts/calculate_strength_features.py --mode daily` | 必跑（加 `--include-strength`） |
| 11 | 核心股票池 | `quant_engine.pool.maintain_pool` | 必跑 |

可选参数：

```bash
python3 scripts/run_daily_data_workflow.py --check-only
python3 scripts/run_daily_data_workflow.py --skip-rps
python3 scripts/run_daily_data_workflow.py --skip-p0
python3 scripts/run_daily_data_workflow.py --include-quarterly
python3 scripts/run_daily_data_workflow.py --northbound-limit 100
```

说明：北向个股持仓使用旧接口，最近可得数据停在 `2024-08-16`，默认不放入每日任务。需要补采时用 `--northbound-limit` 分批跑，避免网络卡死拖垮日常任务。

## 3. 采集器状态清单

### 3.1 生产推荐

| 采集器/脚本 | 目标表 | 数据源 | 使用频率 | 备注 |
| --- | --- | --- | --- | --- |
| `stock_sector_list_collector.py` | `stock_info`, `stock_sector_map` | AkShare + mootdx 兜底 | 初始化/月度 | 行业映射是 L1 行业强度的底座 |
| `collect_stock_kline_mootdx_qfq.py` | `stock_daily_prices` | mootdx + 本地 qfq | 初始化/每日 | 当前个股 K 线主入口，支持高速增量、xdxr、换手率 |
| `stock_valuation_collector.py` | `stock_valuation_daily` | Eastmoney + 本地 K 线兜底 + Sina | 每日 | 同步更新 `stock_info` 估值字段 |
| `index_data_collector.py` | `index_daily` | AkShare | 每日 | L0 指数趋势底座 |
| `sector_kline_collector.py` | `sector_daily_prices` | Eastmoney/AkShare | 每日 | 依赖 `sector_list`，无表时优先复用已入库真实板块，避免误用 `TDX行业*` |
| `etf_info_collector.py` | `etf_info` | 配置列表 + AkShare | 月度/初始化 | ETF 池规模当前 71 |
| `etf_kline_collector.py` | `etf_daily_prices` | AkShare | 每日 | ETF 页面和防守配置底座 |
| `limit_boards_collector.py` | `limit_board_trading`, `consecutive_boards_stats` | AkShare 东方财富涨停池 | 每日 | L0 赚钱效应核心数据 |
| `news_collector.py` | `news_articles`, `news_stock_relation` | 财新数据通 | 每日 | 当前偏宏观新闻，个股关联较少 |
| `macro_data_collector.py` | `macro_indicators` | AkShare | 每日/月度 | 日跑可以，但数据自然是月/季频 |
| `finance_summary_collector.py` | `stock_finance_summary` | AkShare 业绩报表 | 季度/报告期 | 支撑业绩催化、跳空结构解释 |
| `fund_holdings_collector.py` | `finance_fund_holdings` | AkShare 基金持仓 | 季度/报告期 | 当前股票池的重要筛选数据 |
| `northbound_holdings_collector.py` | `stock_northbound_holdings` | AkShare/Eastmoney 旧北向接口 | 手动分批 | 只采最后可得旧数据，不做每日默认任务 |
| `calculate_strength_features.py` | `market_strength_daily`, `industry_strength_daily` | 本地已采集表 | 每日/RPS 后 | 蓝图 L0/L1 强度 MVP 计算入口 |

### 3.2 可保留但不作为默认入口

| 文件 | 原因 | 建议 |
| --- | --- | --- |
| `stock_kline_collector.py` | AkShare qfq 串行，慢且更容易受限 | 保留为兜底，不再作为主链路 |
| `stock_kline_collector_optimized.py` | AkShare 并发版，仍依赖网页接口 | 保留为兜底/对照 |
| `multi_source_kline_collector.py` | 多源 fallback 思路有价值，但当前不如 mootdx qfq 稳定 | 后续可合并为统一 fallback 层 |
| `adjustable_kline_collector.py` / `qfq_kline_collector.py` | 调整价实验路径 | 保留参考，避免和主 qfq 入口并行写同表 |
| `data_job/utils/run_all_collectors.py` | 全量流程仍走 AkShare K 线优化版 | 可改造为调用 mootdx qfq 脚本 |
| `data_job/utils/scheduler.py` | 调度器还指向旧 `run_all_collectors` | 与当前 16:00 自动化重复，后续应统一 |

### 3.3 应标记为历史/实验

`backend/scripts/collect_fast.py`、`collect_multithread.py`、`collect_all_stocks.py`、`collect_3years_kline.py`、`quick_init.py` 等历史采集实验脚本已迁移到 `backend/scripts/archive/collection_experiments/`。`update_missing_dates.py`、`scripts/standalone/pytdx_*`、`mootdx_downloader*` 如继续出现，应明确标记为只读参考，避免后续误用旧入口污染数据。

`data_job/scripts/run_daily_update.py` 不建议继续使用：它构造 `NewsCollector(days=1)`、`LimitBoardsCollector(days=1)` 的方式与当前类签名不匹配，而且默认关闭 K 线更新。

## 4. 当前数据库入库状态

最近一次 `--check-only` 快照：

| 表 | 行数 | 日期范围 |
| --- | ---: | --- |
| `stock_daily_prices` | 3,697,195 | `2023-01-03..2026-04-27` |
| `stock_valuation_daily` | 10,238 | `2026-04-23..2026-04-27` |
| `index_daily` | 5,816 | `2023-04-24..2026-04-24` |
| `sector_daily_prices` | 16,217 | `2023-01-03..2026-04-24` |
| `etf_daily_prices` | 51,627 | `2023-04-24..2026-04-28` |
| `limit_board_trading` | 313 | `2026-04-20..2026-04-28` |
| `consecutive_boards_stats` | 24 | `2026-04-20..2026-04-28` |
| `news_articles` | 19 | `2026-04-24..2026-04-28` |
| `macro_indicators` | 793 | `2005-02-01..2026-03-31` |
| `stock_northbound_holdings` | 366,046 | `2017-03-16..2024-08-16` |
| `finance_fund_holdings` | 55,652 | `2023-03-31..2026-03-31` |
| `stock_finance_summary` | 162,931 | `2021-03-31..2026-03-31` |
| `quant_stock_pool` | 1,804 | `2026-04-28..2026-04-28` |
| `quant_feature_rps` | 1,344,441 | `2025-03-31..2026-04-27` |
| `quant_feature_sector_rps` | 3,670 | `2025-03-31..2026-04-24` |
| `quant_feature_etf_rps` | 18,602 | `2025-03-31..2026-04-28` |
| `market_strength_daily` | 2,908 | `2023-04-24..2026-04-24` |
| `industry_strength_daily` | 39,247 | `2023-01-03..2026-04-27` |

重复键检查：

| 表 | 重复键 |
| --- | ---: |
| `stock_daily_prices` | 0 |
| `stock_valuation_daily` | 0 |
| `stock_northbound_holdings` | 0 |
| `quant_stock_pool` | 0 |
| `quant_feature_rps` | 0 |
| `quant_feature_sector_rps` | 0 |
| `quant_feature_etf_rps` | 0 |
| `market_strength_daily` | 0 |
| `industry_strength_daily` | 0 |

缺口：

- `sector_list` 当前不存在；`sector_kline_collector.py` 已修正为优先复用 `sector_daily_prices` 里的真实板块名，避免 `stock_sector_map` 中 `TDX行业*` 拖慢采集。
- 真实行业/概念映射接口本次不可用，`stock_sector_list_collector.py` 已加旧映射恢复保护，避免“先清空后失败”导致 `stock_sector_map` 变空。
- `index_daily` 当前最新到 `2026-04-24`，因此 `market_strength_daily` 最新也到 `2026-04-24`；`industry_strength_daily` 已通过本地行业映射合成到 `2026-04-27`。
- ETF 日线接口本次网络波动较多，但 ETF RPS 已基于已有数据补齐到 `2026-04-28`。

## 5. 与蓝图的差距

### P0：必须先稳住

1. 统一每日入口：以 `scripts/run_daily_data_workflow.py` 为唯一生产入口，旧 APScheduler 和旧 run_daily_update 不再作为默认生产入口。
2. 默认自动刷新 RPS、市场/行业强度、结构健康、生命周期状态机；临时排障时才使用 `--skip-rps` 或 `--skip-p0`。

### P1：蓝图第一阶段需要补

1. 强化 `market_strength_daily` 评分规则：当前是可运行 MVP，后续可引入跌停、破位股、大阴线、封板率等更细指标。
2. 强化 `industry_strength_daily` 评分规则：当前是行业 RPS + 强股比例 + 新高比例 + 龙头强度 MVP，后续可补行业持续性和龙一/龙二/中军分层。
3. RPS 周期主逻辑保留 `5/10/20/50/120/250`，蓝图核心页面和策略使用 `5/20/50/120/250`，`10` 作为兼容字段。

### P2：结构/买点层后续补

1. 新增 `structure_signals_daily`：用 K 线、均线、成交量、新高距离识别结构。
2. 新增 `timing_signals_daily`：在结构池上识别四类买点。
3. 新增 `stock_lifecycle_state` 和 `trade_plans`：把 L0-L4 串成 S0-S7 生命周期。

## 6. 建议目录治理

建议把后台采集代码分成四类，减少“我到底该跑哪个脚本”的认知成本：

```text
backend/
  scripts/
    run_daily_data_workflow.py        # 日常生产入口
    standalone/
      collect_stock_kline_mootdx_qfq.py
    archive/                          # 历史实验脚本迁移到这里
  data_job/
    collectors/                       # 单一职责采集器
    core/                             # 数据源适配、qfq、基类
    utils/                            # 质量检查、历史工具
  quant_engine/
    calculators/                      # RPS/强度/结构/买点计算器
```

短期不要再新增 `quick_*`、`fast_*`、`simple_*` 入口。新任务优先挂到 `run_daily_data_workflow.py` 或明确放入 `scripts/archive/`。

## 7. 下一步落地顺序

1. 将每日 16:00 任务参数升级为 `--include-rps --include-strength`。
2. 归档历史 K 线脚本，只保留 mootdx qfq 主入口和明确 fallback。
3. 新增 `structure_signals_daily`，先做阶段新高、均线顺排、回踩、波动收缩 MVP。
4. 新增 `timing_signals_daily`，先做四类买点中的“平台/新高放量突破”和“均线缩量回踩后转强”。
5. 新增 `stock_lifecycle_state`，把 M/I/RPS/结构/买点串成 S0-S7。
