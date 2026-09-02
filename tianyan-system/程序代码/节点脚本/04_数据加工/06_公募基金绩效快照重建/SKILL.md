# 公募基金绩效快照重建

节点ID：`public_fund_snapshot`  
执行入口：`run.ps1`  
超时：`3600` 秒  
资源锁：`main_db_write`

## 目标

在策略治理完成后，使用主库最新基金净值、风险指标和基准解析结果，原子重建 `公募基金产品绩效快照`，为报表构建和项目稽核提供唯一一致的数据源。

## 输入

- 已通过的 `strategy_governance` 节点结果。
- 运行工作区主库 `analysis_zh_current.sqlite`。

## 输出

- 主库表 `公募基金产品绩效快照`。
- 节点目录下的 `out/<时间>/snapshot.json.gz` 与 `summary.json`；快照附件使用 GZIP 压缩，避免日志目录被大体积明文 JSON 持续占用。
- 原子 `node_result.json`，包含基金总数、净值/收益/风险/基准覆盖计数和产物哈希。

## 业务口径

- 必须包含 `基金代码`、`基准风险资产权重`、`基准风险资产权重来源`。
- 空结果、缺必需字段、产物无法完整写盘或落库后行数不一致时均阻断报表构建。
- 先写入并验证短路径产物，再开启主库写事务；产物失败不得替换旧表。
- 节点独立持有 `main_db_write` 锁，可在策略治理成功后单独恢复，避免重复执行整套治理。

## 失败影响

关键节点失败将阻断报表、最终稽核、备份和发布，不允许用旧快照继续下游。

## 手工复跑

```powershell
& ".\00_每日数据更新并发布_唯一入口.bat" resume <run_id> --from-node public_fund_snapshot --to-node database_backup
```

## 验证方法

核对节点 `validation.status=passed`、`fundCount` 与主库表行数一致、两项基准风险权重字段存在，且 GZIP 快照与 JSON 摘要均有有效哈希。
