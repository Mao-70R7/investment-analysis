from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RawSnapshot:
    snapshot_id: str
    channel_id: str
    collector_name: str
    access_level: str
    captured_at: str
    source_url: str
    http_status: int | None
    raw_path: str
    content_type: str | None
    content_hash: str
    parse_status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyMaster:
    channel_id: str
    source_strategy_id: str
    strategy_name: str
    advisor_name: str | None
    strategy_type: str | None
    risk_level: str | None
    launch_date: str | None
    suggested_holding_period: str | None
    minimum_amount: float | None
    advisory_fee_rate: str | None
    benchmark: str | None
    tags: list[str]
    strategy_description: str | None
    status: str | None
    source_url: str | None
    first_seen_at: str
    last_seen_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyFundSnapshot:
    snapshot_id: str
    channel_id: str
    source_strategy_id: str
    position_date: str
    disclosure_date: str | None
    fund_code: str
    fund_name: str
    fund_asset_type: str | None
    fund_group_name: str | None
    fund_weight: float | None
    fund_nav: float | None
    fund_nav_date: str | None
    is_precise_weight: bool
    is_login_required: bool
    source_url: str | None
    raw_record_hash: str
    confidence_level: str
    access_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

