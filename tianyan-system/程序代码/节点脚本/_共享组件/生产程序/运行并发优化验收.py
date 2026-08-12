# -*- coding: utf-8 -*-
"""
并发优化验收入口。

这个脚本只负责编排和汇总，不直接改写业务源码。写库类步骤串行执行；
只读校验步骤并发执行，避免多个进程同时修改同一份 SQLite 或页面数据包。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())


@dataclass(frozen=True)
class TaskSpec:
    name: str
    command: list[str]
    script: Path | None = None
    required: bool = False
    mutates_data: bool = False
    description: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="并发运行报告系统优化后的验收与数据质量检查")
    parser.add_argument("--db", default=str(PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"))
    parser.add_argument("--site-dir", default=str(PROJECT_ROOT / "site" / "basic_data"))
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs" / "parallel_optimization_acceptance"))
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=1800, help="单个子任务超时时间，秒")
    parser.add_argument("--dry-run", action="store_true", help="只输出任务计划，不实际执行")
    parser.add_argument("--strict", action="store_true", help="有缺失脚本或警告时也返回非零")
    parser.add_argument("--skip-economic-exposure-build", action="store_true", help="跳过基金经济暴露快照构建")
    parser.add_argument("--skip-field-dictionary-build", action="store_true", help="跳过字段字典页面数据包构建")
    parser.add_argument("--include-site-quality-build", action="store_true", help="同时刷新基础数据质量包")
    parser.add_argument("--include-deploy-manifest", action="store_true", help="同时刷新部署清单")
    return parser.parse_args()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def ensure_output_dir(output_root: Path) -> Path:
    now = dt.datetime.now().astimezone()
    date_dir = now.strftime("%Y-%m-%d")
    run_id = now.strftime("%Y%m%dT%H%M%S%z")
    output_dir = output_root / date_dir / run_id
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)
    return output_dir


def python_cmd(script: Path, *args: str) -> list[str]:
    return [sys.executable, "-X", "utf8", str(script), *args]


def build_tasks(args: argparse.Namespace, output_dir: Path) -> tuple[list[TaskSpec], list[TaskSpec]]:
    db = Path(args.db)
    site_dir = Path(args.site_dir)
    site_data_dir = site_dir / "data"
    scripts = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"

    writer_tasks: list[TaskSpec] = []
    if not args.skip_economic_exposure_build:
        script = scripts / "构建基金经济暴露快照.py"
        cmd = python_cmd(
            script,
            "--db",
            str(db),
            "--output-dir",
            str(output_dir / "fund_economic_exposure"),
        )
        if args.dry_run:
            cmd.append("--dry-run")
        writer_tasks.append(
            TaskSpec(
                name="01_build_fund_economic_exposure",
                command=cmd,
                script=script,
                required=False,
                mutates_data=not args.dry_run,
                description="构建或预检基金经济暴露快照，供资产/行业/主题穿透口径复用",
            )
        )

    if not args.skip_field_dictionary_build:
        script = scripts / "构建字段字典与指标口径.py"
        cmd = python_cmd(
            script,
            "--db",
            str(db),
            "--site-data-dir",
            str(site_data_dir),
        )
        if args.dry_run:
            cmd.append("--dry-run")
        writer_tasks.append(
            TaskSpec(
                name="02_build_field_dictionary_pack",
                command=cmd,
                script=script,
                required=False,
                mutates_data=not args.dry_run,
                description="构建页面字段字典与指标口径数据包，供单指标质量分析和字段说明复用",
            )
        )

    parallel_tasks: list[TaskSpec] = [
        TaskSpec(
            name="10_validate_fund_economic_exposure",
            command=python_cmd(
                scripts / "校验基金经济暴露质量.py",
                "--db",
                str(db),
                "--output-dir",
                str(output_dir / "fund_economic_exposure_quality"),
            ),
            script=scripts / "校验基金经济暴露质量.py",
            required=False,
            description="校验 ETF/FOF/QDII-FOF、固收指数等基金穿透与分类质量",
        ),
        TaskSpec(
            name="11_validate_strategy_governance",
            command=python_cmd(
                scripts / "校验策略治理一致性.py",
                "--db",
                str(db),
                "--output-dir",
                str(output_dir / "strategy_governance_quality"),
            ),
            script=scripts / "校验策略治理一致性.py",
            required=False,
            description="校验测试策略、信号策略、目标盈 stopped、调仓去重等治理口径",
        ),
        TaskSpec(
            name="12_validate_field_dictionary",
            command=python_cmd(
                scripts / "校验字段口径覆盖.py",
                "--db",
                str(db),
                "--site-data-dir",
                str(site_data_dir),
                "--output-dir",
                str(output_dir / "field_dictionary_quality"),
            ),
            script=scripts / "校验字段口径覆盖.py",
            required=False,
            description="校验字段口径、来源、覆盖率和页面指标字典完整性",
        ),
    ]

    if args.include_site_quality_build:
        parallel_tasks.append(
            TaskSpec(
                name="13_build_basic_data_quality_pack",
                command=python_cmd(
                    scripts / "构建基础数据质量包.py",
                    "--site-dir",
                    str(site_dir),
                    "--fail-on-error",
                ),
                script=scripts / "构建基础数据质量包.py",
                required=False,
                mutates_data=True,
                description="刷新页面端基础数据质量包",
            )
        )

    if args.include_deploy_manifest:
        deploy_root = site_dir.parent
        parallel_tasks.append(
            TaskSpec(
                name="14_write_deploy_manifest",
                command=python_cmd(
                    scripts / "write_analysis_platform_deploy_manifest.py",
                    "--deploy-dir",
                    str(deploy_root),
                    "--page-set",
                    "basic_data",
                ),
                script=scripts / "write_analysis_platform_deploy_manifest.py",
                required=False,
                mutates_data=True,
                description="刷新 basic_data 部署清单",
            )
        )

    return writer_tasks, parallel_tasks


def task_to_record(task: TaskSpec) -> dict[str, object]:
    return {
        "name": task.name,
        "script": rel(task.script) if task.script else None,
        "exists": task.script.exists() if task.script else True,
        "required": task.required,
        "mutates_data": task.mutates_data,
        "description": task.description,
        "command": task.command,
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_task(task: TaskSpec, logs_dir: Path, timeout: int) -> dict[str, object]:
    started = dt.datetime.now().astimezone()
    log_path = logs_dir / f"{task.name}.log"
    if task.script is not None and not task.script.exists():
        message = f"SKIP missing script: {rel(task.script)}"
        log_path.write_text(message + "\n", encoding="utf-8")
        return {
            "name": task.name,
            "status": "missing",
            "returncode": None,
            "elapsed_seconds": 0.0,
            "log": str(log_path),
            "started_at": started.isoformat(),
            "finished_at": dt.datetime.now().astimezone().isoformat(),
            "script": rel(task.script),
            "description": task.description,
        }

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    started_mono = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write("$ " + " ".join(task.command) + "\n\n")
        log.flush()
        try:
            proc = subprocess.run(
                task.command,
                cwd=str(PROJECT_ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=timeout,
                check=False,
            )
            status = "ok" if proc.returncode == 0 else "failed"
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            log.write(f"\nTIMEOUT after {timeout}s\n")
            status = "timeout"
            returncode = None

    elapsed = round(time.monotonic() - started_mono, 3)
    return {
        "name": task.name,
        "status": status,
        "returncode": returncode,
        "elapsed_seconds": elapsed,
        "log": str(log_path),
        "started_at": started.isoformat(),
        "finished_at": dt.datetime.now().astimezone().isoformat(),
        "script": rel(task.script) if task.script else None,
        "description": task.description,
    }


def run_tasks_serial(tasks: Iterable[TaskSpec], logs_dir: Path, timeout: int) -> list[dict[str, object]]:
    results = []
    for task in tasks:
        results.append(run_task(task, logs_dir, timeout))
    return results


def run_tasks_parallel(tasks: list[TaskSpec], logs_dir: Path, timeout: int, max_workers: int) -> list[dict[str, object]]:
    if not tasks:
        return []
    workers = max(1, min(max_workers, len(tasks)))
    results: list[dict[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_task = {executor.submit(run_task, task, logs_dir, timeout): task for task in tasks}
        for future in concurrent.futures.as_completed(future_to_task):
            results.append(future.result())
    return sorted(results, key=lambda item: str(item["name"]))


def write_markdown_summary(output_dir: Path, summary: dict[str, object]) -> None:
    lines = [
        "# 并发优化验收摘要",
        "",
        f"- 状态：{summary['status']}",
        f"- 生成时间：{summary['generated_at']}",
        f"- 数据库：{summary['database']}",
        f"- 页面目录：{summary['site_dir']}",
        f"- dry-run：{summary['dry_run']}",
        "",
        "## 任务结果",
        "",
        "| 任务 | 状态 | 耗时秒 | 日志 |",
        "| --- | --- | ---: | --- |",
    ]
    for item in summary["results"]:
        log = item.get("log") or ""
        lines.append(
            f"| {item['name']} | {item['status']} | {item.get('elapsed_seconds', '')} | {log} |"
        )

    warnings = summary.get("warnings") or []
    if warnings:
        lines.extend(["", "## 警告", ""])
        for warning in warnings:
            lines.append(f"- {warning}")

    output_dir.joinpath("summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = ensure_output_dir(Path(args.output_root))
    writer_tasks, parallel_tasks = build_tasks(args, output_dir)
    all_tasks = writer_tasks + parallel_tasks

    plan = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "project_root": str(PROJECT_ROOT),
        "database": str(Path(args.db)),
        "site_dir": str(Path(args.site_dir)),
        "dry_run": bool(args.dry_run),
        "writer_tasks": [task_to_record(task) for task in writer_tasks],
        "parallel_tasks": [task_to_record(task) for task in parallel_tasks],
    }
    write_json(output_dir / "task_plan.json", plan)

    missing_required = [
        rel(task.script)
        for task in all_tasks
        if task.required and task.script is not None and not task.script.exists()
    ]
    missing_optional = [
        rel(task.script)
        for task in all_tasks
        if not task.required and task.script is not None and not task.script.exists()
    ]

    if args.dry_run:
        results = []
        for task in all_tasks:
            status = "missing" if task.script is not None and not task.script.exists() else "planned"
            results.append(
                {
                    "name": task.name,
                    "status": status,
                    "returncode": None,
                    "elapsed_seconds": 0.0,
                    "log": None,
                    "script": rel(task.script) if task.script else None,
                    "description": task.description,
                }
            )
    else:
        logs_dir = output_dir / "logs"
        writer_results = run_tasks_serial(writer_tasks, logs_dir, args.timeout)
        parallel_results = run_tasks_parallel(parallel_tasks, logs_dir, args.timeout, args.max_workers)
        results = writer_results + parallel_results

    failed = [item for item in results if item["status"] in {"failed", "timeout"}]
    warnings = []
    if missing_optional:
        warnings.append("缺少可选优化脚本：" + "、".join(missing_optional))
    if missing_required:
        warnings.append("缺少必需脚本：" + "、".join(missing_required))

    if failed or missing_required:
        status = "failed"
    elif warnings:
        status = "warn"
    else:
        status = "ready"

    summary = {
        "status": status,
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "project_root": str(PROJECT_ROOT),
        "database": str(Path(args.db)),
        "site_dir": str(Path(args.site_dir)),
        "dry_run": bool(args.dry_run),
        "output_dir": str(output_dir),
        "results": results,
        "warnings": warnings,
    }
    write_json(output_dir / "summary.json", summary)
    write_markdown_summary(output_dir, summary)

    print(f"status={status}")
    print(f"output_dir={output_dir}")
    if warnings:
        for warning in warnings:
            print(f"warning={warning}")

    if failed:
        return 1
    if args.strict and (warnings or status != "ready"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
