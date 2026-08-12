# 天天基金投顾模拟器采集验收说明

本说明对应独立脚本：

- `scripts/probe_ttfund_emulator_data.py`
- `scripts/run_ttfund_emulator_probe.ps1`

这套脚本只用于验证“模拟器作为 ADB 设备时，能否获取天天基金投顾详情缓存”。它不会调用或修改现有真机增量流程，也不会写入生产缓存镜像或 SQLite 数据库。

## 隔离边界

脚本只写入：

```text
data/raw/ttfund/emulator_probe/<date>/<run_id>/
```

不会写入：

```text
data/raw/device_cache/
data/advisor_monitor.sqlite
data/analysis_zh_current.sqlite
E:/synctingDataToWork/全市场投顾分析平台
```

## 设备要求

模拟器需要满足：

1. `adb devices -l` 能看到 `device` 状态。
2. 设备能安装并打开天天基金 App：`com.eastmoney.android.fund`。
3. 天天基金 App 已登录，且能进入投顾策略详情页。
4. 缓存目录可读：

```text
/sdcard/Android/data/com.eastmoney.android.fund/files/.ttjj_cache/
```

## 常用命令

列出当前 ADB 设备，并判断是否像模拟器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_ttfund_emulator_probe.ps1 -ListDevices
```

使用模拟器自动选取 3 个本地样本策略验证：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_ttfund_emulator_probe.ps1 -SampleSize 3
```

指定模拟器设备和策略：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_ttfund_emulator_probe.ps1 `
  -DeviceId "emulator-5554" `
  -StrategyId "VOXGR2C","VORCBP0","01VPJAL"
```

仅用于脚本逻辑隔离验收时，可以显式允许非模拟器设备。默认不允许，避免误用生产真机：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_ttfund_emulator_probe.ps1 `
  -DeviceId "b27b7c93" `
  -AllowNonEmulator `
  -StrategyId "VOXGR2C","VORCBP0","01VPJAL"
```

## 验收口径

每个策略会输出独立 `result.json`，总结果在 `summary.json`。

重点看：

- `device.is_emulator`：是否识别为模拟器。
- `device_health.app_installed`：天天基金 App 是否安装。
- `device_health.cache_dir_accessible`：缓存目录是否可读。
- `detail_ok_total`：成功获取策略详情缓存的数量。
- `holding_ok_total`：成功获取当前持仓缓存的数量。
- `results[].validation.strategy_name`：策略名称是否从缓存中解析出。
- `results[].validation.advisor_institution`：投顾机构是否解析出。
- `results[].validation.performance_stage_ok`：区间业绩快照是否解析出。
- `results[].validation.holding_info_ok`：当前持仓是否解析出。

通过标准：

```text
ok_total >= 1
detail_ok_total >= 1
holding_ok_total >= 1
```

如果模拟器返回 `app_installed=false`，先安装天天基金 App 并登录。  
如果 `cache_dir_accessible=false`，需要检查模拟器 Android 版本、应用外部存储权限或 App 是否已真正打开过投顾页面。
