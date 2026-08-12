# 广发证券财富管家采集

节点ID：`gfsec_fima_collect`

## 目标

从广发证券财富管家匿名公开接口发现全部有效产品实例，并按底层 `portfolioCode` 并发采集产品详情、官方当前模型基金仓位、官方累计收益曲线、区间业绩和调仓列表。

## 输入与输出

- 输入：运行布局、批次ID、`config/daily_update_policy.json` 中的每日曲线页大小。
- 输出：`data/normalized/gfsec_fima` 同批次标准化实体、`official_apps/gfsec_fima/outputs` 渠道输出，以及原始 HTTP 快照。
- 每个底层组合完成时输出结构化 `PROGRESS`，消息中必须包含计划数、已处理数、成功数和失败数。

## 业务口径

- 产品实例是策略主表粒度；相同底层组合的官方模型仓位映射到对应产品实例。
- `mainProducts` 是当前模型持仓；`alternativeProducts` 仅为备选基金，不得写入持仓。
- `configRatio` 转为百分比后入库；权重闭合需要落在 99.5%-100.5%。
- 接口未提供仓位生效日时，使用采集日作为持仓日期并保持披露日期为空。
- 当前仓位是官方策略模型配置，不是客户账户实际持仓。
- 调仓列表返回空代表已核验无事件，不允许依据当前仓位的 `actionName` 构造调仓。
- 每日重新扫描机构、策略和目标盈期次目录；只有单页返回数与官方总数闭合（或明确短页结束）才标记目录完整。
- 目录相对主库新增的产品实例必须在同批完成详情采集和标准化，并在摘要列出已采新增与遗漏 ID。

## 手工复跑

```powershell
& ".\00_每日数据更新并发布_唯一入口.bat" node gfsec_fima_collect --run-id <run_id>
```

## 历史仓位预览

历史原始快照可用节点内分析器加工为“官方当前模型仓位观测历史”和“配置变化候选”：

```powershell
python -X utf8 ".\节点脚本\03_广发证券\01_财富管家采集\src\gfsec_fima_position_history.py" `
  --workspace-root "<运行工作区根目录>"
```

- 默认读取运行布局的 `rawRoot/gfsec_fima/public_api`，只读主库基金日度净值，并输出到 `outputRoot/gfsec_fima_position_history/<时间戳>`。
- 精确权重只表示某次采集时官方当前模型接口展示的状态；源端未披露 `effectiveDate` 时不得用采集日伪造生效日。
- 成分变化需在后续观测中持续，才标为高置信窗口候选；同成分权重变化先经过无交易净值漂移模型解释。
- 所有差分快照固定 `eligible_for_official_rebalance_table=false`，不得写入正式调仓事件或客户实际持仓。
- `change_candidates.jsonl` 供人工复核；完整排除理由和漂移证据保留在 `transition_audit.jsonl`。
