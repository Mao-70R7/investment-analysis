# 中欧财富公开采集 Skill

## 目标

采集中欧财富/中欧钱滚滚公开投顾策略列表、策略详情、日度业绩、当前基金级持仓、官方调仓记录和底层基金最新净值。

## 天眼标准入口

在 `程序代码` 目录执行：

```powershell
python -X utf8 .\节点脚本\_共享组件\生产程序\collect_official_apps_public.py --apps zocaifu --workers 8
```

该渠道尚未进入每日 DAG。执行前必须检查 `运行状态/locks/daily_update.lock`，执行后需先检查渠道 `coverage_check.json`，再决定是否隔离入库验证。

## 公开来源

- `/v1/fof/queryAdPageStrategyInfo`：策略清单。
- `/v2/product/detail`：详情及当前基金持仓。
- `/v1/fof/listDailyRiseAndFall`：分页日度业绩。
- `/v1/fof/queryFofNav`：策略净值。
- `/v1/product/queryFofRebalanceInfo`：历史调仓。
- `/v1/product/nav/page`：底层基金最新净值。

## 最新迁移批次

批次 `20260812T234257+0800` 已迁入天眼标准数据目录，但尚未写入主数据库：

- 策略：46。
- 日度业绩：52,166 行。
- 当前基金持仓：938 行，覆盖 44 个策略。
- 调仓事件：478 个。
- 调仓基金变化：9,308 行。
- 基金维度及最新净值：294 只，净值覆盖率 100%。

原始独立包位于：

```text
工具与归档/渠道采集独立验证/20260812/中欧基金投顾数据采集
```

## 口径

- `daily_return`、`cumulative_return` 是小数收益率，加载到主库时乘以 100 转为百分比。
- `fund_weight`、`before_weight`、`after_weight`、`weight_delta` 是百分数点，不得再次乘以 100。
- 历史调仓接口不返回基金代码时，只允许按唯一基金名称或主库基金别名解析；未解析记录保留为空。

天眼加载器已将收益率和权重转换拆开，避免中欧权重被放大 100 倍。

## 验收

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\zocaifu\outputs\coverage_check.json
```

最新批次状态为 `success_with_warning`。两个策略的官方详情返回空持仓，5,997 条历史调仓变化没有可确认的基金代码；这些缺口不能通过猜测填补。
