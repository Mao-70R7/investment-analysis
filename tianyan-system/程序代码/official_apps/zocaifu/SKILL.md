# 中欧财富公开采集 Skill

## 目标

采集中欧财富/中欧钱滚滚公开投顾策略列表、策略详情、日度业绩、当前基金级持仓和官方调仓记录。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps zocaifu --workers 8
```

如只做快速结构测试，可跳过基金净值补充：

```powershell
python .\scripts\collect_official_apps_public.py --apps zocaifu --workers 8 --zocaifu-skip-fund-nav
```

## 公开来源

- `https://mobile.qiangungun.com/v2/fof/list`
- `https://mobile.qiangungun.com/v2/product/detail`
- `https://mobile.qiangungun.com/v1/product/queryFofRebalanceInfo`
- `https://mobile.qiangungun.com/v1/fof/listDailyRiseAndFall`

## 标准输出

- `official_apps/zocaifu/outputs/strategy_master.csv`
- `official_apps/zocaifu/outputs/strategy_performance_daily.csv`
- `official_apps/zocaifu/outputs/strategy_fund_snapshot.csv`
- `official_apps/zocaifu/outputs/strategy_rebalance_event.csv`
- `official_apps/zocaifu/outputs/strategy_rebalance_fund_delta.csv`
- `official_apps/zocaifu/outputs/fund_public_dim.csv`
- `official_apps/zocaifu/outputs/latest_summary.json`
- `official_apps/zocaifu/outputs/coverage_check.json`

## 当前结果

最近一次成功运行：`20260523T223717+0800`。

- 策略：49 个。
- 日度业绩：49959 行。
- 当前基金级持仓：906 行。
- 调仓事件：467 个。
- 调仓基金级明细：9019 行。
- 基金维表：278 只。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\zocaifu\outputs\coverage_check.json
```

`fund_level_position_ok`、`rebalance_event_ok`、`rebalance_fund_delta_ok` 均应为 `true`。当前持仓表有 `fund_code`、`fund_name`、`fund_weight`；调仓明细表有 `fund_name`、`before_weight`、`after_weight`、`weight_delta`。

## 已知口径

公开 API 可获取基金级当前持仓和官方调仓权重。部分历史调仓接口只返回基金名称和权重，不返回基金代码；不对缺失代码做猜测补全。
