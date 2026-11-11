# Data Core 续传与故障恢复验收

日期：2026-10-31（Asia/Shanghai）；补数目标：2026-10-30。

## 实施范围

按用户批准方案实施：只补缺失或异常股票、单协调者有界换服、移除指数/ETF的百度预检依赖。
未修改调度时间、选股规则、前端、真实持仓或交易；未发送补发邮件，未提交或上传 Git。
已有未提交改动保持原样，不以当前 HEAD 的全部 diff 作为本轮修改范围。

## 代码修改

- `backend/scripts/standalone/collect_stock_kline_mootdx_qfq.py`：新增 `--resume-target-day`。
  先按目标日检查 raw/qfq 双表、字段、来源、复权模式、同日重复及收盘价一致性，
  跳过有效股票；只对候选查询索引种子日期，不为续传执行全历史 `GROUP BY`。
- 续传限最新完成交易日、单日、有限 seed 窗口，保留手工 `--force-window` 原语义。
  更早历史仍走原锚定 gap repair，不用历史单日 qfq 覆盖已有价格。
- 批量连接故障时停止提交、等待在途任务归还租约、保存成功批次并关闭旧池；
  主线程最多重建一次连接池，复用既有 30 秒启动预算、坏节点冷却和真实行情验证。
  仅重试连接失败及未处理名单，成功股票不重抓；再次失败明确返回非零。
- `backend/data_job/core/mootdx_adapter.py`：增加连接池管理恢复模式，工作线程不自行反复探测节点。
  普通非池化调用保留原有一次额外尝试，盘中调用语义不变。
- `resume_target` 在单个事务内按股票和目标日半开区间替换双表，清除坏行及时间戳重复；
  提交前按本批候选复查，任一不合格则双表一起回滚，其他股票和历史不动。
- `backend/scripts/run_daily_data_workflow.py`：接入续传；周末、晨间也可续传最新完成交易日。
  更早日期仍保持历史保护，不重采单日 qfq；质量门及失败时的依赖阻断不降低。
- `backend/data_job/collectors/index_data_collector.py` 和 `etf_kline_collector.py`：
  专属健康检查只校验数据库；由正式采集验证 TDX/HTTP 来源，不再用百度响应判断行情可用性。
  不修改全局 BaseCollector，也不增加重复行情探针。
- 日志速率排除已在本地完成的股票；确认的 `no raw data` 记录保留 WARNING 和失败数量，
  不把缩小补缺名单后的高比例无行情误称为整体源故障。连接、协议及其他错误仍保留 FAIL。

## 测试闭环

先复现再修复：双表续传、池化线程重复发现、周末接续、百度预检拦截、坏行不替换、
时间戳重复无法清理、双表价格矛盾，以及无行情日志误分类均有失败证据。
回归覆盖修复幂等、历史及其他股票不变、事务回滚、恢复一次后仍失败、成功股票不重抓、
完整日期零行情连接、数据库失败阻断、真正源失败和原有盘中逻辑。

从 `backend/` 运行以下离线测试，使用临时库并限制原生线程为1：

```bash
env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 APP_DEBUG=false \
  /opt/anaconda3/bin/python3 -m pytest \
  tests/test_kline_collector_stability.py data_job/tests/test_mootdx_qfq.py \
  tests/test_daily_workflow_plan.py tests/test_local_data_scheduler.py \
  tests/test_index_data_collector.py tests/test_etf_kline_collector.py \
  tests/test_repair_plan.py tests/test_data_core_repair.py \
  tests/test_intraday_watch.py tests/test_mootdx_intraday.py -q
```

最终结果：**427 passed，10 subtests passed，63.93秒**。
有10条已有 Pydantic/pkg_resources 弃用警告；`git diff --check` 通过。
独立代码审查发现的3项落库边界问题已修复并复核通过。

## 正式补数

确认无活动写库任务后，核验并复用：
`backend/data/backups/local_quant_2026-10-30_20261030_153003.db`。
检查了 SQLite 文件头、只读打开、103张表可读及持仓记录可读；本轮未再复制整库。

执行现有 wrapper：

```bash
/opt/anaconda3/bin/python3 scripts/run_local_data_skill.py \
  --mode retry --target 2026-10-30 --skip-notifications --lock-wait-minutes 0
```

主运行：`retry_20261031_094031`。

| 实测项 | 结果 |
| --- | --- |
| 股票名单 | 5530 |
| 已完整、未重新请求 | 4967 |
| 本次请求候选 | 563，比全名单少89.8% |
| 新增 raw / qfq | 各543行 |
| 上游无原始行情 | 20只，未填造价格 |
| 股票采集阶段 | 12.3秒；内部批次耗时10.70秒 |
| 整体 workflow | 217.3秒；wrapper约222.3秒，退出码0 |
| 真实恢复触发 | 本次节点正常，0轮；一次恢复分支经离线故障注入验证 |

随后仅对20只无行情股票运行原有 bounded gap repair，实际重试仍全部 `no raw data`，
登记20条例外并重新运行三市场质量门。这是有记录的上游例外，不代表价格已下载成功。

最终同命令复跑：业务任务0项，workflow约4秒、wrapper约8.4秒，未重复行情或因子计算。

## 目标日验收

| 数据 | 结果 |
| --- | --- |
| raw / qfq K线 | 各5510行 |
| 股票RPS | 5530行，覆盖全部5510只当日有行情股票 |
| 行业合成 / 行业RPS v2 | 各496行 |
| 指数 | 8/8 |
| ETF / ETF RPS | 各71/71 |
| 生命周期 | 1751行 |
| MRGC | success，73只 |
| 低吸快照 | 47条 |
| C监控 / 本地日报 | 已完成 / READY |
| 三市场质量 | SH 2303、SZ 2887、BSE 320；均PASS，异常与重复键均0 |

最终 `core_ok=true`、`support_stale=[]`、`critical_feature_stale=[]`、
`repair_pending_dates=[]`、`monitoring_ready=true`、`local_reports_ready=true`。

## 价格与账户安全

逐行 SHA-256 对账：

- 原有4967行目标日 qfq 和4967行 raw 均未改变。
- 10/21至10/29 qfq 和 raw 各38565行均未改变。
- 账户1、交易33、持仓8、邮件投递319条内容均未改变。
- 本次为历史补算，不维护当前智能股票池，不创建真实交易或投递记录。

## 历史与保留限制

一次有40秒查询预算的只读历史聚合检查，实际19.11秒，数据库写入0：
2023-01-03至2026-10-30共925交易日；低覆盖日期0、零行日期0、上市日期缺失0；
最低市场覆盖率SH 98.79%、SZ 98.95%、BSE 99.13%。
这是市场日覆盖摘要，不等于近三年每只股票逐根审计。

最近7交易日（10/22至10/30）逐股扫描：原始缺口140、已登记例外140、
未解释缺口0、异常0、候选股票0。未删除例外或将其伪造成价格。

**估值仍非全字段完整**：10/30表中5510行均为历史本地降级记录，未知PE/PB/总市值等保留缺失，
不能用10/31快照冒充10/30真实估值。因此 `full_recent_window_complete=false` 是预期的真实提示。
已存在的旧估值、北向口径及其他扩展数据问题不在本次采集恢复改动范围。

轻量 health 的深检缓存会因价格更新失效；上述历史结果来自本次独立只读扫描，
未将旧异常扫描缓存伪标为当前完整深检结果。

## 资源与剩余风险

续传名单查询0.065秒，进程RSS约141.9MiB（包含Python/Pandas导入）；
正式最多4个行情worker、4连接、提交窗口8、批次80，原生计算线程均限制为1。
只有一次协调恢复预算，不清空坏节点隔离、不增加后台任务或调度频率。

整条流程实测最大进程RSS约1.56GiB，swap 0；结构计算阶段抽样约0.96GiB。
这仍是现有因子窗口计算的短时峰值，**本轮未声称已解决全部计算内存问题**；
后续应单独设计分块计算与输入复用，不能通过增加并发换取速度。
数据库约5.4GiB、备份目录约5.6GiB、项目日志约168MiB；只保留既有一份自动备份。

外部源全不可用时仍会明确失败，不能承诺永不失败；预算用尽后保留缺失日期，由现有下一轮继续补。

## 证据

- 主运行：`backend/logs/daily_data_workflow_20261031_094031.log`。
- 无行情核验：`backend/logs/daily_data_workflow_20261031_094647.log`。
- 幂等复跑：`backend/logs/daily_data_workflow_20261031_094747.log`。
- 最新状态：`backend/logs/data_core_status/latest.json`。
