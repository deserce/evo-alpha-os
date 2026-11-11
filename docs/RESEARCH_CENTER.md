# Research 研报中心与每日更新

2026-10-25：一期接入与每天 09:00 增量更新已交付。

## 数据与边界

- 独立库 `backend/data/research.db`，可用 `EVOALPHA_RESEARCH_DB` 覆盖；不写行情库。
- 来源为东方财富公司/行业研报元数据；PDF与原文仅外链，查询不调用采集或模型。
- 原 `research-reports` 项目、凭证及后台服务未修改；新服务不依赖其运行。
- 源项目 qType 标签写反已核验：0=公司、1=行业。迁移保留原始标签；异常身份留待分类。
- 原有个股 enrichment 的旧研报接口保留兼容；新页面共用独立库。资料刷新只更新资料模块。

## 每天 09:00 更新

独立 LaunchAgent `com.evoalpha.research.daily` 使用北京时间；安装器校验 macOS 本机时区。
每天09:00运行（包含周末、节假日），RunAtLoad支持登录后补跑；09:00前和同日已成功均跳过。
执行结束退出，不保持常驻，不使用caffeinate。下个计划运行：2026-10-26 09:00。

`research.update_reports` 直接调用来源 API：双类型完整分页后推进游标，正常回看两天，
停机后按最长七天窗口连续补齐。页数超限自动缩小窗口；单日仍超限则显式失败。
失败保留成功窗口进度且不更新整轮成功时间。首次部分写入后失败，从失败窗口重试，
不会误用部分数据的最大日期跳过缺口。启动时持锁销账上轮中断记录。

手动更新（从backend目录执行）：

```bash
/opt/anaconda3/bin/python3 -m research.update_reports
/opt/anaconda3/bin/python3 -m research.update_reports --as-of-date 2026-10-25
/opt/anaconda3/bin/python3 scripts/install_research_launchd.py status
/opt/anaconda3/bin/python3 scripts/install_research_launchd.py install
```

历史日期命令仅控制本轮抓取上限，不删除日期以后的既有研报。
不应将它用于宣称从2020年开始的源库历史全量完整性。
未完成任务在下一次计划运行或手动重试继续，不设置高频后台重试。

## 查询与页面

`GET /api/v1/research/reports`：标题/公司检索、股票代码、类型、机构、行业、日期及分页。
`GET /api/v1/research/reports/{source:id}`：共用详情。
`GET /api/v1/research/facets`：筛选项、最新发布日期、同步状态及最近成功时间。
GET仅只读；缺库显示未导入，坏库返回503；列表上限100条，单次SQL预算3秒。

Research展示更新计划、最近尝试、最近成功、覆盖截至及失败状态。
“刷新列表”只重新读取缓存库。页面重新进入与窗口回焦时检查30秒过期缓存，无轮询。
个股最新行情视图显示最新研报（包含周末发布），历史日期默认严格截止；
用户可切换“最新研报/截至行情日”。从研究页明确指定截止日的跳转保留截止口径。
公司与行业分开，行业关系来自公司研报的来源分类，不当作公司覆盖。

## 资源与保护

- 更新、迁移、回滚共用非阻塞文件锁，禁止多个研究写入任务并发。
- 单线程串行请求，间隔至少0.4秒，连接/读取超时5/15秒，最多2次；4xx非限流不重试。
- 单次响应最多4MiB；每窗口默认100页，最大可配置1000页；正常七天一窗。
- 单轮总预算600秒，调度wrapper超时TERM后最多5秒强制回收整个子进程组。
- SQLite缓存4MiB；连接及时关闭；更新审计保留约365次。
- 日志 `~/Library/Logs/EvoAlpha/research-daily.{out,err}.log`，20MiB轮换，最多一份尾部归档。
- 原生计算线程上限1；无新增LLM/行情/RPS/MRGC/邮件调用。
- 本次更新前备份 `backend/data/backups/research-before-daily-20261025.db`，quick_check=ok，227979条。

## 实测与验收

存量导入批次 `b8dd60d56b3e4da8a613b01c4a266c17`：227979条，13.39秒，峰值RSS约25MiB。
2026-10-25真实增量批次 `e69e0ef0dc0845299f55826c9ddaf414`：扫描370、新增230、更新140，
SUCCESS，覆盖至2026-10-25；总228209条，最新发布日期2026-10-25。
实际耗时1.79秒、峰值RSS约46MiB，无swap；库约231.1MiB，更新结束WAL=0。
原项目只在工作日更新；本次实测周末也有新研报，已扩展为每日09:00。
调度安装后真实spawn，配置/时区健康；退出后无活动更新进程。
空闲前端RSS约61MiB/20线程、后端约49MiB/7线程、CPU均0%，研究库连接句柄均0。
这些是本次短时实测，不替代长期运行观察。

相关后端72项、前端18项测试通过，生产构建与目标ESLint/TypeScript通过。
覆盖分页失败、长停机、迟到数据、幂等、锁竞争、超时与进程回收、错误状态、日期隔离。
生产浏览器核对新增研报两入口一致、日期切换不残留、筛选返回与刷新列表。
截图位于 `docs/verification/research-daily-20261025.png`。

存量迁移命令：

```bash
/opt/anaconda3/bin/python3 -m research.import_reports --source '/Users/deserce/Documents/项目/research-reports/data/report.db'
```

迁移仅补新增来源ID、重复导入不覆盖更新器已经修正的记录；CLI返回审计批次ID。
`--rollback-batch ID` 只回滚该批次首次引入记录，执行前先核对批次与备份；
每日采集的更新不应直接用存量迁移回滚替代恢复。

最终生产BUILD_ID：`cCPtb9IYnoyDJOrXQfDG2`。每日调度真实重跑批次
`369beb49fb2c429fad7c8fca9bf00f5f`扫描105、0新增/0更新、SUCCESS、退出码0，
验证重叠窗口幂等。最终页面显示每天09:00；旧同级重复key导致的DOM残留已修复，
浏览器实测分页后1份表单/1份列表，最新模式包含2026-10-25、历史模式排除该日。
页面HTML约52ms、静态JS约6ms、个股研报API约21ms（本轮热读）。
仅保留当前生产包与两份旧生产回滚包，清理已确认无活动句柄的生成包约146MiB。
建议将本次研究模块、测试及AGENTS规则单独提交，避免混入并行的其它模块改动。
