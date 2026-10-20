# EU Alpha Data Core 缺口报告 - 2026-05-25

生成时间：2026-05-26 13:24 CST

## 结论

Data Core v0.1 的正式日K主链路已经恢复到目标交易日 `2026-05-25`。

- 个股日K：`stock_daily_prices` 最新为 `2026-05-25`，SH/SZ/BSE 三市场质量门均为 `PASS`。
- RPS Gate：`allowed=yes`，SH/SZ/BSE 均为 OK。
- RPS/强度/结构/生命周期：已重算到 `2026-05-25`。
- MRGC：已按 `2026-05-25` 执行，结果为 `0` 个新信号，因此 `quant_preselect_results` 最新有信号日期仍为 `2026-04-27`。
- 非阻断缺口：BSE 估值仍缺 `310/310`；北向持仓最新仅到 `2024-08-16`；research snapshot 为空。

## 正式日K质量门

| 市场 | 应有 | 实际 | 缺失 | 完整率 | 状态 |
| --- | ---: | ---: | ---: | ---: | --- |
| SH | 2313 | 2312 | 1 | 99.96% | PASS |
| SZ | 2888 | 2888 | 0 | 100.00% | PASS |
| BSE | 310 | 308 | 2 | 99.35% | PASS |

缺失明细：

| 市场 | 代码 | 名称 | 说明 |
| --- | --- | --- | --- |
| SH | 600355 | *ST精伦 | TDX 返回无原始数据，完整率仍满足 PASS |
| BSE | 920058 | 华洋赛车 | 目标日缺最新行 |
| BSE | 920305 | *ST云创 | 目标日缺最新行 |

最新日K来源：

| 市场 | source | adjustment_mode | quality_flag | 行数 |
| --- | --- | --- | --- | ---: |
| SH | tdx_mootdx_qfq | qfq | ok | 2312 |
| SZ | tdx_mootdx_qfq | qfq | ok | 2888 |
| BSE | tdx_standard_bj | none | ok_unadjusted | 308 |

## 数据源状态

| 源 | API | 市场 | 状态 | 成功 | 失败 | 时间 |
| --- | --- | --- | --- | ---: | ---: | --- |
| tdx_mootdx_qfq | standard_capability_probe | SH_SZ | PASS | 2 | 0 | 2026-05-26 13:21:57 |
| tdx_standard_bj | bse_standard_capability_probe | BSE | PASS | 6 | 0 | 2026-05-26 13:22:01 |
| tdx_standard_bj | stock_daily_bj_standard | BSE | PASS | 310 | 0 | 2026-05-26 11:43:36 |

历史诊断源 `tdx_exhq` 仍有 FAIL 日志，但它已降级为 diagnostic，不再作为正式 BSE 日K源，也不再阻断 Data Core。

## 主表与支撑数据缺口

| 表 | 行数 | 最新日期 | 状态/缺口 |
| --- | ---: | --- | --- |
| stock_daily_prices | 4,047,177 | 2026-05-25 | 正式日K已达目标交易日 |
| stock_valuation_daily | 165,527 | 2026-05-25 | SH 缺 1，SZ 完整，BSE 缺 310，非阻断 |
| index_daily | 5,796 | 2026-05-25 | 可用 |
| sector_daily_prices | 417,117 | 2026-05-25 | 可用 |
| etf_daily_prices | 52,943 | 2026-05-26 | 已覆盖目标日；ETF 采集器额外写入盘中 2026-05-26 |
| limit_board_trading | 1,256 | 2026-05-25 | 可用 |
| consecutive_boards_stats | 73 | 2026-05-25 | 可用 |
| news_articles | 55 | 2026-05-25 | 可用 |
| macro_indicators | 795 | 2026-04-01 | 宏观低频数据，非阻断 |
| stock_northbound_holdings | 366,046 | 2024-08-16 | 明显陈旧，股票池筛选仍按最后可得数据参与 |
| finance_fund_holdings | 55,652 | 2026-03-31 | 可用 |
| stock_finance_summary | 162,931 | 2026-03-31 | 可用 |

估值缺口按市场：

| 市场 | 应有 | 实际 | 缺失 | 是否阻断 |
| --- | ---: | ---: | ---: | --- |
| SH | 2313 | 2312 | 1 | 否 |
| SZ | 2888 | 2888 | 0 | 否 |
| BSE | 310 | 0 | 310 | 否 |

## 下游计算状态

| 表/任务 | 行数 | 最新日期 | 状态 |
| --- | ---: | --- | --- |
| quant_feature_rps | 209,039 | 2026-05-25 | 已追平日K |
| quant_feature_sector_rps | 18,848 | 2026-05-25 | 已追平板块行情 |
| quant_feature_etf_rps | 2,840 | 2026-05-26 | 已更新；受 ETF 表最新日期影响包含 2026-05-26 |
| market_strength_daily | 3,240 | 2026-05-25 | 已追平 |
| industry_strength_daily | 362,417 | 2026-05-25 | 已追平 |
| structure_health_daily | 62,860 | 2026-05-25 | 已追平 |
| stock_lifecycle_state | 21,735 | 2026-05-25 | 已追平 |
| quant_stock_pool | 1,809 | 2026-05-26 | 已重建 |
| quant_preselect_results | 128 | 2026-04-27 | MRGC 已跑 2026-05-25，但本轮 0 个新信号 |
| timing_signals_daily | 11 | 2026-04-29 | 本轮无新增信号，advisory |
| trade_plans | 11 | 2026-04-29 | 本轮无新增计划，advisory |
| research_stock_daily_snapshot | 0 | - | 空表，advisory |

## 重复键检查

| 表 | 重复键 |
| --- | ---: |
| stock_daily_prices | 0 |
| stock_valuation_daily | 0 |
| index_daily | 0 |
| sector_daily_prices | 0 |
| etf_daily_prices | 0 |
| quant_feature_rps | 0 |
| quant_feature_sector_rps | 0 |
| quant_feature_etf_rps | 0 |
| quant_preselect_results | 0 |

## 本轮执行命令摘要

```bash
python3 -m data_core.tdx_capability --trade-date 2026-05-25 --timeout 5
python3 scripts/standalone/collect_stock_kline_mootdx_qfq.py --market BSE --end 2026-05-25 --workers 1 --pool-size 1 --submit-window 8 --batch-size 20 --max-bars 80 --timeout 5 --connect-retries 2 --max-fail-rate 1.0
python3 -m data_core.quality --date 2026-05-25 --fail-on-fail
python3 -m data_job.collectors.etf_kline_collector
python3 scripts/run_daily_data_workflow.py --check-only --kline-end 2026-05-25
python3 scripts/run_daily_data_workflow.py --skip-kline --skip-market-side --include-strategy --kline-end 2026-05-25
```

## 后续建议

1. 对 BSE 估值建立非阻断补充策略，优先选择能明确单位和口径的低频源，不影响 RPS Gate。
2. ETF 采集器需要增加目标交易日参数，避免盘中写入 `2026-05-26` 这类未收盘支撑行情。
3. 北向持仓数据已经陈旧 647 天，建议后续单独处理或在股票池页面明确标记“北向因子降级”。
4. MRGC 本轮没有信号属于策略结果，不是运行失败；后续可在 `quant_preselect_results` 增加 run summary 表，记录“执行成功但 0 信号”。
