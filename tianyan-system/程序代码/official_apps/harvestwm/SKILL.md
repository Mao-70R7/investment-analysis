# 嘉实财富公开采集 Skill

## 目标

采集嘉实财富官网投顾服务页、投顾相关公告、组合方案说明书 PDF/图片附件和风险等级变更提示。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps harvestwm
```

快速测试可限制公告页数：

```powershell
python .\scripts\collect_official_apps_public.py --apps harvestwm --harvest-pages 3
```

## 公开来源

- `https://www.harvestwm.cn/product/customize_acco`
- `https://www.harvestwm.cn/about/notice?page={page}`
- `https://www.harvestwm.cn/about/notice/{notice_id}`

## 标准输出

- `official_apps/harvestwm/outputs/strategy_master.csv`
- `official_apps/harvestwm/outputs/strategy_disclosure_event.csv`
- `official_apps/harvestwm/outputs/latest_summary.json`
- `official_apps/harvestwm/outputs/coverage_check.json`
- `official_apps/harvestwm/outputs/raw_manifest.json`

## 当前结果

最近一次成功运行：`20260523T224046+0800`。

- 公开识别策略：21 个。
- 投顾相关公告/披露事件：363 条。
- 当前基金级持仓：0 行。
- 调仓事件：0 个。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\harvestwm\outputs\coverage_check.json
```

`strategy_master_ok` 应为 `true`，`strategy_disclosure_event` 应有记录。基金级仓位、业绩和调仓字段预期为 `false`，原因是公开官网不披露这些结构化数据。

## 已知口径

公开官网只披露投顾服务入口、公告和方案说明书附件。公告正文提示部分产品信息需登录会员中心或嘉实财富 App 查看，因此公开采集无法穿透到基金级仓位和每次调仓。
