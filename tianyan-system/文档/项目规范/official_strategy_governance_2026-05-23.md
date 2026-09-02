# 官方投顾策略数据治理记录（2026-05-23）

## 处理范围

本轮治理重点覆盖已公开抓到基金级仓位和调仓明细的两个渠道：

- `huaxia_tougu`：华夏投顾/华夏财富查理智投
- `zocaifu`：中欧财富/中欧钱滚滚

同时重建 `data/analysis_zh_current.sqlite` 的核心中文分析层表，保留库内已有基金净值、模拟净值等非核心加载表。

## 口径修正

- `huaxia_tougu` 纳入 `scripts/load_analysis_zh_current_sqlite.py` 默认加载渠道。
- `huaxia_tougu` 和 `zocaifu` 的收益字段统一按“小数收益率”入库，分析层字段统一转换为百分比。
- 官方已披露的 `daily_return` 不再被净值链反推值覆盖；净值链一致性只作为检查口径。
- 没有官方单位净值时，用 `1 + 累计收益率` 补齐分析层 `单位净值`，用于后续回撤等加工字段。
- 华夏 `1,000元起投` 这类带逗号金额按文本重新解析，避免误入为 `1`。
- 华夏和中欧按“最新完整采集批次”加载，避免同日多次重采的旧批次残留混入当前官方快照。

## 最新入库结果

| 渠道 | 策略 | 日度业绩 | 当前持仓 | 调仓事件 | 调仓明细 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 华夏投顾 | 18 | 22740 | 341 | 225 | 4116 |
| 中欧财富 | 49 | 49959 | 906 | 467 | 9019 |

全库核心表当前按渠道统计：

| 表 | gffunds | huaxia_tougu | ttfund | zocaifu |
| --- | ---: | ---: | ---: | ---: |
| 策略信息 | 66 | 18 | 272 | 49 |
| 策略日度业绩 | 39365 | 22740 | 544 | 49959 |
| 策略当前持仓 | 1137 | 341 | 4730 | 906 |
| 策略调仓事件 | 584 | 225 | 2442 | 467 |
| 策略调仓明细 | 12183 | 4116 | 47469 | 9019 |

## 审计结果

审计脚本：

```powershell
python .\scripts\audit_official_strategy_governance.py --fail-on-error
```

最新报告：

- `outputs/official_strategy_governance/latest_governance_report.md`
- `outputs/official_strategy_governance/latest_governance_report.json`
- `outputs/official_strategy_governance/latest_governance_checks.csv`

检查结果：53 项通过、2 项警告、0 项失败。

两项警告：

- 华夏：`SF1021` 策略详情 HTML 原始快照仍为 `partial`，但标准化后的策略、收益、持仓和调仓实体均已落库并通过口径核对。
- 中欧：5973 条历史调仓源接口未直接给出基金代码，这是源数据限制；加载器已用本地基金维表/持仓/别名映射补码，入库后调仓明细基金代码缺失为 0。

## 复跑顺序

```powershell
python .\scripts\collect_official_apps_public.py --apps huaxia_tougu
python .\scripts\load_analysis_zh_current_sqlite.py --keep-existing-db
python .\scripts\audit_official_strategy_governance.py --fail-on-error
```
