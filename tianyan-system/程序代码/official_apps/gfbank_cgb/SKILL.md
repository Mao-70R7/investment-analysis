# 广发银行发现精彩公开采集

## 复跑命令

```powershell
python -X utf8 节点脚本\_共享组件\生产程序\collect_official_apps_public.py --apps gfbank_cgb --workers 4
```

## 标准输出

- `official_apps/gfbank_cgb/outputs/app_public_entry.csv`
- `official_apps/gfbank_cgb/outputs/latest_summary.json`
- `official_apps/gfbank_cgb/outputs/coverage_check.json`
- `official_apps/gfbank_cgb/outputs/source_inventory.json`
- `official_apps/gfbank_cgb/outputs/raw_manifest.json`

## 口径

APK 静态资源可见基金、广发智投、基金定投、定投专区、360 资产配置等入口，并保留 `jumpLink` 与 `loginedFlag` 证据。匿名 H5 入口暂未返回投顾策略主数据、持仓或调仓接口。没有登录态缓存时，`strategy_master_ok` 和 `fund_level_position_ok` 预期均为 `false`；已有登录态缓存时，公开刷新会合并缓存，不会把策略主档覆盖为空，但 `fund_level_position_ok` 仍应为 `false`。

## 登录态真机证据

仅使用已登录的外接真机，禁止启用 MuMu。正式采集前确认每日更新锁不存在；采集程序会持有 `device` 资源锁。

广发智投首页的“理财组合”“超级定投”“目标盈”是三个独立策略入口，不能只抓“理财组合”后宣称银行渠道全量。正式补采三个入口时重复传入 `--strategy-entry`；证据文件名、`capture_summary.json.captured_strategy_names_by_entry`、策略标签和 App 内来源 URL 必须保留入口血缘：

```powershell
python -X utf8 节点脚本\_共享组件\生产程序\capture_gfbank_authenticated_ui.py `
  --workspace-root .. `
  --code-root . `
  --adb tools\platform-tools\adb.exe `
  --device-id <真机序列号> `
  --output-dir <本次证据目录> `
  --strategy-entry 理财组合 `
  --strategy-entry 超级定投 `
  --strategy-entry 目标盈 `
  --curve-scan-scope auto `
  --curve-read-mode recorded_ocr `
  --curve-ocr-verify-every 24
```

如果某个入口本批没有采到任何策略，批次标记为 `partial_success` 并在 `missing_strategy_entries` 中披露；已经取得且验证通过的其它入口允许增量晋级，但缺失入口必须保留为后续重试任务。“超级定投”是二级机构服务页，必须同时出现基金投顾服务说明、机构名称和当期组合/发车建议等登录态证据；普通基金定投排行不得写入投顾策略表。“目标盈”按当前服务与往期期次结构化提取目标收益率、实际运作天数和页面状态；该入口没有展示业绩走势图时，必须明确标记未披露，不得伪造收益曲线。

先采一个指定策略验证逐点曲线（示例为“招商佳薪90天”）：

```powershell
python -X utf8 节点脚本\_共享组件\生产程序\capture_gfbank_authenticated_ui.py `
  --workspace-root .. `
  --code-root . `
  --adb tools\platform-tools\adb.exe `
  --device-id <真机序列号> `
  --output-dir <本次证据目录> `
  --strategy-name 招商佳薪90天 `
  --curve-scan-step-px 3 `
  --curve-scan-scope auto `
  --curve-read-mode recorded_ocr `
  --curve-ocr-verify-every 24 `
  --skip-curve-dense-refinement
```

脚本先切换到“成立以来”，再沿 `echarts-div-line` 图表横轴触控。`recorded_ocr` 使用 scrcpy 临时录制和 Monkey 固定坐标脚本一次完成密集触控，不包含随机事件、App 重启或模拟器流程；每 24 个触点读取一次 UIAutomator 精确锚点。OCR 同时识别完整标签行和纯数值区域，二者不一致时拒绝该帧，同一日期出现多组值或周期锚点冲突时整批不允许晋级。录制视频解析后立即删除，不进入原始证据包。

`curve_*_since_inception_*.xml` 保存提示框明确披露的日期、组合累计收益和基准累计收益；OCR 读取的是渲染后的提示文字，不按走势图线条坐标反推收益。保留 `exact_ui` 作为逐点精读兜底。3 像素扫描得到的是页面分辨率下可触达的历史采样点，不应宣称覆盖每个交易日；多轮采集按日期取并集，并对同日值做冲突阻断。

`--curve-scan-scope auto` 是正式默认口径：缓存中该策略尚无至少两个已验证历史点时执行 `full` 首次回补；已有真实曲线时，先用当前详情页最新日期、组合累计收益和基准累计收益与缓存末点做三字段精确比对，完全一致则跳过录屏和触控；发生变化才执行 `recent`，只扫图表右侧 `--curve-recent-width-px`（默认 180 像素）补近期点。受控真机样本中，未变化策略约 50 秒完成页面核对且触点数为 0；`recent` 用 61 个触点取得 54 个日期点，单策略总耗时约 1 分 34 秒；首次全图约 4 分 16 秒。这些是设备和页面状态相关的实测值，不作为固定时限承诺。需要周期性复核或缓存损坏时可显式使用 `--curve-scan-scope full`。

外接真机已登录且已保存 `combo*.png`、`detail*.xml`、`curve*.xml` 时，先运行预览：

```powershell
python -X utf8 节点脚本\_共享组件\生产程序\collect_gfbank_authenticated_ui.py `
  --source-dir <真机证据目录>
```

确认 OCR 策略数、详情数、重复名称和最低置信度后，才允许加 `--promote` 写入标准化层：

```powershell
python -X utf8 节点脚本\_共享组件\生产程序\collect_gfbank_authenticated_ui.py `
  --source-dir <真机证据目录> `
  --promote
```

- 策略列表卡片可用于 `strategy_master`，没有渠道官方 ID 时按数据模型使用稳定名称 slug，并明确标记 ID 语义。
- 详情页明确展示的最新净值、收益率和日期可进入 `strategy_performance_daily`；收益率统一保存为百分点（页面 `28.96%` 保存为 `28.96`，不是 `0.2896`），净值仍保存为 `1.2896`。
- “成立以来”提示框的组合涨跌幅是自成立累计收益，因此逐点净值按 `单位净值 = 1 + 累计收益率_百分点 / 100` 生成；基准涨跌幅按同一日期保存为累计基准收益。至少两个不同日期才认定为真实曲线。
- 每个策略还要点击“基准涨跌幅”右侧披露图标，保存弹层中的“业绩基准”原文到 `strategy_master.benchmark`；该描述与走势图的基准累计收益是两个互补字段，不能互相推断或替代。
- 业绩基准说明和双曲线覆盖率只以实际提供业绩模块的“理财组合”为分母；“目标盈”和“超级定投”页面未披露业绩模块时，只入主档和披露状态，不得因为空值而反向补造数据。
- 目标盈期次名称保留 App 原文。若官方页面出现第75期、第17期、第77期这样的序列异常，必须同时保留 XML 和截图并输出稽核告警，不能擅自把第17期改写成推测的第76期。
- 曲线点只接受同一提示框同时出现完整日期、组合累计收益和基准累计收益的原值；任一字段缺失则该触点不入曲线。页面悬浮提示也只能展示所选日期的精确披露点，不得把上一披露日的值冒充为当天值。
- 采集晋级前必须校验上述量纲关系，并校验曲线最新日期/净值/累计收益与详情页一致；不一致时阻断本渠道晋级，避免切错标签或错误收益进入页面。
- `curve_*_manifest.json` 的 `failure_total`、`ocr_verification_mismatch_total`、视频同日冲突数必须全部为 0，且至少有一个精确锚点、临时视频已删除；否则 `collect_gfbank_authenticated_ui.py --promote` 必须阻断。
- 多策略批次允许“成功策略先晋级、失败策略单独重试”，避免一个瞬时页面失败使已验证结果全部作废；`capture_summary.json` 必须随证据保存，标准化摘要通过 `diagnostics.capture_batch.partial_failure`、缺失策略名和失败原因明确披露本批次不完整，不得把部分成功误报为全量成功。
- `--promote` 不得用单个策略或近期窗口直接覆盖登录态历史缓存：策略主档按策略 ID 合并，日度曲线按“策略 ID + 交易日”合并，区间收益按“策略 ID + 区间代码”保留最新日期。合并后历史数量缩水、同日净值/累计收益/基准收益冲突或业务键重复时必须阻断。
- 未展开并核实基金代码、基金名称和权重前，`strategy_fund_snapshot` 必须为空。
- “查看基金备选库”不是当前持仓；没有官方事件时，调仓事件和调仓明细必须为空。

## 入库核对

```powershell
python -X utf8 节点脚本\_共享组件\生产程序\load_analysis_zh_current_sqlite.py --keep-existing-db --channels gfbank_cgb
python -X utf8 节点脚本\_共享组件\生产程序\audit_official_app_channels_quality.py --channel-id gfbank_cgb
```

仅匿名采集时，入库后应看到策略信息 0 条。已提升本次完整登录态证据时，应逐策略看到多个不同交易日的日度净值与基准累计收益点；若仍只有 1 个日期，只能视为最新点，不能声称已取得走势图。持仓、调仓事件和调仓明细仍为 0，且质量报告必须与标准化文件一致。
