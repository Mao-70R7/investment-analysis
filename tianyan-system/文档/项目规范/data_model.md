# 数据监测结构模型

## 采集分层

### L0 原始层

保留每次采集的原始文件：

- HTML：网页源代码。
- JSON：接口响应。
- Screenshot：页面截图，用于登录态或前端渲染校验。
- HAR/metadata：接口 URL、请求方法、状态码、抓取时间、登录状态。

路径建议：

```text
data/raw/{channel}/{collector}/{yyyy-mm-dd}/{run_id}/
```

示例：

```text
data/raw/southern/public_index/2026-05-14/20260514T101500/
data/raw/southern/auth_strategy/2026-05-14/20260514T101500/
```

### L1 标准快照层

每次采集后抽取为统一 JSONL：

```text
data/normalized/{channel}/{entity}/{yyyy-mm-dd}/{run_id}.jsonl
```

实体包括：

- `strategy_master`
- `strategy_performance_daily`
- `strategy_fund_snapshot`
- `strategy_rebalance_event`
- `strategy_rebalance_fund_delta`
- `fund_public_dim`

说明：

- `run_id` 是一次采集批次，避免同一天多次运行互相覆盖。
- 每行应带 `channel_id`、`run_id` 和可追溯的 `source_snapshot_id` 或来源字段。
- 标准层只做字段抽取和轻量规范化，不覆盖原始响应。

### L2 数据库层

数据库建议先用 SQLite 验证，稳定后迁移到 PostgreSQL。

核心约束：

- 同一渠道、同一策略、同一日期只能有一个“当前有效”的持仓快照版本。
- 原始采集可多版本保存，标准层通过 `snapshot_id` 追踪来源。
- 单基金权重字段允许为空；缺失不补猜。

## 核心表

### `channel`

渠道维表。

字段：

- `channel_id`：如 `southern`。
- `channel_name`：如 `南方基金/司南投顾`。
- `provider_type`：`fund_company`、`wealth_subsidiary`、`third_party`。
- `official_site_url`
- `login_required_level`：`none`、`partial`、`required`。

### `strategy_master`

组合基础信息。

字段：

- `channel_id`
- `source_strategy_id`：渠道内策略 ID，没有则用稳定 slug。
- `strategy_name`
- `advisor_name`
- `strategy_type`
- `risk_level`
- `launch_date`
- `suggested_holding_period`
- `minimum_amount`
- `advisory_fee_rate`
- `benchmark`
- `tags`
- `strategy_description`
- `status`
- `source_url`
- `first_seen_at`
- `last_seen_at`

### `strategy_performance_daily`

组合日度业绩。

字段：

- `channel_id`
- `source_strategy_id`
- `trade_date`
- `nav`
- `daily_return`
- `cumulative_return`
- `benchmark_return`
- `index_return`
- `max_drawdown`
- `source_snapshot_id`

中欧财富当前口径：

- `daily_return` 来自 `listDailyRiseAndFall.dailyRate`，为小数收益率，例如 `0.0112` 表示约 `1.12%`。
- `cumulative_return` 来自 `listDailyRiseAndFall.totalRate`，为小数累计收益率，例如 `1.4615` 表示约 `146.15%`。
- `nav` 来自 `queryFofNav.nav`。
- `benchmark_return` / `index_return` 若接口未返回则留空，不用基准描述硬猜。

### `strategy_fund_snapshot`

基金级持仓快照，工程的核心事实表。

字段：

- `snapshot_id`
- `channel_id`
- `source_strategy_id`
- `position_date`
- `disclosure_date`
- `fund_code`
- `fund_name`
- `fund_asset_type`
- `fund_group_name`
- `fund_weight`
- `fund_nav`
- `fund_nav_date`
- `is_precise_weight`
- `is_login_required`
- `source_url`
- `raw_record_hash`

扩展字段建议：

- `internal_product_id`：渠道内部底层基金产品 ID，用于补取净值。
- `latest_fund_daily_rate`：底层基金最新日涨幅。

说明：

- `position_date` 是平台口径的仓位日期。
- `disclosure_date` 是页面或接口披露日期。
- `fund_weight` 是组合内单基金占比。拿不到精确值时不填。
- `is_precise_weight=false` 可用于保存只知道分组、顺序或粗略范围的记录。

### `strategy_rebalance_event`

官方调仓事件主表。

字段：

- `rebalance_event_id`
- `channel_id`
- `source_strategy_id`
- `rebalance_date`
- `previous_position_date`
- `new_position_date`
- `disclosure_date`
- `event_title`
- `event_reason`
- `source_url`
- `source_snapshot_id`

说明：

- 若渠道没有显式上一仓位日，`previous_position_date` 可以由上一条官方调仓日期推断，但必须增加 `previous_position_date_is_inferred=true`。
- 官方调仓优先保留原始调仓说明，不做摘要覆盖。

### `strategy_rebalance_fund_delta`

基金级调仓变化明细。

字段：

- `rebalance_event_id`
- `fund_code`
- `fund_name`
- `before_weight`
- `after_weight`
- `weight_delta`
- `action_type`：`buy`、`sell`、`increase`、`decrease`、`keep`、`unknown`。

说明：

- 如果官方调仓接口只给基金名称、不含基金代码，标准层允许 `fund_code=null`，并增加 `fund_code_resolve_status` 标记 `exact`、`ambiguous` 或 `missing`。

### `fund_public_dim`

底层基金公开数据。

字段：

- `fund_code`
- `fund_name`
- `fund_company`
- `fund_type`
- `tracking_index`
- `theme_tags`
- `latest_nav`
- `latest_nav_date`
- `status`
- `source`

## 监测逻辑

### 每日快照

每日对每个渠道执行：

1. 拉取策略列表。
2. 拉取策略详情。
3. 拉取日度业绩曲线。
4. 拉取当前基金持仓。
5. 对比上一交易日持仓快照，生成本地推断的基金变仓记录。

本地推断调仓不等同于官方调仓，单独标记：

- 官方调仓：来自平台调仓公告或调仓记录。
- 本地快照变仓：来自连续快照比对。

### 调仓事件优先级

1. 平台官方调仓接口或公告。
2. App 详情页调仓记录。
3. 日度快照差异推断。

### 数据质量标记

建议每条记录包含：

- `confidence_level`：`official_exact`、`official_partial`、`snapshot_exact`、`snapshot_partial`、`inferred`。
- `access_level`：`public`、`login`、`signed_account`。
- `parse_status`：`success`、`partial`、`failed`。
