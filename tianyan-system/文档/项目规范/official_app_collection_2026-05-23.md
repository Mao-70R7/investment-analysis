# 官方 App 投顾策略公开采集结果（2026-05-23）

## 范围

本轮覆盖用户指定的 5 个官方 App/官方入口：

- 华夏投顾/华夏财富查理智投
- 嘉实财富
- 中欧财富/中欧钱滚滚
- 南方基金/司南投顾
- 招商基金/招财乐投顾

统一采集脚本：

```powershell
python .\scripts\collect_official_apps_public.py --apps huaxia_tougu,zocaifu,harvestwm,southern,cmfchina --workers 8
```

统一复跑入口：

```powershell
powershell -ExecutionPolicy Bypass -File .\official_apps\run_all.ps1
```

## 结果总览

| App/渠道 | run_id | 状态 | 策略数 | 日度业绩 | 当前基金持仓 | 调仓事件 | 调仓基金明细 | 基金级穿透结论 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 华夏投顾 | 20260523T233343+0800 | success | 18 | 22740 | 341 | 225 | 4116 | 公开可穿透到基金代码、名称、调前/调后权重 |
| 中欧财富 | 20260523T223717+0800 | success | 49 | 49959 | 906 | 467 | 9019 | 公开可穿透到基金权重；部分历史调仓缺基金代码 |
| 嘉实财富 | 20260523T224046+0800 | success_public_disclosure_only | 21 | 0 | 0 | 0 | 0 | 公开站只到公告/PDF，仓位和业绩需登录 |
| 南方基金 | 20260523T224046+0800 | success_landing_only | 0 | 0 | 0 | 0 | 0 | 公开入口成功，策略数据需登录 |
| 招商基金 | 20260523T224046+0800 | success_public_selected_strategies | 4 | 0 | 0 | 0 | 0 | 公开 SSR 只有精选策略卡片 |

## 可穿透到每次调仓基金占比的渠道

### 华夏投顾

可用。`sfAdjustVersion` 公开接口返回每次调仓的基金代码、基金名称、调仓前权重、调仓后权重和生效日期。标准化文件：

- `official_apps/huaxia_tougu/outputs/strategy_rebalance_event.csv`
- `official_apps/huaxia_tougu/outputs/strategy_rebalance_fund_delta.csv`
- `official_apps/huaxia_tougu/outputs/strategy_fund_snapshot.csv`

### 中欧财富

可用。公开 API 返回当前基金级持仓和官方调仓记录。当前持仓含基金代码、基金名称和权重；部分历史调仓记录只含基金名称和权重，不含基金代码，标准化时保留为缺失，不做猜测补全。标准化文件：

- `official_apps/zocaifu/outputs/strategy_rebalance_event.csv`
- `official_apps/zocaifu/outputs/strategy_rebalance_fund_delta.csv`
- `official_apps/zocaifu/outputs/strategy_fund_snapshot.csv`

## 暂不能公开穿透的渠道

### 嘉实财富

官网能全量抓取投顾相关公告和方案说明书附件。本轮抓取 363 条投顾相关公告/披露事件，识别 21 个策略名。公告正文提示部分产品信息需登录会员中心或嘉实财富 App 查看，公开侧未发现结构化基金级仓位、业绩曲线或调仓明细。

### 南方基金

公开页面是司南投顾入口，策略数据在登录后的 `iainvest` 交易系统中。公开侧只能记录入口和登录跳转。

### 招商基金

官网 SSR 页面公开披露 4 个精选策略：乐钱包、乐稳健、乐均衡、乐进取。基金级持仓、业绩曲线和调仓记录没有在公开 HTML 中出现；底层接口存在加密/签名封装，本轮不模拟 App 加密请求。

## 校验方式

每个 App 目录都有：

- `outputs/latest_summary.json`
- `outputs/coverage_check.json`
- `outputs/source_inventory.json`
- `outputs/raw_manifest.json`
- `SKILL.md`

批量查看覆盖情况：

```powershell
Get-ChildItem .\official_apps -Directory |
  Where-Object { Test-Path (Join-Path $_.FullName "outputs\coverage_check.json") } |
  ForEach-Object {
    Get-Content -Raw -Encoding UTF8 (Join-Path $_.FullName "outputs\coverage_check.json") |
      ConvertFrom-Json |
      Select-Object channel_id, collection_status, holding_penetration_status, entity_counts
  }
```

## 原始数据和标准层

原始快照按数据模型保存：

```text
data/raw/{channel}/{collector}/{yyyy-mm-dd}/{run_id}/
```

标准 JSONL 保存：

```text
data/normalized/{channel}/{entity}/{yyyy-mm-dd}/{run_id}.jsonl
```

每个 App 子目录 `outputs/` 复制了最新一轮的 CSV/JSONL，便于人工检查和后续加载。
