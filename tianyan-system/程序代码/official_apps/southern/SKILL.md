# 南方基金公开采集 Skill

## 目标

采集南方基金/司南投顾公开入口、登录跳转和可公开落地页信息，并记录登录后数据缺口。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps southern
```

## 公开来源

- `https://www.nffund.com/new/snzt/index.html`
- 登录后 `iainvest` 交易系统入口。

## 标准输出

- `official_apps/southern/outputs/app_public_entry.csv`
- `official_apps/southern/outputs/latest_summary.json`
- `official_apps/southern/outputs/coverage_check.json`
- `official_apps/southern/outputs/source_inventory.json`
- `official_apps/southern/outputs/raw_manifest.json`

## 当前结果

最近一次成功运行：`20260523T224046+0800`。

- 公开入口：成功。
- 登录链接：1 个。
- 公开策略：0 个。
- 当前基金级持仓：0 行。
- 调仓事件：0 个。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\southern\outputs\coverage_check.json
```

`collection_status` 应为 `success_landing_only`，`holding_penetration_status` 应为 `blocked_login_required`。

## 已知口径

公开页面是司南投顾入口；策略列表、仓位、业绩和调仓在登录后的 `iainvest` 交易系统内。当前公开采集不使用个人登录态，不做登录后交易系统抓取。
