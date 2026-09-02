# 官方 App 投顾策略公开采集

本目录按基金公司官方 App/官网入口分子目录保存复跑说明和标准化产出。统一采集入口：

```powershell
powershell -ExecutionPolicy Bypass -File .\official_apps\run_all.ps1
```

也可以只跑单个或多个 App：

```powershell
python .\scripts\collect_official_apps_public.py --apps huaxia_tougu,zocaifu,gffunds,gfsec_robot,gfbank_cgb --workers 8
```

每个 App 子目录的标准输出位置：

- `outputs/latest_summary.json`：本 App 最新采集摘要。
- `outputs/coverage_check.json`：关键实体覆盖校验。
- `outputs/source_inventory.json`：来源、接口、采集方法说明。
- `outputs/raw_manifest.json`：原始快照清单。
- `outputs/*.jsonl` / `outputs/*.csv`：标准实体明细。

统一实体：

- `strategy_master`：策略基础信息。
- `strategy_performance_daily`：日度业绩。
- `strategy_fund_snapshot`：基金级持仓快照。
- `strategy_rebalance_event`：调仓事件。
- `strategy_rebalance_fund_delta`：每次调仓的基金级调前/调后权重。
- `fund_public_dim`：基金公开维表。
- `app_public_entry`：App/官网公开入口。
- `strategy_disclosure_event`：公告/披露事件，主要用于只披露公告的渠道。

## 当前公开采集结论

| App/渠道 | 公开采集状态 | 策略数 | 日度业绩 | 当前持仓行 | 历史持仓快照 | 调仓事件 | 调仓基金明细 | 基金级穿透 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 华夏投顾/华夏财富查理智投 | success | 18 | 22,740 | 341 | 341 | 225 | 4,116 | 可公开穿透到基金代码、基金名称、调前/调后权重 |
| 中欧财富/中欧钱滚滚 | success_with_warning | 46 | 52,166 | 938 | 938 | 478 | 9,308 | 可公开穿透到基金权重；2 个策略公开详情无持仓，部分历史调仓仅给基金名 |
| 广发基金投顾 | success | 11 | 10,685 | 266 | 4,964 | 225 | 4,964 | 可公开穿透到每次调仓前后基金权重 |
| 广发证券易淘金/贝塔牛理财 | success_public_strategy_and_recommendation | 49 | 0 | 0 | 0（另有推荐基金清单 70 行） | 0 | 0 | 可公开获取策略主数据和货币/短债/场内货币推荐清单；推荐清单不等同于策略持仓 |
| 广发银行发现精彩 | success_public_entries_only | 0 | 0 | 0 | 0 | 0 | 0 | APK 可见基金、广发智投、定投专区、360 资产配置等入口；匿名 H5 未返回投顾策略主数据 |
| 嘉实财富 | success_public_disclosure_only | 21 | 0 | 0 | 0 | 0 | 0 | 公开站仅公告/PDF，仓位和业绩需登录会员中心或 App |
| 南方基金/司南投顾 | partial | 35 | 38,041 | 650（无单基金权重） | 650 | 0 | 0 | 匿名 H5 可取策略、业绩、基金代码名称和资产大类权重；精确基金权重及调仓需登录 |
| 招商基金/招财乐投顾 | success_public_selected_strategies | 4 | 0 | 0 | 0 | 0 | 0 | 官网 SSR 仅精选策略卡片，基金级数据未公开 |
| 易方达财富/e钱包 | success_public_strategy_categories | 4 | 0 | 0 | 0 | 0 | 0 | 公开页仅策略分类，具体产品和仓位需 App/交易端 |
| 富国基金/富钱包星投顾 | success_public_strategy_docs | 4 | 0 | 0 | 0 | 0 | 0 | 公开说明书披露资产类别仓位区间，未披露具体基金占比 |
| 汇添富基金/现金宝投顾 | success_public_fee_list | 8 | 0 | 0 | 0 | 0 | 0 | 公开帮助页仅策略名称和投顾费率 |
| 且慢/盈米基金 | success_spa_entry_only | 0 | 0 | 0 | 0 | 0 | 0 | 公开入口为 SPA/下载页，需登录态或授权接口继续穿透 |

## 重点口径

1. “当前持仓行”只统计最新一期基金级持仓；`strategy_fund_snapshot` 可保存历史持仓快照，因此历史快照行数可能大于当前持仓行。
2. “调仓基金明细”必须包含 `fund_code`、`fund_name`、`before_weight`、`after_weight`、`weight_delta`，否则不能认为已穿透到“每次调仓的基金占比”。
3. 公开页只能披露资产类别区间时，统一进入 `strategy_master.extra.asset_range_text`，不等同于基金级持仓。
4. 推荐基金清单可暂存到 `strategy_fund_snapshot` 以保留基金代码和收益字段，但必须用 `is_precise_weight=false`、`confidence_level=public_recommendation_list_not_holding` 标识，且 `coverage_check.fund_level_position_ok` 必须为 `false`。
5. 登录态、加密签名、App 私有接口未绕过；此类缺口统一写入 `known_gap` 和 `holding_penetration_status`。
6. 2026-08-12 中欧、南方批次已迁入标准原始/标准化目录，但尚未写入天眼主数据库；迁移完成不等于主库和页面已刷新。

## 入库与质量核对

公开采集完成后，按当前分析库模型加载官方 App 渠道：

```powershell
python -X utf8 scripts\load_analysis_zh_current_sqlite.py --keep-existing-db --channels gfsec_robot gfbank_cgb
python -X utf8 scripts\audit_official_app_channels_quality.py --channel-id gfsec_robot --channel-id gfbank_cgb
```

`gfsec_robot` 的推荐基金清单只保留在标准化文件和质量报告中，入库脚本会将 `public_recommendation_list_not_holding` 记录计入 `策略当前持仓_推荐清单跳过`，不写入 `策略当前持仓`。`gfbank_cgb` 当前只登记渠道和公开入口证据，匿名公开链路下策略主数据为 0 属于预期结果。
