# 广发基金公开采集 Skill

## 目标

采集广发基金投顾公开 H5/API，穿透到策略信息、日度业绩、历史持仓快照、每次调仓事件以及调仓前后基金级权重。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps gffunds --workers 8 --gffunds-skip-fund-nav --gffunds-skip-protocol-pdf
```

如需补充基金最新净值和策略协议 PDF，可去掉两个 `--gffunds-skip-*` 参数。

## 公开来源

- `https://gfwx.gffunds.com.cn/html5app/invest-advisor`
- `https://gfwx.gffunds.com.cn/mapi/get_invest_advisor_config`
- `https://gfwx.gffunds.com.cn/mapi/get_investadvisor_adjustment_record`
- `https://gfwx.gffunds.com.cn/mapi/get_investadvisor_yield_trend`

## 标准输出

- `official_apps/gffunds/outputs/strategy_master.csv`
- `official_apps/gffunds/outputs/strategy_performance_daily.csv`
- `official_apps/gffunds/outputs/strategy_fund_snapshot.csv`
- `official_apps/gffunds/outputs/strategy_rebalance_event.csv`
- `official_apps/gffunds/outputs/strategy_rebalance_fund_delta.csv`
- `official_apps/gffunds/outputs/fund_public_dim.csv`
- `official_apps/gffunds/outputs/latest_summary.json`
- `official_apps/gffunds/outputs/coverage_check.json`

## 当前结果

最近一次成功运行：`20260524T094403+0800`。
- 策略：11 个。
- 日度业绩：10,685 行。
- 最新一期基金级持仓：266 行。
- 历史持仓快照：4,964 行。
- 调仓事件：225 个。
- 调仓基金级明细：4,964 行。
- 基金维表：612 只。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\gffunds\outputs\coverage_check.json
```

`fund_level_position_ok`、`rebalance_event_ok`、`rebalance_fund_delta_ok` 均应为 `true`。`strategy_rebalance_fund_delta.csv` 应包含 `fund_code`、`fund_name`、`before_weight`、`after_weight`、`weight_delta`。

## 已知口径

`strategy_fund_snapshot` 保存历史持仓快照，`latest_summary.json` 中的 `current_holding_rows` 按最新一期持仓口径统计。本次快速复跑跳过基金最新净值和协议 PDF，不影响调仓前后基金权重。
