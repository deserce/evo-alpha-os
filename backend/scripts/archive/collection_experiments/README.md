# Collection Experiments

这里是历史采集实验脚本归档区。

这些脚本可能仍然能运行，但它们不再代表当前生产链路，尤其不要用它们直接写入
`stock_daily_prices`，避免和 mootdx qfq 主入口产生口径冲突。

生产入口请使用：

```bash
cd backend
python3 scripts/run_daily_data_workflow.py
```
