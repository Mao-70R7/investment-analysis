# 广发证券易淘金/贝塔牛理财公开采集

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps gfsec_robot --workers 4
```

## 标准输出

- `official_apps/gfsec_robot/outputs/strategy_master.csv`
- `official_apps/gfsec_robot/outputs/strategy_fund_snapshot.csv`
- `official_apps/gfsec_robot/outputs/fund_public_dim.csv`
- `official_apps/gfsec_robot/outputs/app_public_entry.csv`
- `official_apps/gfsec_robot/outputs/latest_summary.json`
- `official_apps/gfsec_robot/outputs/coverage_check.json`
- `official_apps/gfsec_robot/outputs/source_inventory.json`
- `official_apps/gfsec_robot/outputs/raw_manifest.json`

## 口径

匿名 `robot.gf.com.cn` 接口可获取策略主数据和货币、短债、场内货币推荐清单。推荐清单不是策略精确持仓，`coverage_check.fund_level_position_ok` 应为 `false`，`recommendation_fund_list_ok` 应为 `true`。

## 入库核对

```powershell
python -X utf8 scripts\load_analysis_zh_current_sqlite.py --keep-existing-db --channels gfsec_robot
python -X utf8 scripts\audit_official_app_channels_quality.py --channel-id gfsec_robot
```

入库后应看到 `策略信息=49`，`策略当前持仓_推荐清单跳过=70`，`策略当前持仓=0`。质量报告中 `strategy_fund_snapshot` 应显示为“推荐清单”，不是“不一致”。
