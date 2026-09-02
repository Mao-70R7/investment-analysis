# 广发证券易淘金/财富管家公开采集

## 正式复跑

```powershell
& ".\00_每日数据更新并发布_唯一入口.bat" node gfsec_fima_collect --run-id <run_id>
```

验收和入库由每日 DAG 的 `gfsec_fima_gate`、`process_load` 节点完成。不得绕过门禁把不完整批次写入主库。

## 标准输出

- `official_apps/gfsec_fima/outputs/latest_summary.json`
- `official_apps/gfsec_fima/outputs/coverage_check.json`
- `official_apps/gfsec_fima/outputs/source_inventory.json`
- `data/normalized/gfsec_fima/<entity>/<date>/<run_id>.jsonl`

## 口径

- `gfsec_fima` 是财富管家基金投顾产品渠道，与 `gfsec_robot` 贝塔牛推荐策略渠道分开。
- 产品实例为策略主数据粒度，底层 `portfolioCode` 为接口处理粒度。
- `mainProducts` 是官方当前模型配置并含精确权重；`alternativeProducts` 是备选基金，不是持仓。
- 当前模型配置不是客户账户实际持仓。
- 调仓列表逐组合核验；接口返回 0 条时保留明确状态，不生成虚假事件或明细。
- 当前公开接口不依赖登录态。
