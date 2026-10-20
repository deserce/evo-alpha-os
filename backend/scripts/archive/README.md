# Archived Scripts

这些脚本不再作为生产入口使用，只保留为历史参考或手动排查材料。

当前唯一生产入口：

```bash
cd backend
python3 scripts/run_daily_data_workflow.py
```

定时调度入口：

```bash
cd backend
python3 scripts/schedule_daily_data_workflow.py
```

归档原则：

- `collection_experiments/`：早期批量采集、快速初始化、多线程实验脚本。
- 生产 K 线统一走 `scripts/standalone/collect_stock_kline_mootdx_qfq.py`。
- 新增采集任务优先挂到 `scripts/run_daily_data_workflow.py`，不要再新增 `quick_*`、`fast_*`、`simple_*` 入口。
