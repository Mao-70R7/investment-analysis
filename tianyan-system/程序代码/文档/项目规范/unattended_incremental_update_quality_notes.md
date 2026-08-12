# 无人值守增量更新优化说明

更新日期：2026-07-07

## 质量前提

- 不降低数据完整性、准确性和最新时效性。
- 不跳过最终质量审计、覆盖率审计、报告导出和 manifest 校验。
- 所有增量优化都保留回看窗口，用覆盖写入处理上游延迟披露或历史修订。

## 已优化项

- `run_incremental_update_and_refresh_report.bat` 修复括号块内 `%ERRORLEVEL%` 提前展开风险，避免出现 `8 was unexpected at this time.` 这类控制流中断。
- 新增 `ADVISOR_UNATTENDED=1`。无人值守模式下，天天基金和广发基金采集失败后会自动重试一次；仍失败则退出并记录需要人工检查的方向，不再弹窗或 `pause` 等待。
- 默认 `TTFUND_COLLECTION_ARGS` 的 `-QuoteProbeTimeoutSec` 从 `8` 提高到 `20`，降低上游慢响应导致的探测不完整风险。
- `scripts/update_index_daily_quotes.py` 新增 `--incremental --lookback-days N`。后处理默认按数据库现有最新日期回看 10 天刷新指数行情，避免每次从 2000 年全量请求。
- `scripts/run_ttfund_post_update_quality.py` 在基金历史净值装载前检查 normalized JSONL 输入是否比上次装载摘要更新；若没有新输入且数据库表已有数据，则跳过重复扫描大文件。
- `run_incremental_update_and_refresh_report.bat` 的默认参数改为“增量完整更新”口径：策略详情和广发费率/基准按日检查，详情/基准修复不再限制 80 条，基金净值和指数行情默认回看 30 天，基金穿透默认 1 天内不重复采集。

## 推荐配置

在 `advisor_update.local.bat` 中按需加入：

```bat
set ADVISOR_UNATTENDED=1
set ADVISOR_INDEX_QUOTE_LOOKBACK_DAYS=30
set ADVISOR_FUND_NAV_INCREMENTAL_DAYS=30
set ADVISOR_FUND_NAV_WORKERS=12
set ADVISOR_STRATEGY_DETAIL_COOLDOWN_DAYS=7
set ADVISOR_STRATEGY_DETAIL_REFRESH_LIMIT=0
set ADVISOR_CURRENT_HOLDING_COOLDOWN_DAYS=1
set ADVISOR_CURRENT_HOLDING_REFRESH_LIMIT=0
set ADVISOR_BENCHMARK_DETAIL_REPAIR_LIMIT=0
set ADVISOR_FUND_LOOKTHROUGH_STALE_DAYS=1
```

如果一台机器停跑超过 30 天，可以临时把 `ADVISOR_INDEX_QUOTE_LOOKBACK_DAYS` 和 `ADVISOR_FUND_NAV_INCREMENTAL_DAYS` 提高到覆盖停跑天数；这仍是滚动增量窗口，不会重跑全历史。`TTFUND_COLLECTION_ARGS` 中的 `-SkipQuality` 只用于避免天天基金子任务重复后处理，统一脚本的 Step 5 仍会完成数据库写入、质量检查和报表导出。

## 必须人工介入的节点

- 手机或 ADB 不可用：设备未连接、未解锁、ADB offline、USB 调试授权失效。
- App 登录态失效且接口需要人工登录、验证码、人脸、短信或滑块验证。
- 上游接口连续异常：同一批策略或行情接口多次超时、返回空数据、返回结构变化。
- 数据质量闸口失败：最终完整性审计、官方业绩覆盖率审计、调仓/持仓一致性审计失败。
- 服务器或目标目录异常：`ADVISOR_REPORT_ROOT` 不存在、权限不足、磁盘不足、`robocopy` 返回码大于等于 8。
- 数据库或输入文件损坏：SQLite 读写失败、normalized JSONL 无法读取、字段结构不符合预期。

无人值守脚本遇到上述问题应失败退出，而不是自动吞错继续发布。
