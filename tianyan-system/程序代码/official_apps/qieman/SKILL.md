# 且慢公开采集 Skill

## 目标

采集且慢公开入口和关联 SPA 静态资源，保存可复查快照，并提取投顾、组合、基金、调仓相关前端路径作为后续授权接口探测线索。

登录态 App 的早期只读技术验证和现行签名接口采集实现均保留在 `authenticated_probe/`。其中公开签名历史采集已由 `qieman_collect -> qieman_gate -> qieman_load` 正式节点封装进入每日 DAG；目录下其他探索性脚本仍不得绕过节点、资源锁、批次 gate 和事务入库直接写主库。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps qieman
```

登录态入口探测：

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\probe_qieman_device.py --allow-missing-device
```

公开双目录、登录态搜索目录和官方 StarGate 能力探测：

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\probe_qieman_public_api.py
python -X utf8 .\official_apps\qieman\authenticated_probe\enumerate_qieman_search_catalog.py `
  --device-id <adb_serial> --queries 组,基金,米,新,启
python -X utf8 .\official_apps\qieman\authenticated_probe\probe_qieman_stargate_api.py
```

StarGate 的 API Key 只能通过官方手机号短信流程取得，并仅从当前进程环境变量 `QIEMAN_STARGATE_API_KEY` 读取；不得写进脚本、配置、日志或运行产物。无 Key 的 401 是权限边界，不是采集成功。

登录态策略详情的无障碍树样本提取：

```powershell
python -X utf8 .\official_apps\qieman\authenticated_probe\extract_qieman_strategy_dom.py `
  --device-id <adb_serial> --strategy-id <策略代码> --strategy-name <策略名称>
```

该入口只保存脱敏后的业务文字和样本实体，不保存原始 UI XML、认证令牌或账户信息；详情标签切换只能使用内置 `--safe-tab` 白名单，不得操作交易控件。

## 公开来源

- `https://qieman.com/app`
- 页面引用的 `https://cdn2.qieman.com/static/*.js`

## 标准输出

- `official_apps/qieman/outputs/app_public_entry.csv`
- `official_apps/qieman/outputs/latest_summary.json`
- `official_apps/qieman/outputs/coverage_check.json`
- `official_apps/qieman/outputs/source_inventory.json`
- `official_apps/qieman/outputs/raw_manifest.json`

## 当前结果

最近一次成功运行：`20260524T094545+0800`。
- 公开策略：0 个。
- 关联 JS：3 个。
- 投顾相关前端路径线索：84 条。
- 当前基金级持仓：0 行。
- 调仓事件：0 个。

2026-08-09 隔离探测补充结果：

- 公开双目录 25 个带代码候选，登录态搜索 61 个名称，合并去重 82 个名称；旧单入口 16 个。
- 82 个名称中 26 个可关联策略代码、56 个缺代码；搜索页没有全量总数，不能将 82 视为完整目录。
- 用户点名的“我要稳稳的幸福”“超级理财加”“新锐定投组合”“搬砖小组B计划”均已发现，并发现 7 个“启明睿”系列。
- 官方 StarGate OpenAPI 已确认目录、详情、日度净值、基准拆分、当前持仓、历史调仓及调前/调后权重能力；无 Key 实测为 HTTP 401。
- 当前正式日度业绩、带 `position_date` 的严格完整持仓、调仓基金增减明细仍为 0，不得用卡片指标、OCR 或无日期权重替代。
- 所有新增产物仍在 `authenticated_probe/`，未接入每日 DAG、未写主库。

2026-08-09 StarGate 授权采集补充：

- 关键词并集 656 个带代码对象；排除 45 个明确测试/内测对象后，生产候选 611 个。
- 609 个生产策略取得严格完整当前持仓，共 10,724 行，单策略权重合计均为 100%；412 个生产策略取得基准响应，393 个具有精确基准拆分。
- “启明睿”识别 54 个生产策略及渠道版本；此前点名的四个策略均已补齐稳定代码、成立日、基准和当前持仓。
- API Key 使用 Windows DPAPI `CurrentUser` 加密保存在工作区本机配置，不得回显、写入 Git 或复制到其他电脑；换机后重新授权。
- 授权工具目录没有开放日度净值和调仓操作，HTTP 429 后当天停止重试；日度业绩与官方调仓仍不得推断或伪造。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\qieman\outputs\coverage_check.json
```

`app_public_entry` 应为 1 行，`holding_penetration_status` 应为 `blocked_app_or_auth_required`。

## 已知口径

公开入口是 SPA/下载页，不直接披露策略清单、基金级持仓、日度业绩或调仓明细。后续若要穿透到基金占比，需要 App/H5 登录态、授权 Cookie 或签名接口规则。
