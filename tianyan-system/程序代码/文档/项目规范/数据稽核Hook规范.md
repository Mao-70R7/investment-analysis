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

其中 `config/数据稽核规则规范.json` 负责登记 `ruleId`、原因说明、优化建议和修复责任脚本；`config/系统字段检查规则.json` 负责登记核心 SQLite 表、必需字段、字段覆盖率下限、渠道策略级事实覆盖率、页面数据包必需字段和字段字典必需实体。新增或调整业务字段、页面包字段、字段字典实体或渠道覆盖门禁时，必须同步更新系统字段检查规则。

发现新问题时必须同时更新：

- `config/数据稽核规则规范.json`：新增规则编号、原因说明、优化建议、修复责任脚本。
- `config/系统字段检查规则.json`：新增或修正核心字段、字段非空率、`channelStrategyCoverage` 的渠道策略级事实覆盖率、页面包字段和字段字典实体要求。
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
- 天天投顾详情增量的新鲜度只允许来自有效 `strategyDetailPageData` 响应缓存；布局缓存即使更新时间较新也不得刷新详情冷却期。
- 天天常规详情刷新必须使用 60～80 个共享日额度并按最旧有效成功时间优先；新策略、缺失、无效和重新启用对象不受常规额度限制，明确终止策略至少按 30 天慢速复核。
- 同一策略的详情、基准、当前仓位和调仓历史必须形成唯一任务包。随仓位或调仓访问完成的详情计入日额度；任务结果逐字段记录完成和待补状态，同一 runId 只补未完成字段，不得用详情成功覆盖仓位或历史缺口。
- 调仓质量事件、基金明细、策略汇总和构建状态必须在策略模拟净值重建后原子重建；除明确采用官方权重直接回放的渠道外，当前算法的模拟区间事件ID集合、事件数和最新调仓日必须与调仓质量事实完全一致。货币基金只有日收益率的增量行可以复利续接既有复权指数，但不得跨真实缺失区间插值。页面构建后还必须按发布策略列表逐一核对最近一个可评估事件：详情包需存在同事件ID、由基金净值与调前调后权重精确回放的贡献曲线，两侧各至少两个不同日期的有效点，否则阻断发布。绝对最新调仓尚无调后净值区间时，页面必须明确回退到前一个可评估事件；数据库保留但未进入发布列表的历史策略不计作页面缺失。
- 规范库必须登记结构版本；启用外键前应先完成现有孤儿记录清理，并在所有写连接中统一开启外键校验。
