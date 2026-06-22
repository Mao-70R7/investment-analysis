# 全市场投顾监控迁移运行说明

## 迁移口径

这个目录是从 `E:\AI工作区\投顾数据处理` 生成的最小化运行包，用于在另一台 Windows 服务器上继续执行后续增量脚本。

已包含：

- `scripts/`、`src/`、`schemas/`、`config/`：当前有效脚本和本地模块。
- `data/analysis_zh_current.sqlite`：当前中文分析主库。
- `data/advisor_monitor.sqlite`：原始快照索引库。
- `data/normalized/`：标准化采集基线数据，包含天天基金投顾、基金历史净值等源文件。
- `data/raw/`：当前项目原始采集快照。
- `site/`：当前可查看页面，包含基础数据页和策略中心页。
- `outputs/`：当前项目分析、质检、回放和页面导出结果。

未包含：

- `data/backups/`：历史 SQLite 备份库体积较大，不参与日常运行；需要回滚旧库时再单独同步。
- `node_modules/`：天天基金增量主链路不依赖它。

## 新服务器环境

建议准备：

- Windows PowerShell。
- Python 3.12 或兼容版本。
- Python 包：`requests`。
- Android Platform Tools：`adb` 可在 PATH 中调用，或运行时用 `-AdbExe` 指定完整路径。
- 手机已开启 USB 调试，天天基金 App 登录态有效，必要时保持解锁。

先检查环境：

```powershell
Set-Location "D:\SyncthingShareToMpc\全市场投顾监控"
powershell -NoProfile -ExecutionPolicy Bypass -File .\check_environment.ps1
```

## 日常增量运行

普通增量会默认执行完整链路：天天基金策略增量采集、基金净值和分红依赖刷新、基准指数行情刷新、策略净值重算、质检稽核和页面导出。

```powershell
Set-Location "D:\SyncthingShareToMpc\全市场投顾监控"
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_daily_incremental.ps1 -DeviceId "你的ADB设备ID"
```

基金净值刷新默认开启。为了避免每天全量重抓 4000 多只基金的完整历史，默认采用“已有基金近 45 天窗口增量刷新、新出现基金全历史补齐”的模式；可用 `-FundNavWorkers` 调整并发，用 `-FundNavIncrementalDays` 调整窗口天数：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_daily_incremental.ps1 -DeviceId "你的ADB设备ID" -FundNavWorkers 8 -FundNavIncrementalDays 45
```

只有在净值库历史被破坏、基金映射大范围修复、或需要重建完整基金历史时，才使用全历史刷新：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_daily_incremental.ps1 -DeviceId "你的ADB设备ID" -FullFundNavRefresh -FundNavWorkers 8
```

只在排障或临时加速时跳过基金净值或指数行情刷新：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_daily_incremental.ps1 -DeviceId "你的ADB设备ID" -SkipFundNavRefresh
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_daily_incremental.ps1 -DeviceId "你的ADB设备ID" -SkipIndexQuoteUpdate
```

只跑采集、不跑后处理质检：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_daily_incremental.ps1 -DeviceId "你的ADB设备ID" -SkipQuality
```

查看后处理步骤清单但不写库、不跑任务：

```powershell
python -X utf8 .\scripts\run_ttfund_post_update_quality.py --dry-run
```

## 增量链路顺序

当前日常脚本的有效顺序如下：

1. 建立天天基金增量计划，判断是否需要手机补采详情或历史调仓。
2. 如有缺口，通过真机补采详情页、历史调仓页和登录态接口数据。
3. 入库策略信息、官方披露业绩、调仓仓位、当前持仓等天天基金数据。
4. 先加载已有标准化基金历史数据，再刷新公开基金净值和分红数据；默认只刷近期窗口，新出现基金会自动补全历史，避免新数据被旧基线覆盖。
5. 补齐互认、海外、QDII 等特殊基金净值和分红提示。
6. 更新内置基准指数行情，包括沪深300、中证800、中证500等。
7. 重算策略标准净值、官方披露业绩对比、当前持仓推算稽核、完整性报告和页面数据。

## 同步注意事项

- 不要让两台机器同时写同一个 SQLite 数据库。建议只在一台机器执行脚本，另一台只查看或等待同步完成。
- 运行脚本期间最好避免 Syncthing 正在写入 `.sqlite` 文件；任务结束后再同步更稳定。
- 如果 Syncthing 产生冲突副本，需要保留最近一次成功任务所在机器的数据库和输出目录。
- 页面入口：`site/basic_data/index.html`。

## Linux 页面启动

Linux 上只负责查看页面时，不需要跑采集和增量脚本。进入同步后的发布目录，直接启动静态页服务：

```bash
cd /path/to/全市场投顾分析平台
bash ./start_basic_data_site_linux.sh --host 0.0.0.0 --port 7676
```

该启动脚本会同时启用 Ai选策略模型同源代理：

- 浏览器请求：`http://服务器IP:7676/llmapi/v1/chat/completions`
- 代理上游：`http://10.89.189.109:8000/llmapi/v1/chat/completions`
- 上游配置文件：`./config/ai_strategy_proxy.env`

不要改用裸 `python -m http.server` 启动页面，否则 Ai选策略会重新遇到浏览器 CORS 限制。

启动脚本默认后台运行，控制台信息和 HTTP server 输出会追加到日志：

```bash
tail -f ./logs/basic_data_site_7676.log
```

PID 文件在 `./logs/basic_data_site_7676.pid`。默认监听 `0.0.0.0:7676`，访问 `http://服务器IP:7676/`。
如需前台排查，使用 `bash ./start_basic_data_site_linux.sh --foreground --host 0.0.0.0 --port 7676`。

数据增量仍在 Windows 环境下通过 `run_daily_incremental.bat` 执行。控制台会持续打印当前阶段，例如生成增量计划、手机补采、接口采集、入库质检、页面导出等，不需要等任务结束后再看日志。
