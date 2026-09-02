# 富国基金公开采集 Skill

## 目标

采集富国基金/富钱包星投顾公开入口和策略说明书，标准化组合名称、投资目标、业绩基准、风险等级、资产类别仓位区间和投顾服务费率。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps fullgoal
```

## 公开来源

- `https://www.fullgoal.com.cn/mobile/tougu/`
- `https://www.fullgoal.com.cn/mobile/tougu/shouhuxing.html`
- `https://www.fullgoal.com.cn/mobile/tougu/qimingxing.html`
- `https://www.fullgoal.com.cn/mobile/tougu/shuangzixing.html`
- `https://www.fullgoal.com.cn/mobile/tougu/mantianxing.html`

## 标准输出

- `official_apps/fullgoal/outputs/strategy_master.csv`
- `official_apps/fullgoal/outputs/app_public_entry.csv`
- `official_apps/fullgoal/outputs/latest_summary.json`
- `official_apps/fullgoal/outputs/coverage_check.json`
- `official_apps/fullgoal/outputs/source_inventory.json`
- `official_apps/fullgoal/outputs/raw_manifest.json`

## 当前结果

最近一次成功运行：`20260524T094147+0800`。
- 公开策略说明书：4 份，分别为守护星、启明星、双子星、满天星。
- 公开字段：组合名称、投资目标、业绩基准、风险等级、资产类别仓位区间、投顾服务费率。
- 当前基金级持仓：0 行。
- 调仓事件：0 个。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\fullgoal\outputs\coverage_check.json
```

`strategy_master_ok` 应为 `true`，`holding_penetration_status` 应为 `blocked_app_or_login_required`。

## 已知口径

公开策略说明书只披露资产类别仓位区间，例如权益类基金、固定收益类基金和货币基金区间；未披露具体基金占比、每次调仓基金明细和日度业绩。
