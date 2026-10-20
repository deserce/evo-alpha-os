# Data Core Source And DB Map

Generated at: 2026-05-25 21:33:58
Database: `/Users/deserce/Documents/项目/EvoAlpha-OS/backend/data/local_quant.db`

## Summary

- Latest stock daily date: `2026-05-25`
- Stock universe by market: SH:2313, SZ:2888, BSE:310
- Latest daily rows by market: SH:2312, SZ:2888, BSE:0
- Formal stock daily writes are TDX-only. HTTP sources are diagnostic or legacy only.

## Source Registry

| Source | Module | Role | Official Write | Blocks Downstream | Applies To |
| --- | --- | --- | --- | --- | --- |
| `tdx_mootdx_qfq` | stock_daily | official | yes | yes | SH/SZ 股票日K、增量更新、区间补齐 |
| `tdx_exhq` | stock_daily | official | yes | yes | BSE 股票日K capability / 后续正式接入 |
| `eastmoney_spot_em` | stock_valuation | supplemental | yes | no | stock_valuation_daily |
| `local_kline_derived` | stock_valuation | supplemental | yes | no | stock_valuation_daily fallback |
| `sina_spot` | stock_valuation | legacy | yes | no | stock_valuation_daily legacy fallback |
| `multi_source_auto` | stock_daily | diagnostic | no | no | diagnostic only |
| `akshare_legacy` | stock_daily | legacy | no | no | legacy diagnostic only |

## Table Inventory

| Table | Role | Module | Rows | Date Range | P0 Gate |
| --- | --- | --- | ---: | --- | --- |
| `stock_info` | core | stock_basic | 5511 | - | yes |
| `stock_daily_prices` | core | stock_daily | 3845387 | 2023-01-03..2026-05-25 | yes |
| `stock_valuation_daily` | core | stock_valuation | 165527 | 2026-04-07..2026-05-25 | no |
| `trading_calendar` | core | stock_daily | 1636 | 2023-01-03..2026-05-25 | yes |
| `data_source_log` | core | data_core | 26 | 2026-05-15..2026-05-25 | no |
| `data_quality_report` | core | data_core | 18 | 2026-05-15..2026-05-25 | yes |
| `quant_feature_rps` | derived | quant | 176510 | 2026-03-18 00:00:00.000000..2026-05-08 00:00:00.000000 | no |
| `quant_stock_pool` | derived | quant | 1816 | 2026-05-09..2026-05-09 | no |
| `index_daily` | support | market_side | 5796 | 2023-05-09..2026-05-25 | no |
| `sector_daily_prices` | support | market_side | 417117 | 2023-01-03..2026-05-25 | no |
| `etf_daily_prices` | support | market_side | 52091 | 2023-04-24..2026-05-08 00:00:00.000000 | no |
| `limit_board_trading` | support | market_side | 1256 | 2026-04-15 00:00:00.000000..2026-05-25 00:00:00.000000 | no |
| `limit_down_trading` | support | market_side | 206 | 2026-04-15 00:00:00.000000..2026-05-25 00:00:00.000000 | no |
| `consecutive_boards_stats` | support | market_side | 73 | 2026-04-15 00:00:00.000000..2026-05-25 00:00:00.000000 | no |
| `sector_list` | support | market_side | 496 | - | no |
| `stock_sector_map` | support | market_side | 15600 | - | no |
| `etf_info` | support | market_side | 71 | - | no |
| `stock_finance_summary` | support | research | 162931 | 2021-03-31..2026-03-31 | no |
| `finance_fund_holdings` | support | research | 55652 | 2023-03-31..2026-03-31 | no |
| `stock_northbound_holdings` | support | research | 366046 | 2017-03-16..2024-08-16 | no |
| `macro_indicators` | support | research | 795 | 2005-02-01 00:00:00.000000..2026-04-01 | no |
| `news_articles` | support | research | 55 | 2026-04-24 00:00:00.000000..2026-05-25 00:00:00.000000 | no |
| `news_stock_relation` | support | research | 0 | - | no |
| `quant_feature_sector_rps` | derived | quant | 17360 | 2026-03-17 00:00:00.000000..2026-05-08 00:00:00.000000 | no |
| `quant_feature_etf_rps` | derived | quant | 2485 | 2026-03-17 00:00:00.000000..2026-05-08 00:00:00.000000 | no |
| `market_strength_daily` | derived | quant | 3228 | 2023-01-03..2026-05-08 | no |
| `industry_strength_daily` | derived | quant | 360929 | 2023-01-03..2026-05-08 | no |
| `structure_health_daily` | derived | quant | 46368 | 2026-04-23..2026-05-08 | no |
| `stock_lifecycle_state` | derived | quant | 16308 | 2026-04-23..2026-05-08 | no |
| `timing_signals_daily` | derived | quant | 11 | 2026-04-24..2026-04-29 | no |
| `trade_plans` | derived | quant | 11 | 2026-04-24..2026-04-29 | no |
| `research_stock_daily_snapshot` | derived | research | 0 | -..- | no |
| `quant_preselect_results` | derived | quant | 128 | 2026-04-27..2026-04-27 | no |
| `research_themes` | derived | research | 0 | - | no |
| `research_theme_nodes` | derived | research | 0 | - | no |
| `research_watchlist` | derived | research | 0 | - | no |
| `pipeline_run_log` | support | ops | 43 | - | no |
| `quant_task_run_log` | support | ops | 3 | - | no |
| `stock_kline_qfq` | legacy | stock_daily | 2486074 | 2024-03-04..2026-04-23 | no |
| `sector_kline` | legacy | market_side | 74 | 2025-12-30..2026-04-23 | no |
| `etf_kline` | legacy | market_side | 0 | -..- | no |

## Latest Quality Reports

| Date | Module | Market | Status | Expected | Actual | Missing | Score |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| 2026-05-25 | stock_daily | BSE | FAIL | 310 | 0 | 310 | 0.00 |
| 2026-05-25 | stock_daily | SH | PASS | 2313 | 2312 | 1 | 99.96 |
| 2026-05-25 | stock_daily | SZ | PASS | 2888 | 2888 | 0 | 100.00 |
| 2026-05-20 | stock_daily | BSE | FAIL | 310 | 0 | 310 | 0.00 |
| 2026-05-20 | stock_daily | SH | FAIL | 2313 | 0 | 2313 | 0.00 |
| 2026-05-20 | stock_daily | SZ | FAIL | 2888 | 0 | 2888 | 0.00 |
| 2026-05-19 | stock_daily | BSE | FAIL | 310 | 0 | 310 | 0.00 |
| 2026-05-19 | stock_daily | SH | FAIL | 2313 | 0 | 2313 | 0.00 |
| 2026-05-19 | stock_daily | SZ | FAIL | 2888 | 0 | 2888 | 0.00 |
| 2026-05-18 | stock_daily | BSE | FAIL | 310 | 0 | 310 | 0.00 |
| 2026-05-18 | stock_daily | SH | FAIL | 2313 | 0 | 2313 | 0.00 |
| 2026-05-18 | stock_daily | SZ | FAIL | 2888 | 0 | 2888 | 0.00 |

## Latest Source Failures

| Source | API | Market | Status | Fail Count | Error | Created At |
| --- | --- | --- | --- | ---: | --- | --- |
| `tdx_exhq` | bse_capability_probe | BSE | FAIL | 6 | exhq_beijing: TimeoutError: timed out; exhq_shenzhen: TimeoutError: timed out; exhq_shanghai_1: TimeoutError: timed out | 2026-05-25 13:23:29 |
| `tdx_mootdx_qfq` | stock_daily_qfq | SH | FAIL | 1 | 600355:no raw data | 2026-05-25 12:58:16 |
| `tdx_mootdx_qfq` | stock_daily_qfq | SH | WARNING | 2 | 600355:no raw data; 688188:no raw data | 2026-05-25 12:57:29 |
| `tdx_exhq` | bse_capability_probe | BSE | FAIL | 6 | exhq_beijing: TimeoutError: timed out | 2026-05-25 12:46:05 |
| `tdx_mootdx_qfq` | adapter_pool | ALL | FAIL | 5511 | no available TDX connections: slot=5, attempt=3; slot=6, attempt=1; slot=6, attempt=2; slot=6, attempt=3; slot=7, attempt=1; slot=7, attempt=2; slot=7, attempt=3; slot=8, attempt=1; slot=8, attempt=2; slot=8, attempt=3 | 2026-05-20 08:16:33 |
| `tdx_exhq` | bse_capability_probe | BSE | FAIL | 6 | exhq_beijing: connect_failed; exhq_shenzhen: connect_failed; exhq_shanghai_1: connect_failed | 2026-05-20 08:02:28 |
| `tdx_mootdx_qfq` | standard_capability_probe | SH_SZ | FAIL | 2 | standard TDX adapter unavailable | 2026-05-20 08:02:26 |
| `tdx_mootdx_qfq` | adapter_pool | ALL | FAIL | 5511 | no available TDX connections: slot=5, attempt=3; slot=6, attempt=1; slot=6, attempt=2; slot=6, attempt=3; slot=7, attempt=1; slot=7, attempt=2; slot=7, attempt=3; slot=8, attempt=1; slot=8, attempt=2; slot=8, attempt=3 | 2026-05-19 08:19:46 |

## Current Blockers

- `stock_daily[BSE]` is FAIL on `2026-05-25`: actual=0, expected=310, missing=310.
- `stock_daily[BSE]` is FAIL on `2026-05-20`: actual=0, expected=310, missing=310.
- `stock_daily[SH]` is FAIL on `2026-05-20`: actual=0, expected=2313, missing=2313.
- `stock_daily[SZ]` is FAIL on `2026-05-20`: actual=0, expected=2888, missing=2888.
- `stock_daily[BSE]` is FAIL on `2026-05-19`: actual=0, expected=310, missing=310.
- `stock_daily[SH]` is FAIL on `2026-05-19`: actual=0, expected=2313, missing=2313.
- `stock_daily[SZ]` is FAIL on `2026-05-19`: actual=0, expected=2888, missing=2888.
- `stock_daily[BSE]` is FAIL on `2026-05-18`: actual=0, expected=310, missing=310.
- `stock_daily[SH]` is FAIL on `2026-05-18`: actual=0, expected=2313, missing=2313.
- `stock_daily[SZ]` is FAIL on `2026-05-18`: actual=0, expected=2888, missing=2888.
- `stock_daily[BSE]` is FAIL on `2026-05-15`: actual=0, expected=310, missing=310.
- `stock_daily[SH]` is FAIL on `2026-05-15`: actual=0, expected=2313, missing=2313.
- `stock_daily[SZ]` is FAIL on `2026-05-15`: actual=0, expected=2888, missing=2888.
