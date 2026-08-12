# 广发证券历史接口批次验收

节点ID：`gf_supplemental_gate`

## 目标

验证 `gfsec_robot` 精确批次来源、策略库存留存、最低策略数和业绩数据；未通过时保留旧库。广发银行不再进入每日门禁。

## 口径

- `gfsec_robot` 需要策略主档、日度/区间业绩和推荐清单，但推荐清单仍明确为非持仓。
- `gfsec_robot` 与 `gfsec_fima` 只在来源血缘上区分，业务渠道统一为“广发证券”。
- 四个策略目录必须全部完整，且目录 ID 与当批策略主表闭环；新 ID 未采到详情时拒绝当批入库并保留旧库。

## 手工复跑

```powershell
& ".\00_每日数据更新并发布_唯一入口.bat" node gf_supplemental_gate --run-id <run_id>
```
