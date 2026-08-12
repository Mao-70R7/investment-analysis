# 节点调度框架

## 目标

读取 `pipeline.json`，按依赖顺序执行节点，记录每次尝试、日志、结果、产物和数据水位，并支持失败续跑与单节点诊断。

## 输入与输出

- 输入：工作区根目录、运行模式、可选运行批次或节点ID。
- 输出：`运行状态/logs/daily_update/<日期>/<run_id>` 和 `update_state.sqlite` 节点状态表。

## 运行约束

- 根入口只调用 `启动.ps1`，业务命令只能放在节点目录。
- 节点退出码为0后仍必须通过 `node_result.json` 的输出校验。
- 关键节点失败后不得执行备份和发布。

## 手工验证

```powershell
python -X utf8 .\节点脚本\00_调度框架\orchestrator.py --workspace-root . --dry-run daily
```

