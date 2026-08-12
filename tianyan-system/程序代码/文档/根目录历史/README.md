# 投顾产品观测工程

## 每日更新唯一入口

日常更新只运行项目根目录下这个文件：

```text
00_每日数据更新并发布_唯一入口.bat
```

直接双击即可。它会依次执行数据就绪检查、天天投顾及广发基金增量更新、完整数据稽核、成功数据库滚动备份和 GitHub 最小发布集发布。控制台会显示总进度、当前步骤及耗时；在策略采集阶段还会显示已完成策略数/总策略数、成功/失败数、当前策略和预计剩余时间。完整日志保存在 `logs/daily_update_console`。

其他 `.bat`、`.ps1` 是专项采集、故障修复或兼容入口，不作为日常运行入口。详细说明见 [每日更新唯一入口说明](docs/每日更新唯一入口说明.md)。

本工程用于沉淀基金投顾组合的策略基础信息、日度业绩、基金级持仓快照和调仓事件。

当前优先渠道：

- 南方基金 / 司南投顾
- 中欧财富 / 中欧钱滚滚
- 易方达投顾 / 易方达财富 e钱包
- 嘉实财富投顾
- 广发基金投顾

## 核心原则

1. 不把账号密码写入代码、配置或日志。
2. 每个渠道独立保存原始响应，再抽取为统一表结构。
3. 历史基金仓位以每日快照为主；如果平台只披露调仓记录，则保存基金级调仓前后权重。
4. 任何字段都记录来源、抓取时间、页面或接口 URL、登录状态，方便后续审计。

## 目录

```text
config/channels/         渠道配置，记录入口 URL、采集层级、字段映射线索
data/raw/                原始 HTML/JSON/截图等，不做覆盖
data/normalized/         统一结构 JSONL/CSV，便于入库前检查
data/state/              登录态、游标、上次快照索引等运行状态
docs/                    数据模型和采集说明
schemas/                 数据库建表 SQL
scripts/                 可直接运行的采集、初始化脚本
src/advisor_monitor/     采集与标准化代码
```

## 当前南方公开页验证

南方司南投顾公开专区页面主要是图片落地页，入口会跳转到南方基金网上交易系统登录后页面：

- 公开入口：`https://www.nffund.com/new/snzt/index.html`
- 登录入口：`https://trade.southernfund.com/new/account/login/init?from=web&url=%2Fiainvest%2Finit%3FmenuId%3D80000`

公开页可稳定确认渠道存在、入口 URL、宣传图资源和登录跳转；策略列表、日度业绩、基金持仓权重预计需要登录后采集。

## 南方登录态验证

登录页包含验证码/滑块模块，建议先人工登录一次并保存会话，再用无头模式复用会话发现接口：

```powershell
$env:NODE_PATH='C:\Users\41088\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
& 'C:\Users\41088\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' scripts\save_southern_session.js
& 'C:\Users\41088\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' scripts\probe_southern_authenticated.js
```

`data/state/southern.storageState.json` 是登录态文件，包含 cookie，不应提交或外传。
