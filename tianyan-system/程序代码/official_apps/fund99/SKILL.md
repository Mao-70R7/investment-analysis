# 汇添富基金公开采集 Skill

## 目标

采集汇添富基金/现金宝公开投顾服务说明页，标准化策略名称和投顾服务费率，并记录未公开持仓、业绩、调仓明细的缺口。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps fund99
```

## 公开来源

- `https://qy.99fund.com/info/investment_adviser.htm`

## 标准输出

- `official_apps/fund99/outputs/strategy_master.csv`
- `official_apps/fund99/outputs/app_public_entry.csv`
- `official_apps/fund99/outputs/latest_summary.json`
- `official_apps/fund99/outputs/coverage_check.json`
- `official_apps/fund99/outputs/source_inventory.json`
- `official_apps/fund99/outputs/raw_manifest.json`

## 当前结果

最近一次成功运行：`20260524T094042+0800`。
- 公开费率策略：8 个，包含理财佳、稳稳小确幸、跟我投、添富教育和添富养老系列。
- 当前基金级持仓：0 行。
- 调仓事件：0 个。
- 调仓基金级明细：0 行。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\fund99\outputs\coverage_check.json
```

`strategy_master_ok` 应为 `true`，`holding_penetration_status` 应为 `blocked_app_or_login_required`。

## 已知口径

公开帮助页只披露投顾服务费率。页面说明投顾资产和交易记录需要在“我的投顾”登录态查看，基金级持仓、每次调仓和业绩明细未公开。
