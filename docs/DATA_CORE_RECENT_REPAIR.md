# 最近交易日自动补齐

## 入口与顺序

正式入口仍为 `backend/scripts/run_local_data_skill.py`，由现有 launchd 调度运行，
不增加第二套调度器。morning、daily、retry 自动启用 `--repair-recent`。
调度保持工作日 09:00 morning、09:30 health、15:30 daily、16:30 retry；
周六 09:15 深度 health。

1. 检查目标日、磁盘和唯一 workflow 锁，首次写库前创建/复用同目标日备份。
2. 按交易日历检查最近最多 7 个交易日的日期与股票/板块/ETF 覆盖，不能用最新日期推断连续性。
3. 先持久化待修复依赖，再按日期从旧到新执行需要的任务。
4. 股票质量门、板块合成和 RPS、强度、结构、生命周期、MRGC、择时/风控按依赖补算。
5. 低吸状态、C 监控和三组本地日报随上游修复更新；指数/ETF 失败独立告警。
6. 完成后再次读取同一规划器，清除已经核验的任务债务，保留失败和递延任务。

行情不变且结果覆盖完整时，不重新下载或重算；RPS 仅保存明确指定的日期，
仍读取算法所需历史种子。上游价格修订会使窗口内依赖失效，避免“日期有记录但内容过期”。
零条择时/风控/选股结果需有指定日期的成功审计，不能仅因输出表为空就视为未运行。

## mootdx 节点恢复

- 优先近期真实业务成功的具体 IP，不缓存 `auto:0`。
- 使用沪、深、北实际行情验证协议和内容，探针连接与业务连接隔离。
- 每轮最多尝试 6 个节点，启动总预算 30 秒；连接池共享该预算。
- 协议/解析错误销毁连接，坏节点进入退避隔离；业务请求最多换服重试一次。
- 成功节点状态至多每分钟持久化一次，避免逐股反复写小文件。
- 不全量扫描节点，不将异常响应伪装成停牌或可接受缺口。

## 历史安全边界

- 历史补算不刷新当前智能股票池、不修改真实 Portfolio、不创建或发送历史通知。
- 历史 Watch 只允许写日快照；旧前态需要核验版本、来源、价位和转移，无法核验则明确阻断。
- 整体复权换基只在核验一致后内存换算；旧状态确有修订时，允许从有界范围内的可信种子
  在内存逐日预热，只保存请求日期。找不到可信种子或中间资料缺失仍须阻断。
- 历史 C 监控和日报按所选日期解析/冻结成员，不能借用未来智能池成员。
- 历史估值优先保留真实来源；本地 K 线无法恢复的 PE/PB、总市值为 NULL，
  不能用今天的财务/股本补成历史真值。降级估值单列，不宣称完整。
- GET 接口只读；禁止请求页面时隐式重算。

## 时间与资源

morning/daily/retry 分别按 25/30/20 分钟预算在任务启动边界判断递延，
每个子任务另有超时；超时清理任务进程组，避免遗留采集进程。
BLAS/OMP 等原生线程限制为 1，依旧单写库，不全历史回放。
保留现有低占用备份策略，不因补算每个日期新增全库副本。

## 验收

轻量 health 新增 `repair_pending_dates`、`monitoring_ready`、`local_reports_ready`、
`full_recent_window_complete`、`valuation_degraded`。
`core_ok` 只说明当天核心链；不等同所有支撑、历史估值和报告均完备。
深度历史覆盖沿用周六审计，轻量巡检明确标识缓存是否过期。

测试入口包括 `test_repair_plan.py`、`test_daily_workflow_plan.py`、
`test_local_data_scheduler.py`、`test_mootdx_qfq.py`、`test_sparse_exact_dates.py`、
Watch/C/日报与估值相关回归。修复后应逐日核对核心表、MRGC 审计、47 股低吸范围、
C 的当日成员范围和本地日报，并对通知、当前池和 Portfolio 做修复前后内容摘要对账。

手动验收沿用正式入口，例如：

```bash
cd backend
/opt/anaconda3/bin/python3 scripts/run_local_data_skill.py --mode health
/opt/anaconda3/bin/python3 scripts/run_local_data_skill.py --mode retry --dry-run
```

显式历史补齐使用 `--target YYYY-MM-DD --skip-notifications`；目标日必须已收盘且在交易日历中。
`early/catchup` 保留原人工专项语义，不通过日常调度触发全量修复。
