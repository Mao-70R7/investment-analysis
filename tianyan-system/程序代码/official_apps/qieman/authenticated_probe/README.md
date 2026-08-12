# 且慢登录态 App 隔离探测

本目录只用于且慢 App 的技术方案验证，不属于每日更新 DAG，不写主库，也不修改正式 `basic_data` 页面包。

## 探测顺序

1. 检查 `daily_update`、`device`、`main_db_write`、`publish_repo` 活动锁。
2. 通过 ADB 确认唯一真机及当前前台应用，定位且慢包名、版本和启动 Activity。
3. 保存脱敏后的 UI 层级、包信息和 APK 静态接口线索；默认不保存截图、不读取账户私有目录。
4. 若发现可验证的接口，再对 3 至 5 个策略采集列表、详情、基准、日度业绩和基金级仓位样本。
5. 样本只写入本目录的 `runs/<run_id>/`，确认字段和质量口径后再评估是否接入生产节点。

## 首次入口探测

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\probe_qieman_device.py --allow-missing-device
```

手机尚未授权时，可复跑匿名 API 边界验证：

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\probe_qieman_public_api.py
```

该命令合并 `/pmdj/v2/m4` 与 `/pmdj/v2/m4/hand-picked` 两个公开目录，把去重后的卡片保存为 `strategy_master_candidates.jsonl`。公开卡片只是目录候选，不会被误写成且慢全量策略清单；卡片上的“历史年化收益”如果缺统计日期和区间定义，也不会写入正式业绩实体。受保护的详情、净值、持仓和调仓接口即使返回 HTTP 200，只要响应体为空，也统一判定为未取得数据。

登录态全局搜索目录枚举：

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\enumerate_qieman_search_catalog.py `
  --device-id <adb_serial> --queries 组,基金,米,新,启
```

该入口只点击搜索历史和“更多策略”，不点击交易控件。搜索卡片可形成名称、机构、风险等级、建议持有期以及搜索时点的收益/回撤候选，但页面不披露策略代码，因此不能仅凭名称候选写入主库或关联正式业绩、持仓、调仓实体。

官方 StarGate OpenAPI 能力和全量目录探测：

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\probe_qieman_stargate_api.py
```

未设置 `QIEMAN_STARGATE_API_KEY` 时，该命令只保存公开 OpenAPI 字段目录和 401 权限边界；不会保存、打印或猜测 API Key。获得官方 API Key 后，只在当前 PowerShell 进程设置环境变量再复跑，采集器会按照接口返回的 `metadata.pagination.total` 分页到目录总数。官方接口已确认覆盖策略详情、带日期净值、基准及拆分、当前基金持仓、历史调仓和调前/调后权重；未经 API Key 实际返回验证前，这些只记为“接口能力已确认”，不记为数据已采集。

一站式覆盖汇总使用 `build_qieman_discovery_report.py`，合并公开目录、登录态搜索、历史详情样本和 StarGate 边界，输出 `discovery_coverage_report.json`、`discovery_strategy_matrix.csv` 与 Markdown 摘要。

如同时连接多台设备，显式指定序列号：

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\probe_qieman_device.py --device-id <adb_serial>
```

只有确认屏幕停留在不含个人账户信息的策略页面时，才使用 `--capture-screenshot` 保存截图证据。

策略详情页可见字段滚动验证：

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\capture_qieman_strategy_scroll.py `
  --device-id <adb_serial> --strategy-id ZH012636 --strategy-name 货币三佳
```

该工具只在策略介绍页向上滑动并截图/OCR，不点击转入、定投、关注、咨询或交易入口；OCR 结果统一标记为 `authenticated_ui_ocr_partial`。

目录覆盖率报告使用 `build_qieman_catalog_coverage.py` 生成。它严格区分业绩摘要与结构化日度序列，也区分基金类型占比、基金名单与单基金仓位占比；不会把截图曲线、日涨跌或类别分布升级为完整持仓数据。
需要回到长列表或详情顶部时，可显式使用 `--scroll-direction page_up`；默认 `page_down` 表示向页面下方采样。

策略详情 WebView 无障碍树的结构化样本提取：

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\extract_qieman_strategy_dom.py `
  --device-id <adb_serial> --strategy-id ZH012636 --strategy-name 货币三佳
```

如需切换详情页的数据标签，只允许使用白名单参数 `--safe-tab performance|configuration|announcement`。工具不会接受任意点击坐标，不会点击“定投”“转入”或任何确认交易控件；原始 XML 只在内存中解析并立即删除，落盘内容仅包含脱敏后的业务文字和样本实体。

若真机 WebView 后续因页面过大导致 `uiautomator` 被系统终止，可用 `--sanitized-nodes-input <先前的 sanitized_text_nodes.json>` 离线重做规范化。该模式不访问设备，便于修正解析规则后复核同一份脱敏证据。

## 输出

- `summary.json`：运行状态、设备、包名、版本、入口和阻塞原因。
- `device_evidence.json`：ADB、前台 Activity、WebView 调试入口等只读证据。
- `package_metadata.json`：且慢包元数据和 APK 路径。
- `ui/window.xml`：脱敏后的 UI 层级，不保存原始未脱敏 XML。
- `apk_inventory.json`：APK 内公开 URL、域名、接口路径和业务关键词线索。
- `coverage_assessment.json`：按现有实体结构记录已验证、部分验证、未验证和不可获取字段。
- `normalized/strategy_master_candidates.jsonl`：公开精选卡片形成的策略候选，只用于入口和字段验证。
- `normalized/strategy_*`：登录态策略详情样本；缺少持仓日期时 `position_date=null`，只用于技术验证，不得直接进入主库。
- `evaluations/<run_id>/`：多策略样本合并后的覆盖评估、质量门禁结果和 `validated_samples/*.jsonl`。

## 2026-08-09 覆盖结论

- 原单入口目录为 16 个；两个公开目录合并得到 25 个带策略代码候选。
- 登录态搜索 5 个查询词得到 61 个去重名称；与公开目录合并为 82 个名称，是旧结果的 5.125 倍。
- “我要稳稳的幸福”“超级理财加”“新锐定投组合”“搬砖小组B计划”均已发现；另发现 7 个“启明睿”系列策略。
- 82 个名称中 26 个已有策略代码，56 个仍缺代码；搜索结果不是全量目录，不能据此声称达到预计的 200+ 总数。
- 现有样本有 55 行基金名单、38 行精确权重和 2 个调仓事件，但缺正式 `position_date`，严格完整持仓仍为 0；结构化日度业绩仍为 0，调仓基金增减明细仍为 0。
- StarGate 无 Key 实测为 HTTP 401“缺少API密钥”。取得官方 API Key 后，先以接口总数作为目录完整性门禁，再采集详情、净值、基准、持仓和调仓。
- 本目录仍是隔离探测区：未接入每日 DAG、未写主库、未修改正式页面包。

## 2026-08-09 StarGate 授权采集结果

官方短信授权后，API Key 仅在本机处理，并以 Windows DPAPI `CurrentUser` 加密保存到工作区 `本机配置/qieman_stargate_api_key.dpapi`；密钥明文不进入命令行、日志、JSON、SQLite 或 Git 管理目录。换电脑或换 Windows 用户后必须重新授权，不能复制密文直接使用。

- 授权关键词目录并集取得 656 个带稳定策略代码的对象，已明显超过原预期 200+；API Key 权限未开放 `SearchPortfolioStrategies` 总数，因此 656 仍是可追溯下限，不冒充官方完整 total。
- 其中 45 个名称明确包含测试、TEST、staging 或内测标识，保留原始证据但从生产口径排除；生产候选为 611 个。
- 609 个生产策略取得严格完整当前基金持仓，共 10,724 行；基金代码、基金名称、精确权重和统一官方更新时间同时存在，单策略权重合计全部为 100%。缺持仓的生产策略为 `SI000186` 与 `S_WALLET`。
- 412 个生产策略取得基准响应，393 个取得权重合计为 100% 的精确基准拆分。
- 用户点名的四个策略全部取得代码、成立日、基准和当前持仓；“启明睿”共识别 54 个生产策略及渠道版本。
- 授权工具目录只开放关键词目录、持仓、基准和一个当前不可用的详情工具；未开放 `GetStrategyNavHistory` 与 `GetStrategyAdjustments`，所以结构化日度业绩、官方调仓事件及调前/调后权重仍为真实缺口。
- 采集后官方返回 HTTP 429“今日请求次数已达上限”；当日不继续重试，后续批次必须复用已加密 Key 并控制目录、持仓和基准的请求预算。

长期运行时，先用 `QIEMAN_DPAPI_INPUT` 启动本地只读代理，再运行 `collect_qieman_stargate_proxy.py`。正式接入每日 DAG 前还需增加配额预算、增量计划、批次门禁和主库事务加载节点；当前仍未入主库。

## 2026-08-10 全量历史采集与发车类口径

- 生产关键词并集口径的 611 个策略全部完成签名历史接口请求；该目录仍是可追溯下限，不冒充官方完整 total。
- 607 个策略取得非空日度业绩，共 521,389 行；`LONG_WIN`、`LONG_WIN_S`、`S_WALLET`、`SI000186` 的官方接口返回 HTTP 200 空数组。
- 基准缺口专项补采后，611 个策略全部有官方基准响应，592 个有权重合计为 100% 的精确拆分；剩余 19 个响应本身没有有效拆分。
- 609 个策略有严格完整当前仓位；缺口仍为 `S_WALLET` 与 `SI000186`。
- 普通组合通过 `/pomodels/{code}/adjustments` 取得 7,653 个事件、128,594 条基金权重明细；577 个普通/特殊代码中 574 个有披露事件，3 个为官方零事件响应。
- 34 个发车/信号组合通过 `/pomodels/{code}/sig-adjustments` 取得 3,776 个事件和 31,079 条买入、赎回、转换指令。26 个策略的每个事件均有完整 post 仓位，7 个策略存在早期事件缺 post 快照，`SI000186` 为官方零事件响应。
- `buyOrders.percent`、`redeemOrders.percent`、`convertOrders.percent` 只保留为指令比例，不得当作组合仓位。组合仓位只取 `signalPoSimulateAsset.compositionAssetList` 和 `modelTargetComposition` 的官方完整快照。
- 旧版 post 快照可能把 `targetPercent` 留为 0；标准化器在同一事件内从 `targetCapitalPercent`、`targetPercent`、`capitalPercent`、`percent` 中选择可加总为 100% 的官方字段并记录 `fund_weight_field`，不跨事件拼接。
- 缺失的旧仓位快照不使用发车金额或指令比例反推；信号兼容投影单独输出，标记 `eligible_for_official_rebalance_table=false`，不得混入普通官方调仓事实。
- 最终隔离运行目录为 `runs/20260810T092304+0800-signed-history-catalog`；覆盖报告、严格缺口清单和标准化实体均在该目录。生产数据库和每日更新 DAG 未修改。
- 且慢隔离包专项稽核为 `0 error / 5 warning`：14,570 个历史仓位快照权重合计异常为 0，基金代码/名称缺失为 0，信号投影进入普通官方调仓表为 0；5 类 warning 均对应已披露的数据缺口。
- 且慢专项测试 36 项全部通过。项目 full hook 因既有 FOF 排名外部源 DNS 解析失败而中止；随后 `audit-only` 超过 15 分钟工具窗口，不能声明本轮全项目稽核通过。

## 安全边界

- 不保存登录密码、Cookie、Authorization、设备账号、手机号、身份证号或银行卡号。
- 首页一旦出现账户、待办、收益或资产提示，UI 的全部 `text`/`content-desc` 会整体替换为占位符，只保留结构和资源 ID。
- 不安装抓包证书、不修改系统代理、不 root、不使用 Frida 绕过 TLS 或 App 安全控制。
- 不把截图、推荐列表、页面入口或单点文本当作日度业绩、精确持仓或调仓明细。
- 日度曲线至少需要同一策略两个以上可验证日期点；基金级仓位必须能对应基金代码/名称和权重。
