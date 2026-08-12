from __future__ import annotations

import json
import re
from typing import Any


PROGRESS_PREFIX = "PROGRESS "
LEGACY_PROGRESS = re.compile(r"(?P<done>\d+)\s*/\s*(?P<total>\d+)")


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
        return f"[节点进度 {percent:3d}%] {node_name}: {completed}/{total} {unit} {message}".rstrip()
    return f"[节点进度] {node_name}: {message or json.dumps(payload, ensure_ascii=False)}"

