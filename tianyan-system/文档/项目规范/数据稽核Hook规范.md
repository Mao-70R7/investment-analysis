# 数据稽核 Hook 规范

## 目标

本项目所有采集、加工、页面包、字段口径和实体识别修改后，都必须执行标准化数据稽核。稽核不只判断通过/失败，还必须说明不符合规范字段或数据的原因、影响和优化建议。

Hook 还承担规则治理职责：发现未登记规则时输出 `RULE_CATALOG_MISSING`，同一问题持续出现时输出 `PERSISTENT_AUDIT_ISSUE`。这两个治理规则用于推动“发现问题 -> 沉淀规则 -> 补检测逻辑 -> 再验证”的闭环。

## 执行入口

手动全量执行：

```powershell
python -X utf8 scripts\运行项目数据稽核hook.py --mode manual
```

只检查当前已生成页面包：

```powershell
python -X utf8 scripts\运行项目数据稽核hook.py --mode manual --audit-only
```

Windows Git hook 实际调用 ASCII 包装脚本，避免部分终端中文路径编码不一致：

```powershell
python -X utf8 scripts\run_project_data_audit_hook.py --mode manual
```

安装 Git hooks：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\安装数据稽核git_hooks.ps1
```

安装后：

- `pre-commit`：运行静态检查和标准化稽核，失败则阻止提交。
- `pre-push`：推送前再次运行标准化稽核，并保留 Git LFS pre-push。
- `post-commit`、`post-merge`、`post-checkout`：自动运行全量报表包构建和稽核，并保留 Git LFS hooks。

## 规则维护

稽核规则规范保存在：

- `config/数据稽核规则规范.json`
- `config/系统字段检查规则.json`

其中 `config/数据稽核规则规范.json` 负责登记 `ruleId`、原因说明、优化建议和修复责任脚本；`config/系统字段检查规则.json` 负责登记核心 SQLite 表、必需字段、字段覆盖率下限、页面数据包必需字段和字段字典必需实体。新增或调整业务字段、页面包字段、字段字典实体时，必须同步更新系统字段检查规则。

发现新问题时必须同时更新：

- `config/数据稽核规则规范.json`：新增规则编号、原因说明、优化建议、修复责任脚本。
- `config/系统字段检查规则.json`：新增或修正核心字段、覆盖率下限、页面包字段和字段字典实体要求。
- `scripts/标准化数据稽核.py`：新增或修正检测逻辑。

不得为了通过 hook 删除规则、降低 severity 或跳过问题。确认为误报时，应把规则边界改得更精确，并在规则规范中说明。

Windows 中文项目路径下，所有以 `sys.executable` 启动项目内 Python 子脚本的 `subprocess` 调用必须显式带 `-X utf8`，并优先传项目内相对路径。hook 会用 `PYTHON_SUBPROCESS_UTF8_REQUIRED` 静态规则检查这类问题，避免报表包构建时出现中文路径乱码。

## 效率分层

- Quick：`--audit-only`，适用于只验证现有页面包或文档类修改。
- Full：不加 `--audit-only`，适用于业务逻辑、数据加工、页面包和实体规则修改。
- Strict：加 `--fail-on-warn`，适用于交付、内网同步和大规模改动合并。

详细工作模式见 `docs/任务迭代持续进化工作模式.md`。

## 输出

标准化稽核报告：

- `outputs/data_audit/<date>/<run_id>/data_audit_report.json`
- `outputs/data_audit/<date>/<run_id>/data_audit_report.md`

hook 汇总：

- `outputs/data_audit_hook/<date>/<run_id>/hook_summary.json`
- `outputs/data_audit_hook/<date>/<run_id>/hook_summary.md`

报告中的每个 issue 必须包含：

- `ruleId`
- `severity`
- `scope`
- `item`
- `detail`
- `原因说明`
- `优化建议`
- `修复责任脚本`
- `sample`

## 核心事实语义检查

字段非空不代表数据可用于分析。标准化稽核还必须检查下列跨字段和量纲约束：

- 基金 F10 成立日期必须是有效日期，`---` 等占位符按缺失处理。
- FOF 历史不足必须先排除成立未满观察窗口的新基金，成熟产品缺口和成立日期待确认样本分别报告。
- 公募基金绩效快照的净值日期、单位净值和累计净值必须来自同一个净值点。
- 固定“近1年”收益、回撤和波动率必须使用完整一年窗口；不足一年数据只能写入实际样本期指标。
- 策略日度业绩不得出现非正单位净值或无法解释的极端日收益。
- 在运作策略的最新直接持仓必须保留原始量纲并校验权重闭合；空权重、0-1 与 0-100 混用必须显式告警。
- 规范库必须登记结构版本；启用外键前应先完成现有孤儿记录清理，并在所有写连接中统一开启外键校验。
