# 易方达财富公开采集 Skill

## 目标

采集易方达财富/e钱包公开投顾策略入口，标准化公开策略分类，并记录基金级持仓、业绩、调仓明细未公开的缺口。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps efundcf
```

## 公开来源

- `https://www.efundcf.com.cn/lm/tgfw/tgcl/`

## 标准输出

- `official_apps/efundcf/outputs/strategy_master.csv`
- `official_apps/efundcf/outputs/app_public_entry.csv`
- `official_apps/efundcf/outputs/latest_summary.json`
- `official_apps/efundcf/outputs/coverage_check.json`
- `official_apps/efundcf/outputs/source_inventory.json`
- `official_apps/efundcf/outputs/raw_manifest.json`

## 当前结果

最近一次成功运行：`20260524T095254+0800`。
- 公开策略分类：4 个，分别为资产增值、现金管理、养老规划、子女教育。
- 当前基金级持仓：0 行。
- 调仓事件：0 个。
- 调仓基金级明细：0 行。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\efundcf\outputs\coverage_check.json
```

`strategy_master_ok` 应为 `true`，`holding_penetration_status` 应为 `blocked_app_or_login_required`。

## 已知口径

公开官网只展示投顾策略分类入口。具体投顾产品、基金级仓位、日度业绩和每次调仓基金占比需要进入 e 钱包/交易端。
