# 广发证券历史接口增量入库

节点ID：`gf_supplemental_load`

## 目标

只消费同一运行批次且通过门禁的 `gfsec_robot` 来源，在 `main_db_write` 资源锁内事务加载并核对策略数。未通过门禁时不删除、不覆盖旧库；广发银行历史数据不在日常运行中重载。

## 数据边界

- 贝塔牛推荐基金记录由加载器识别并跳过，不写为 `策略当前持仓`。
- 主库保留 `gfsec_robot` 来源 ID，页面和报表的业务渠道统一显示“广发证券”。
- 事务提交前逐 ID 核对目录及新增策略已写入主库，任何缺失都会回滚整个 `gfsec_robot` 渠道替换。

## 手工复跑

```powershell
& ".\00_每日数据更新并发布_唯一入口.bat" node gf_supplemental_load --run-id <run_id>
```
