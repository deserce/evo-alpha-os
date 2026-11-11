# Data Core 补数验收报告

验收日期：2026-10-28，Asia/Shanghai。最终轻量 health：23:16:35。

## 结论

按用户要求先运行已有补漏流程，之后只修复实际复现的问题。最近 7 个交易日
（10/20、21、22、23、26、27、28）的行情、主要计算、MRGC、低吸/C 快照和本地日报
均已完成现有修复清单验收，`repair_pending_dates=[]`。

这不表示全库所有字段均完整：无原始行情例外和历史估值降级仍明确保留，
`full_recent_window_complete=false`，没有用今天的估值或虚构 K 线填平历史。

## 实际执行

1. 全局写锁内先创建 APFS COW 备份：
   `backend/data/backups/local_quant_2026-10-28_20261028_221006.db`，耗时 5.31 秒。
2. 运行现有 wrapper：
   `python scripts/run_local_data_skill.py --mode retry --target 2026-10-28 --skip-notifications`。
   首轮 workflow 用时 432.3 秒，恢复 10/28 主链；最终精确日期验收发现 10/26 生命周期少一行。
3. 使用现有 collector 补资讯、涨停榜、宏观已发布数据，并补估值缺行。
4. 修复生命周期冻结名单问题后，只计算 `2026-10-26 / 601995 中金公司` 一行；
   通过共享 planner 验证、持久化完成状态，未直接删除未完成债务。
5. 现有有界 gap repair 实际重试 10/28 的 19 只、10/26 至 27 的 18 只股票。
   后者共 36 个股票-日期缺口。全部返回 `no raw data`，没有新增或重写历史价格，
   按已有规则登记 `tdx_no_raw_data` 例外。
6. 再次运行同一 retry：任务数 0、行情和计算无重复执行，workflow 2.7 秒、wrapper 4.97 秒，退出码 0。
7. 运行 SQLite `quick_check`、历史覆盖和异常扫描；再刷新轻量 health。

未更换日常数据源、未改调度时间、未发送补发邮件、未提交或上传 Git。

## 10/28 数据

| 数据 | 验收结果 |
| --- | --- |
| 股票 raw / qfq K 线 | 各 5511 行，日期均为 10/28 |
| 三市场日 K 质量 | SH、SZ、BSE 全部 PASS；异常和重复键均为 0 |
| 股票 RPS | 5530 行；覆盖全部 5511 只当日有 K 线股票 |
| 行业合成 / 行业 RPS v2 | 各 496 行 |
| 指数 | 8/8 |
| ETF / ETF RPS | 各 71/71 |
| 生命周期 | 1752/1752；按当日名单验收 |
| MRGC | 成功，39 只 |
| 低吸 Watch | 47/47 |
| C 监控 | 141/141，运行状态 COMPLETED |
| 本地日报 | READY；最近 7 个交易日均 READY |
| 涨停 / 跌停 / 连板统计 | 72 / 5 / 4 行 |
| 市场资讯 | 当日 5 条 |
| 估值行 | 5529 行；5209 行腾讯直接数据、320 行本地降级 |

股票总名单 5530 只。10/28 无原始行情的 19 只为 SH 11、SZ 7、BSE 1。
这些记录是上游无数据例外，不是已补出价格，也未直接断言全部属于停牌。
RPS 行数多于当日 K 线行数不等于额外生成 K 线。

## 最近 7 个交易日

| 日期 | 股票 K 线 | 生命周期实际/冻结或当日期望 | MRGC 数量 | Watch | C | 本地日报 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 10/20 | 5513 | 1756/1756 | 31 | 47/47 | 121/121 | READY |
| 10/21 | 5505 | 1756/1756 | 34 | 47/47 | 122/122 | READY |
| 10/22 | 5507 | 1756/1756 | 28 | 47/47 | 123/123 | READY |
| 10/23 | 5507 | 1756/1756 | 35 | 47/47 | 132/132 | READY |
| 10/26 | 5512 | 1756/1756 | 40 | 47/47 | 138/138 | READY |
| 10/27 | 5512 | 1755/1755 | 37 | 47/47 | 141/141 | READY |
| 10/28 | 5511 | 1752/1752 | 39 | 47/47 | 141/141 | READY |

以上各日指数 8/8、ETF 71/71、行业合成和 RPS v2 496/496。
生命周期名单随日期变化，不用今天名单替换历史名单。

## 根因与最小修复

10/26 冻结名单含中金公司 601995，但今天的核心池已不包含该股票。
原计算器先读取当前池、再筛定向 symbols，导致历史补算返回成功但实际永远只有 1755 行。

- `backend/data_core/repair_plan.py`：共用只读冻结名单读取、格式及容量校验。
- `backend/quant_engine/calculators/lifecycle_state_calculator.py`：先按每个输出日确定冻结名单，
  再筛选股票；单股定向补算不被整日覆盖率跳过；删除和插入处于同一事务。
- `backend/tests/test_lifecycle_state_calculator.py`：新增先失败后通过的冻结名单、幂等、
  多日期隔离、无效输入零写入、插入失败回滚及受保护表不变测试。
- `backend/tests/test_daily_workflow_plan.py`：固定一个“当日补数”测试的时钟；
  原测试在日期过去后会错误期待历史日重采。正式 workflow 未为迎合该测试改动。

现有 mootdx 已恢复并完成真实采集。此次无需修改网络适配器或选择其他来源。
前端没有本轮改动，无需部署或重建。

## 历史健康与例外

历史聚合覆盖扫描范围为 2023-01-03 至 2026-10-28，共 923 个交易日：

- `low_coverage_date_count=0`。
- `zero_row_date_count=0`。
- `stock_info_list_date_missing=0`。
- 最低市场覆盖率：SH 98.79%、SZ 98.95%、BSE 99.13%。
- 近 90 日 RPS250 大跳变、高价 OHLCV 克隆、前值存在但涨跌幅为空、异常估值快照均为 0。
- SQLite `quick_check` 通过。

最近 45 个交易日（8/19 至 10/28）逐股缺口扫描：原始缺口 799、已登记例外 799、
`effective_missing_count=0`、`abnormal_count=0`、候选股票数 0。
这是“未解释缺口为零”，不是“799 根价格已下载成功”。
近三年摘要采用市场日覆盖聚合，不等价于近三年每只股票逐根精确审计。

仍存在的估值限制：10/20 至 23 分别有 5513、5505、5507、5507 行本地降级；
10/26、27、28 各 320 行本地降级。当前行已覆盖有 K 线股票，但未知 PE/PB/市值等字段
保持缺失，不能从当前快照倒填历史，故完整字段状态仍为 false。

原已接受的 7/3 涨跌幅种子边界和 6/12 旧估值异常未主动修库，仍在已知历史问题中保留。
宏观指标最新发布期为 9/30，属于按月/季度公布，不伪造 10/28 日频记录。
旧行业行情/RPS 表不是当前行业主链；本次按合成行情与 RPS v2 验收。

## 最终状态和安全对账

`core_ok=true`、`history_ok=true`、`data_anomaly_ok=true`；
`support_stale=[]`、`critical_feature_stale=[]`、`recent_pending_dates=[]`；
`monitoring_ready=true`、`local_reports_ready=true`，无 running 残留。

与写库前快照逐行哈希对账：账户 1、交易 33、持仓 8、邮件投递 309 条均不变；
非智能 MRGC 池的手工成员不变。10/20 至 27 的 raw 和 qfq 各 33056 行价格哈希不变。
正常当天流程更新了智能池和派生快照，不涉及真实持仓下单或交易记账。
launchd 配置均已加载且 config=ok；各服务 last_exit 仍保留此前失败结果，
手工修复成功不会改写 launchd 上一次调度退出码，后续调度仍需按实际执行结果观察。

## 测试与资源

最终命令从 `backend/` 运行：

```bash
env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 APP_DEBUG=false \
  /opt/anaconda3/bin/python3 -m pytest \
  tests/test_local_data_scheduler.py tests/test_daily_workflow_plan.py \
  tests/test_lifecycle_state_calculator.py tests/test_repair_plan.py \
  tests/test_sparse_exact_dates.py tests/test_calculation_dates.py -q
```

结果：278 passed，7 条现有 Pydantic/Pandas 弃用警告，64.30 秒；`git diff --check` 通过。
原失败时钟测试已实际复现再修复，没有跳过或删除断言。

原生计算线程均限制为 1；定向 gap repair 使用 2 worker、2 连接、提交窗口 4、最多 40 根。
下载阶段抽样 RSS 约 186 MiB（不是全程峰值）；只读历史聚合扫描 18.04 秒、峰值 RSS 38.8 MiB、
数据库写入 0。正式深度 health 112.16 秒、峰值 RSS 122 MiB、swap 0。
仅复用一份当天自动备份，未重复复制整库。数据库约 5.4 GiB，备份目录约 5.6 GiB、日志目录约 168 MiB；
APFS COW 的逻辑文件大小不能直接视为额外独占磁盘量。磁盘可用约 24 GiB，仍建议保持余量。

## 证据

- 主补数：`backend/logs/daily_data_workflow_20261028_221032.log`。
- 支撑数据：`backend/logs/daily_data_workflow_20261028_222134.log`。
- 单行修复：`backend/logs/repair_lifecycle_20261028_224742.log`。
- 幂等复跑：`backend/logs/daily_data_workflow_20261028_225131.log`。
- 26/27 缺口核验：`pipeline_run_log.run_id=repair_recent_gaps_20261028_231028`。
- 最新状态：`backend/logs/data_core_status/latest.json`。
