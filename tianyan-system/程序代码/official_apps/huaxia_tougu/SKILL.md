# 华夏投顾公开采集 Skill

## 目标

采集华夏投顾/华夏财富查理智投公开策略页、日度收益、当前持仓和每次调仓的基金级调前/调后权重。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps huaxia_tougu
```

## 公开来源

- `https://www.amcfortune.com/superfund/fundList.shtml`
- `https://www.amcfortune.com/funds/superfund/{code}/index.shtml`
- `https://www.amcfortune.com/hxcf/cf/sfNavPage`
- `https://www.amcfortune.com/hxcf/cf/sfAdjustVersion`

## 标准输出

- `official_apps/huaxia_tougu/outputs/strategy_master.csv`
- `official_apps/huaxia_tougu/outputs/strategy_performance_daily.csv`
- `official_apps/huaxia_tougu/outputs/strategy_fund_snapshot.csv`
- `official_apps/huaxia_tougu/outputs/strategy_rebalance_event.csv`
- `official_apps/huaxia_tougu/outputs/strategy_rebalance_fund_delta.csv`
- `official_apps/huaxia_tougu/outputs/fund_public_dim.csv`
- `official_apps/huaxia_tougu/outputs/latest_summary.json`
- `official_apps/huaxia_tougu/outputs/coverage_check.json`

## 当前结果

最近一次成功运行：`20260523T233343+0800`。

- 策略：18 个。
- 日度业绩：22740 行。
- 当前基金级持仓：341 行。
- 调仓事件：225 个。
- 调仓基金级明细：4116 行。
- 基金维表：216 只。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\huaxia_tougu\outputs\coverage_check.json
```

`fund_level_position_ok`、`rebalance_event_ok`、`rebalance_fund_delta_ok` 均应为 `true`。`strategy_rebalance_fund_delta.csv` 中应包含 `fund_code`、`fund_name`、`before_weight`、`after_weight`、`weight_delta`。

## 已知口径

公开页面给出调仓前后基金代码、基金名称和权重。未见单独组合净值字段；历史接口返回累计收益和日涨跌幅。
