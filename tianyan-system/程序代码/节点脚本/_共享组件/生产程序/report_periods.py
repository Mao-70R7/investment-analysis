from __future__ import annotations

from datetime import date, timedelta


def previous_completed_month(reference: date | None = None) -> str:
    """Return the last fully completed calendar month as YYYY-MM."""
    current = reference or date.today()
    first_day = current.replace(day=1)
    previous_day = first_day - timedelta(days=1)
    return f"{previous_day.year:04d}-{previous_day.month:02d}"


def compact_month(month: str) -> str:
    return month.replace("-", "")


def monthly_rebalance_report_page(month: str | None = None) -> str:
    resolved = month or previous_completed_month()
    return f"monthly-rebalance-report-{compact_month(resolved)}.html"


def monthly_rebalance_asset_directory(month: str | None = None) -> str:
    resolved = month or previous_completed_month()
    return f"monthly-rebalance-report-{compact_month(resolved)}"


def monthly_rebalance_snapshot_name(month: str | None = None) -> str:
    resolved = month or previous_completed_month()
    return f"monthly-rebalance-report-{compact_month(resolved)}.snapshot.json"
