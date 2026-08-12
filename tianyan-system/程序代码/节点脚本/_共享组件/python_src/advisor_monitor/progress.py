from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "--"
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@dataclass
class ConsoleProgress:
    label: str
    total: int
    unit: str = "策略"
    started_monotonic: float | None = None

    def __post_init__(self) -> None:
        self.total = max(0, int(self.total))
        if self.started_monotonic is None:
            self.started_monotonic = time.monotonic()

    def snapshot(
        self,
        completed: int,
        *,
        success: int | None = None,
        failed: int | None = None,
        current: str | None = None,
        extra: str | None = None,
    ) -> dict[str, Any]:
        completed = min(max(0, int(completed)), self.total) if self.total else max(0, int(completed))
        elapsed = max(0.0, time.monotonic() - float(self.started_monotonic or 0.0))
        percent = completed * 100.0 / self.total if self.total else 100.0
        eta_seconds: float | None = None
        if completed > 0 and self.total > completed:
            eta_seconds = elapsed * (self.total - completed) / completed
        return {
            "label": self.label,
            "unit": self.unit,
            "completed": completed,
            "total": self.total,
            "percent": round(percent, 1),
            "success": success,
            "failed": failed,
            "elapsed_seconds": round(elapsed, 1),
            "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
            "current": current,
            "extra": extra,
        }

    def line(
        self,
        completed: int,
        *,
        success: int | None = None,
        failed: int | None = None,
        current: str | None = None,
        extra: str | None = None,
    ) -> str:
        row = self.snapshot(
            completed,
            success=success,
            failed=failed,
            current=current,
            extra=extra,
        )
        parts = [
            f"[PROGRESS] {row['label']}",
            f"已完成{row['unit']} {row['completed']}/{row['total']} ({row['percent']:.1f}%)",
        ]
        if row["success"] is not None:
            parts.append(f"成功 {row['success']}")
        if row["failed"] is not None:
            parts.append(f"失败 {row['failed']}")
        parts.append(f"已耗时 {format_duration(row['elapsed_seconds'])}")
        parts.append(f"预计剩余 {format_duration(row['eta_seconds'])}")
        if row["current"]:
            parts.append(f"当前 {row['current']}")
        if row["extra"]:
            parts.append(str(row["extra"]))
        return " | ".join(parts)

    def emit(
        self,
        completed: int,
        *,
        success: int | None = None,
        failed: int | None = None,
        current: str | None = None,
        extra: str | None = None,
    ) -> dict[str, Any]:
        row = self.snapshot(
            completed,
            success=success,
            failed=failed,
            current=current,
            extra=extra,
        )
        print(
            self.line(
                completed,
                success=success,
                failed=failed,
                current=current,
                extra=extra,
            ),
            flush=True,
        )
        return row
