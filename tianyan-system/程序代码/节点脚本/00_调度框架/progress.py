from __future__ import annotations

import json
import re
import statistics
from typing import Any


PROGRESS_PREFIX = "PROGRESS "
LEGACY_PROGRESS = re.compile(r"(?P<done>\d+)\s*/\s*(?P<total>\d+)")
PHASE_PREFIX = re.compile(r"^\d+[_ -]*")


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "待估算"
    value = max(0, int(round(float(seconds))))
    hours, remainder = divmod(value, 3600)
    minutes, seconds_value = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_value:02d}"


def progress_fraction(payload: dict[str, Any] | None) -> float | None:
    if not isinstance(payload, dict):
        return None
    completed = payload.get("completed")
    total = payload.get("total")
    if not isinstance(completed, (int, float)) or not isinstance(total, (int, float)):
        return None
    if total <= 0:
        return None
    return max(0.0, min(1.0, float(completed) / float(total)))


def _phase_groups(nodes: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    groups: list[tuple[int, int, str]] = []
    for index, node in enumerate(nodes):
        phase = str(node.get("phase") or "未分组")
        if groups and groups[-1][2] == phase:
            start, _end, existing = groups[-1]
            groups[-1] = (start, index + 1, existing)
        else:
            groups.append((index, index + 1, phase))
    return groups


def build_pipeline_status(
    nodes: list[dict[str, Any]],
    current_index: int,
    node_progress: dict[str, Any] | None,
    *,
    node_elapsed_seconds: float,
    total_elapsed_seconds: float,
    duration_estimates: dict[str, float] | None = None,
) -> dict[str, Any]:
    if not nodes:
        raise ValueError("pipeline status requires at least one node")
    if current_index < 0 or current_index >= len(nodes):
        raise IndexError(f"pipeline node index out of range: {current_index}")

    estimates = {
        str(key): float(value)
        for key, value in (duration_estimates or {}).items()
        if isinstance(value, (int, float)) and float(value) > 0
    }
    fraction = progress_fraction(node_progress)
    current_fraction = fraction if fraction is not None else 0.0
    overall_fraction = (current_index + current_fraction) / len(nodes)

    groups = _phase_groups(nodes)
    stage_index = next(
        index
        for index, (start, end, _phase) in enumerate(groups)
        if start <= current_index < end
    )
    stage_start, stage_end, raw_stage_name = groups[stage_index]
    stage_total = stage_end - stage_start
    stage_completed = current_index - stage_start
    stage_fraction = (stage_completed + current_fraction) / stage_total

    known_estimates = list(estimates.values())
    fallback_estimate: float | None = (
        float(statistics.median(known_estimates)) if known_estimates else None
    )
    if fallback_estimate is None and current_index > 0 and total_elapsed_seconds > 0:
        fallback_estimate = float(total_elapsed_seconds) / current_index

    current_node = nodes[current_index]
    current_node_id = str(current_node.get("id") or "")
    current_estimate = estimates.get(current_node_id) or fallback_estimate
    if fraction is not None and fraction > 0:
        current_remaining = max(
            0.0,
            float(node_elapsed_seconds) * (1.0 - fraction) / fraction,
        )
        eta_basis = "当前节点进度+历史节点耗时"
    elif current_estimate is not None:
        current_remaining = max(0.0, current_estimate - float(node_elapsed_seconds))
        eta_basis = "历史节点耗时"
    else:
        current_remaining = None
        eta_basis = "无足够样本"

    future_remaining = 0.0
    future_estimable = True
    for node in nodes[current_index + 1 :]:
        estimate = estimates.get(str(node.get("id") or "")) or fallback_estimate
        if estimate is None:
            future_estimable = False
            break
        future_remaining += estimate
    estimated_remaining = (
        current_remaining + future_remaining
        if current_remaining is not None and future_estimable
        else None
    )

    node_completed = node_progress.get("completed") if isinstance(node_progress, dict) else None
    node_total = node_progress.get("total") if isinstance(node_progress, dict) else None
    node_unit = node_progress.get("unit") if isinstance(node_progress, dict) else None
    node_message = node_progress.get("message") if isinstance(node_progress, dict) else None
    return {
        "stageIndex": stage_index + 1,
        "stageCount": len(groups),
        "stageName": PHASE_PREFIX.sub("", raw_stage_name) or raw_stage_name,
        "stageRawName": raw_stage_name,
        "stageCompletedNodes": stage_completed,
        "stageTotalNodes": stage_total,
        "stagePercent": round(stage_fraction * 100.0, 1),
        "completedNodes": current_index,
        "totalNodes": len(nodes),
        "overallPercent": round(overall_fraction * 100.0, 1),
        "nodePercent": round(fraction * 100.0, 1) if fraction is not None else None,
        "nodeCompleted": node_completed,
        "nodeTotal": node_total,
        "nodeUnit": node_unit,
        "nodeMessage": node_message,
        "nodeElapsedSeconds": int(max(0, node_elapsed_seconds)),
        "totalElapsedSeconds": int(max(0, total_elapsed_seconds)),
        "estimatedRemainingSeconds": (
            int(round(estimated_remaining)) if estimated_remaining is not None else None
        ),
        "etaBasis": eta_basis,
    }


def render_pipeline_status(
    label: str,
    node: dict[str, Any],
    status: dict[str, Any],
    *,
    device: str | None = None,
    log_path: str | None = None,
) -> str:
    node_percent = status.get("nodePercent")
    if node_percent is None:
        node_progress_text = "待节点上报"
    else:
        completed = status.get("nodeCompleted")
        total = status.get("nodeTotal")
        unit = status.get("nodeUnit") or "项"
        detail = (
            f"，{completed}/{total}{unit}"
            if completed is not None and total is not None
            else ""
        )
        node_progress_text = f"{node_percent:.1f}%{detail}"
    parts = [
        f"[{label}]",
        f"阶段={status['stageIndex']}/{status['stageCount']} {status['stageName']}",
        (
            f"整体={status['overallPercent']:.1f}%"
            f"（已完成{status['completedNodes']}/{status['totalNodes']}节点）"
        ),
        (
            f"阶段进度={status['stagePercent']:.1f}%"
            f"（已完成{status['stageCompletedNodes']}/{status['stageTotalNodes']}节点）"
        ),
        f"当前节点={node.get('id')} {node.get('name')}",
        f"节点进度={node_progress_text}",
        f"节点已运行={format_duration(status.get('nodeElapsedSeconds'))}",
        f"整体预计剩余={format_duration(status.get('estimatedRemainingSeconds'))}",
    ]
    message = str(status.get("nodeMessage") or "").strip()
    if message:
        parts.append(f"节点状态={message}")
    if device:
        parts.append(f"设备={device}")
    if log_path:
        parts.append(f"日志={log_path}")
    return parts[0] + " " + " | ".join(parts[1:])


def parse_progress(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if text.startswith(PROGRESS_PREFIX):
        try:
            payload = json.loads(text[len(PROGRESS_PREFIX) :])
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None
    if "progress" in text.lower() or "已完成" in text or "完成策略" in text:
        match = LEGACY_PROGRESS.search(text)
        if match:
            return {"completed": int(match.group("done")), "total": int(match.group("total")), "message": text}
    return None


def render_progress(node_name: str, payload: dict[str, Any]) -> str:
    completed = payload.get("completed")
    total = payload.get("total")
    unit = payload.get("unit") or "项"
    message = payload.get("message") or ""
    if isinstance(completed, int) and isinstance(total, int) and total > 0:
        percent = int(completed * 100 / total)
        details = [f"{completed}/{total} {unit}"]
        for key, label in (("success", "成功"), ("failed", "失败"), ("skipped", "跳过")):
            value = payload.get(key)
            if isinstance(value, int):
                details.append(f"{label}{value}")
        current = str(payload.get("current") or "").strip()
        if current:
            details.append(f"当前={current}")
        if message:
            details.append(str(message))
        return f"[节点进度 {percent:3d}%] {node_name}: " + " | ".join(details)
    return f"[节点进度] {node_name}: {message or json.dumps(payload, ensure_ascii=False)}"
