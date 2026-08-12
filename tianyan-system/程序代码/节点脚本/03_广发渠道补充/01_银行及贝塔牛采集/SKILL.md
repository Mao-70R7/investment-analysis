# 广发证券历史接口补充采集

节点ID：`gf_supplemental_collect`

## 目标

采集 `gfsec_robot` 匿名公开策略数据。`gfbank_cgb` 因无法稳定取得完整业绩、基准和真实曲线，已退出每日生产清单；历史文件和数据库记录保留，不在本节点刷新。

## 数据边界

- 贝塔牛公开推荐基金清单不是精确持仓，不写入主库持仓表。
- `gfsec_robot` 是来源追溯 ID，业务展示渠道统一归为“广发证券”，不与 `gfsec_fima` 拆成两个业务渠道。
- 广发银行如需恢复，必须先完成独立探测和完整性验收，再显式调整生产配置。
- `gfsec_robot` 每日重扫 `gfit/gfit2` 正常及全量四个目录；每个目录都要按官方总数闭合，并将库外新增 ID 纳入同批详情采集和标准化。

## 手工复跑

```powershell
& ".\00_每日数据更新并发布_唯一入口.bat" node gf_supplemental_collect --run-id <run_id>
```
