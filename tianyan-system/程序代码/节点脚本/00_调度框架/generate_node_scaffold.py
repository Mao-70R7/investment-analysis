from __future__ import annotations

import json
from pathlib import Path


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
NODE_ROOT = CODE_ROOT / "节点脚本"


NODES = [
    ("preflight_environment", "运行环境与依赖", "01_运行预检/01_环境与依赖", "environment_preflight", [], "critical", 600, 1, None, "项", True),
    ("preflight_database", "数据库健康检查", "01_运行预检/02_数据库健康", "database_health", ["preflight_environment"], "critical", 7200, 1, "main_db_write", "张表", True),
    ("source_readiness", "数据源就绪检查", "01_运行预检/03_数据源就绪", "source_readiness", ["preflight_database"], "optional", 12600, 1, None, "次检查", True),
    ("device_select", "真机选择与预检", "01_运行预检/04_设备选择", "device_select", ["source_readiness"], "optional", 900, 1, "device", "台设备", True),
    ("ttfund_plan_component", "天天增量计划与新策略组件", "02_天天投顾/01_增量计划与新策略", "component_info", [], "optional", 60, 1, None, "个计划", False),
    ("ttfund_direct_component", "天天直接接口采集组件", "02_天天投顾/02_直接接口采集", "component_info", [], "optional", 60, 1, None, "只策略", False),
    ("ttfund_holding_component", "天天详情与当前仓位组件", "02_天天投顾/03_详情与当前仓位", "component_info", [], "optional", 60, 1, None, "只策略", False),
    ("ttfund_rebalance_component", "天天调仓增量组件", "02_天天投顾/04_调仓增量", "component_info", [], "optional", 60, 1, None, "只策略", False),
    ("ttfund_curve_component", "天天官方业绩曲线组件", "02_天天投顾/05_官方业绩曲线", "component_info", [], "optional", 60, 1, None, "只策略", False),
    ("ttfund_incremental", "天天失败重试与批次验收", "02_天天投顾/06_失败项重试与批次验收", "ttfund_incremental", ["device_select"], "optional", 28800, 1, "device", "只策略", True),
    ("gffunds_performance", "广发业绩曲线", "03_广发基金/01_业绩曲线", "gffunds_performance", ["preflight_database"], "optional", 7200, 2, None, "只策略", True),
    ("gffunds_metadata", "广发基准费率元数据", "03_广发基金/02_基准费率元数据", "gffunds_metadata", ["process_load"], "optional", 7200, 2, "main_db_write", "只策略", True),
    ("gffunds_collect", "广发仓位与调仓", "03_广发基金/03_仓位与调仓", "gffunds_collect", ["gffunds_performance"], "optional", 10800, 2, None, "只策略", True),
    ("gffunds_gate", "广发失败重试与批次验收", "03_广发基金/04_失败项重试与批次验收", "gffunds_gate", ["gffunds_collect"], "optional", 600, 1, None, "项检查", True),
    ("gfsec_fima_collect", "广发证券财富管家采集", "03_广发证券/01_财富管家采集", "gfsec_fima_collect", ["preflight_database"], "optional", 3600, 2, None, "个底层组合", True),
    ("gfsec_fima_gate", "广发证券财富管家批次验收", "03_广发证券/02_批次验收", "gfsec_fima_gate", ["gfsec_fima_collect"], "optional", 600, 1, None, "项检查", True),
    ("gfsec_fima_load", "广发证券财富管家增量入库", "03_广发证券/03_增量入库", "gfsec_fima_load", ["gfsec_fima_gate"], "optional", 1800, 1, "main_db_write", "张表", True),
    ("gf_supplemental_collect", "广发证券历史接口补充采集", "03_广发渠道补充/01_银行及贝塔牛采集", "gf_supplemental_collect", ["preflight_database"], "optional", 3600, 1, None, "个渠道", True),
    ("gf_supplemental_gate", "广发证券历史接口批次验收", "03_广发渠道补充/02_批次验收", "gf_supplemental_gate", ["gf_supplemental_collect"], "optional", 600, 1, None, "个渠道", True),
    ("gf_supplemental_load", "广发证券历史接口增量入库", "03_广发渠道补充/03_增量入库", "gf_supplemental_load", ["gf_supplemental_gate"], "optional", 1800, 1, "main_db_write", "个渠道", True),
    ("process_load", "标准化与增量入库", "04_数据加工/01_标准化与增量入库", "process_load", ["ttfund_incremental", "gffunds_gate", "gfsec_fima_load", "gf_supplemental_load"], "critical", 14400, 1, "main_db_write", "个步骤", True),
    ("fund_nav", "仓位基金净值", "04_数据加工/02_仓位基金净值", "fund_nav", ["process_load"], "critical", 14400, 1, "main_db_write", "只基金", True),
    ("fund_lookthrough", "基金穿透与分类", "04_数据加工/03_基金穿透与分类", "fund_lookthrough", ["fund_nav"], "critical", 14400, 1, "main_db_write", "只基金", True),
    ("index_benchmark", "指数基准与资产映射", "04_数据加工/04_指数基准与资产映射", "index_benchmark", ["fund_lookthrough"], "critical", 10800, 1, "main_db_write", "个指数", True),
    ("strategy_governance", "策略治理业绩风险分类", "04_数据加工/05_策略治理业绩风险分类", "strategy_governance", ["index_benchmark"], "critical", 21600, 1, "main_db_write", "个步骤", True),
    ("report_build", "最小发布源报表构建", "05_质量与交付/02_页面报表构建", "report_build", ["strategy_governance"], "critical", 14400, 1, None, "个报表步骤", True),
    ("data_audit", "项目完整数据稽核", "05_质量与交付/01_数据稽核", "data_audit", ["report_build"], "critical", 14400, 1, None, "项规则", True),
    ("database_backup", "成功数据库备份", "05_质量与交付/03_成功数据库备份", "database_backup", ["data_audit"], "optional", 14400, 1, "main_db_write", "个版本", True),
    ("publish", "最小发布集发布", "05_质量与交付/04_最小发布集发布", "publish", ["data_audit"], "publish", 10800, 1, "publish_repo", "个文件", True),
    ("pages_verify", "GitHub Pages版本验证", "05_质量与交付/05_GitHubPages版本验证", "pages_verify", ["publish"], "optional", 900, 1, None, "次检查", True),
    ("runtime_initialize", "首次初始化", "90_运维工具/01_首次初始化", "runtime_initialize", [], "critical", 7200, 1, None, "项检查", False),
    ("runtime_check", "环境检查修复", "90_运维工具/02_环境检查修复", "runtime_check", [], "critical", 3600, 1, None, "项检查", False),
    ("migration_package", "迁移包构建", "90_运维工具/03_迁移包构建", "migration_package", [], "critical", 14400, 1, None, "个文件", False),
    ("runtime_update", "程序更新", "90_运维工具/04_程序更新", "runtime_update", [], "critical", 3600, 1, None, "个提交", False),
    ("runtime_rollback", "程序回退", "90_运维工具/05_程序回退", "runtime_rollback", [], "critical", 3600, 1, None, "个提交", False),
]

NODE_OVERRIDES = {
    "source_readiness": {"failureImpact": "channel"},
    "device_select": {"failureImpact": "warning"},
    "ttfund_incremental": {"failureImpact": "channel"},
    "gffunds_performance": {"failureImpact": "warning"},
    "gffunds_metadata": {"failureImpact": "warning"},
    "gffunds_collect": {"failureImpact": "channel"},
    "gffunds_gate": {"failureImpact": "channel"},
    "gfsec_fima_collect": {"failureImpact": "channel"},
    "gfsec_fima_gate": {"failureImpact": "channel"},
    "gfsec_fima_load": {"failureImpact": "channel"},
    "gf_supplemental_collect": {"failureImpact": "channel"},
    "gf_supplemental_gate": {"failureImpact": "channel"},
    "gf_supplemental_load": {"failureImpact": "channel"},
    "database_backup": {"failureImpact": "warning"},
    "pages_verify": {"failureImpact": "warning"},
}


RUN_TEMPLATE = r'''param(
    [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$NodeRunDir,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$codeRoot = $env:ADVISOR_CODE_ROOT
if (-not $codeRoot) {
    $codeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
}
$bridge = Join-Path $codeRoot '节点脚本\00_调度框架\bridge_node.py'
$python = if ($env:ADVISOR_PYTHON_EXE) { $env:ADVISOR_PYTHON_EXE } else { 'python' }
$arguments = @('-u', '-X', 'utf8', $bridge, '--action', '__ACTION__', '--workspace-root', $WorkspaceRoot, '--run-id', $RunId, '--node-run-dir', $NodeRunDir)
if ($DryRun) { $arguments += '--dry-run' }
& $python @arguments
exit $LASTEXITCODE
'''


SKILL_TEMPLATE = r'''# {name}

节点ID：`{node_id}`  
执行入口：`run.ps1`  
超时：`{timeout}` 秒  
资源锁：`{resource_lock}`

## 目标

{purpose}

## 输入

- 工作区根目录、运行批次ID和节点运行目录。
- 上游节点通过校验的结果及统一运行环境变量。

## 输出

- 原子写入 `node_result.json`，并在尝试目录保留 `console.log` 与 `artifacts.json`。
- 节点业务输出：{node_output}
- 结果必须包含状态、退出码、输入指纹、输出水位、计数、警告、错误、日志和校验结论。

## 依赖节点

{dependencies}

## 业务口径

{business_rule}

## 进度单位

`{progress_unit}`。长任务必须持续输出已完成数量、总数和当前动作。

## 重试策略

进程级最大尝试次数为 `{attempts}`。{retry_rule}

## 幂等性

{idempotency}

## 失败影响

关键级别为 `{criticality}`。{failure_impact}

## 手工复跑

```powershell
& ".\00_每日数据更新并发布_唯一入口.bat" node {node_id} --run-id <run_id>
```

## 验证方法

检查节点结果中的退出码、`validation.status`、业务计数、水位和产物路径，不得只看进程退出码。

## 已知限制

{limitations}
'''


ACTION_DETAILS = {
    "environment_preflight": ("校验 Python、关键依赖、Git、Node 和内置 ADB。", "运行状态中的环境预检报告。"),
    "database_health": ("以 SQLite `quick_check` 和写锁探测确认主库可读写，不修改业务表。", "数据库健康报告和表水位。"),
    "source_readiness": ("每次启动先检查源端最新披露日；未就绪每 30 分钟重试，最多 6 次。", "数据源检查次数、目标交易日和就绪结论。"),
    "device_select": ("优先选择配置的实体手机；配置连接 ID 失效时自动发现唯一通过校验的非模拟器真机，且预检不主动拉起 App。", "本批次真机 ID、选择方式、预检结果和失败原因。"),
    "ttfund_incremental": ("天天基础信息按缺失或 7 天过期刷新，当前仓位每日刷新，调仓只补差异，业绩补到最新披露日。", "天天原始批次、标准化数据、缺口与渠道验收结果。"),
    "gffunds_performance": ("广发业绩曲线每日轻量刷新，以 App 披露为优先口径。", "广发业绩曲线批次和成功失败清单。"),
    "gffunds_metadata": ("广发基准和费率按缺失或 7 天过期刷新，脚本解析失败时才允许进入协议补采。", "广发基准费率元数据及缺口。"),
    "gffunds_collect": ("广发当前仓位和调仓按 App 最新披露增量采集，不在本节点重复更新底层基金净值。", "广发仓位、调仓批次及对象级结果。"),
    "gffunds_gate": ("只有广发必需输出、对象计数和批次状态均通过，才允许后续入库。", "广发渠道验收结论和阻断缺口。"),
    "gfsec_fima_collect": ("匿名公开接口发现财富管家全部产品实例，按底层组合并发采集官方模型仓位、业绩和调仓接口。", "产品目录、当前模型基金权重、业绩、调仓接口结果和逐组合成功失败计数。"),
    "gfsec_fima_gate": ("计划数、处理数、成功失败恒等式及核心覆盖率通过后，才允许财富管家批次入库。", "广发证券财富管家批次验收结论和阻断缺口。"),
    "gfsec_fima_load": ("只消费通过财富管家门禁的精确批次，在 main_db_write 资源锁内事务替换该渠道数据。", "主库中财富管家策略、业绩、持仓与调仓行数。"),
    "gf_supplemental_collect": ("采集广发证券历史接口匿名公开策略；广发银行已退出日常处理。", "广发证券历史接口的精确批次摘要、覆盖报告、原始快照和标准化实体。"),
    "gf_supplemental_gate": ("检查广发证券历史接口的策略数量、库存留存和业绩覆盖；推荐基金清单不得当成持仓。", "门禁结果及本轮允许入库的广发证券来源清单。"),
    "gf_supplemental_load": ("只事务加载通过门禁的广发证券历史接口批次；失败时保留旧库。", "主库中可追溯到 gfsec_robot 来源的广发证券策略和业绩边界。"),
    "process_load": ("仅消费通过渠道验收的标准化批次，使用事务增量写入主库。", "主库入库计数、前后水位和事务结果。"),
    "fund_nav": ("新基金补成立以来完整净值，已有基金按各自最新净值日补增量；明确空返回与失败分开统计。", "基金净值批次、基金级结果和主库最新净值日。"),
    "fund_lookthrough": ("按既有基金主份额、持仓穿透和公开分类规则更新，不改变历史分类口径。", "基金穿透、分类快照和质量统计。"),
    "index_benchmark": ("按业务基线中的指数资产分类解析策略基准，保留映射置信度和未映射成分。", "指数行情、基准资产配置和分类水位。"),
    "strategy_governance": ("统一治理策略生命周期、重复调仓、收益风险指标、基准风险资产权重和可比分类。", "治理记录、策略绩效快照和分类结果。"),
    "report_build": ("每日只重建最小发布包需要的临时报表源，不覆盖正式完整报表目录。", "最小发布源目录、基础数据部署清单和页面构建校验。"),
    "data_audit": ("按规则库稽核刚生成的数据包；不得通过删除、跳过或弱化规则消除问题。", "数据稽核报告、规则问题和业务缺口。"),
    "database_backup": ("仅当前置数据与稽核成功后使用 SQLite Backup API 备份，整个备份目录最多只保留最近一个已验证成功版本。", "一致性数据库备份及滚动保留结果。"),
    "publish": ("只发布通过门禁的正式结果，提交并推送最小发布集仓库。", "最小发布集、Git 提交和推送结果。"),
    "pages_verify": ("以远端 `version.json` 的 `buildId` 与本地发布版本一致作为发布完成依据。", "Pages 远端版本验证报告。"),
    "runtime_initialize": ("建立相对路径运行布局、兼容连接并执行首次完整检查。", "初始化与工作区检查报告。"),
    "runtime_check": ("只检查环境、路径、数据库、设备和网络，不执行每日采集。", "工作区健康检查报告。"),
    "migration_package": ("按白名单生成可迁移运行工作区，数据库使用一致性快照。", "迁移目录、清单、哈希和数据库基线。"),
    "runtime_update": ("只快进更新程序代码，执行兼容性与语法测试，失败自动恢复旧提交。", "代码更新前后提交和验证报告。"),
    "runtime_rollback": ("仅在数据库版本兼容时回退到上一个已验证程序提交。", "程序回退提交和兼容性验证。"),
    "component_info": ("记录天天复合采集器内部子阶段的责任边界，不单独执行数据写入。", "子阶段说明和排障入口。"),
}


def main() -> None:
    pipeline_nodes = []
    for node_id, name, relative, action, dependencies, criticality, timeout, attempts, resource_lock, unit, daily in NODES:
        directory = NODE_ROOT / Path(relative)
        (directory / "src").mkdir(parents=True, exist_ok=True)
        (directory / "tests").mkdir(parents=True, exist_ok=True)
        manifest = {
            "schemaVersion": 1,
            "id": node_id,
            "name": name,
            "phase": relative.split("/")[0],
            "entrypoint": "run.ps1",
            "dependencies": dependencies,
            "criticality": criticality,
            "timeoutSeconds": timeout,
            "maxProcessAttempts": attempts,
            "resourceLock": resource_lock,
            "supportsResume": True,
            "progressUnit": unit,
            "validator": {"type": "node_result", "requiredStatus": "passed"},
            "daily": daily,
            "action": action,
        }
        manifest.update(NODE_OVERRIDES.get(node_id, {}))
        (directory / "node.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (directory / "run.ps1").write_text(RUN_TEMPLATE.replace("__ACTION__", action), encoding="utf-8-sig")
        business_rule, node_output = ACTION_DETAILS[action]
        purpose = f"以独立节点执行“{name}”，形成可重试、可验证、可追溯的故障边界。"
        limitations = "无。"
        if action == "component_info":
            purpose = "说明天天复合采集节点中的业务子阶段；日常由天天批次验收节点统一驱动并记录具体失败子阶段。"
            limitations = "当前为复合采集器的子阶段说明节点，不单独进入每日DAG；排障时按天天批次摘要中的失败阶段定向调用原采集程序。"
        skill = SKILL_TEMPLATE.format(
            name=name,
            purpose=purpose,
            dependencies="、".join(dependencies) if dependencies else "无。",
            progress_unit=unit,
            attempts=attempts,
            criticality=criticality,
            node_id=node_id,
            limitations=limitations,
            timeout=timeout,
            resource_lock=resource_lock or "无",
            node_output=node_output,
            business_rule=business_rule,
            retry_rule=(
                "设备对象失败时在同一真机内宽松重试，剩余项进入第二批次补采和最终缺口清单。"
                if resource_lock == "device"
                else "节点失败后由调度器按清单重试；不允许把输出校验失败当作成功。"
            ),
            idempotency=(
                "同一批次重复执行使用事务或批次目录覆盖，只有完整验证后才提交；输入指纹变化会使下游失效。"
                if resource_lock == "main_db_write"
                else "输入指纹一致、结果文件存在且验证通过时可跳过；重新执行不得制造重复业务记录。"
            ),
            failure_impact=(
                "发布失败保留数据成功状态和最近成功备份，只允许从交付节点恢复。"
                if criticality == "publish"
                else (
                    "失败只记 warning，不否定已经完成的数据更新或发布。"
                    if NODE_OVERRIDES.get(node_id, {}).get("failureImpact") == "warning"
                    else (
                        "失败只影响当前渠道；其他可用渠道继续执行。"
                        if NODE_OVERRIDES.get(node_id, {}).get("failureImpact") == "channel"
                        else "失败会阻断依赖节点以及成功备份、发布，已通过验收的上游节点保留供续跑。"
                    )
                )
            ),
        )
        (directory / "SKILL.md").write_text(skill, encoding="utf-8")
        (directory / "src" / "README.md").write_text("# 节点实现\n\n当前节点通过 `run.ps1` 调用共享生产程序；后续业务实现只允许在本目录或共享组件中维护。\n", encoding="utf-8")
        (directory / "tests" / "README.md").write_text("# 节点测试\n\n节点契约、dry-run和失败恢复测试由调度框架测试统一覆盖。\n", encoding="utf-8")
        pipeline_nodes.append(
            {
                "id": node_id,
                "directory": relative,
                "dependencies": dependencies,
                "enabledWhen": {"daily": daily},
            }
        )

    payload = {
        "schemaVersion": 1,
        "version": "node_pipeline_v3_20260724_minimal_publish",
        "nodes": pipeline_nodes,
        "initializeNodes": ["runtime_initialize"],
        "checkNodes": ["preflight_environment", "preflight_database", "runtime_check"],
    }
    (NODE_ROOT / "pipeline.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rows = ["# 节点索引", "", "| 节点ID | 名称 | 目录 | 日常执行 |", "| --- | --- | --- | --- |"]
    for node_id, name, relative, _action, _dependencies, _criticality, _timeout, _attempts, _lock, _unit, daily in NODES:
        rows.append(f"| `{node_id}` | {name} | `{relative}` | {'是' if daily else '否'} |")
    (NODE_ROOT / "节点索引.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
