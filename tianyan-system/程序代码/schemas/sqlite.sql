PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channel (
    channel_id TEXT PRIMARY KEY,
    channel_name TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    official_site_url TEXT,
    login_required_level TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    collector_name TEXT NOT NULL,
    access_level TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source_url TEXT,
    http_status INTEGER,
    raw_path TEXT NOT NULL,
    content_type TEXT,
    content_hash TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    FOREIGN KEY (channel_id) REFERENCES channel(channel_id)
);

CREATE TABLE IF NOT EXISTS strategy_master (
    channel_id TEXT NOT NULL,
    source_strategy_id TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    advisor_name TEXT,
    strategy_type TEXT,
    risk_level TEXT,
    launch_date TEXT,
    suggested_holding_period TEXT,
    minimum_amount REAL,
    advisory_fee_rate TEXT,
    benchmark TEXT,
    tags TEXT,
    strategy_description TEXT,
    status TEXT,
    source_url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (channel_id, source_strategy_id),
    FOREIGN KEY (channel_id) REFERENCES channel(channel_id)
);

CREATE TABLE IF NOT EXISTS strategy_performance_daily (
    channel_id TEXT NOT NULL,
    source_strategy_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    nav REAL,
    daily_return REAL,
    cumulative_return REAL,
    benchmark_return REAL,
    index_return REAL,
    max_drawdown REAL,
    source_snapshot_id TEXT,
    PRIMARY KEY (channel_id, source_strategy_id, trade_date),
    FOREIGN KEY (channel_id, source_strategy_id) REFERENCES strategy_master(channel_id, source_strategy_id),
    FOREIGN KEY (source_snapshot_id) REFERENCES raw_snapshot(snapshot_id)
);

CREATE TABLE IF NOT EXISTS strategy_fund_snapshot (
    snapshot_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    source_strategy_id TEXT NOT NULL,
    position_date TEXT NOT NULL,
    disclosure_date TEXT,
    fund_code TEXT NOT NULL,
    fund_name TEXT NOT NULL,
    fund_asset_type TEXT,
    fund_group_name TEXT,
    fund_weight REAL,
    fund_nav REAL,
    fund_nav_date TEXT,
    is_precise_weight INTEGER NOT NULL DEFAULT 0,
    is_login_required INTEGER NOT NULL DEFAULT 1,
    source_url TEXT,
    raw_record_hash TEXT NOT NULL,
    confidence_level TEXT NOT NULL,
    access_level TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, fund_code),
    FOREIGN KEY (channel_id, source_strategy_id) REFERENCES strategy_master(channel_id, source_strategy_id),
    FOREIGN KEY (snapshot_id) REFERENCES raw_snapshot(snapshot_id)
);

CREATE TABLE IF NOT EXISTS strategy_rebalance_event (
    rebalance_event_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    source_strategy_id TEXT NOT NULL,
    rebalance_date TEXT NOT NULL,
    previous_position_date TEXT,
    new_position_date TEXT,
    disclosure_date TEXT,
    event_title TEXT,
    event_reason TEXT,
    source_url TEXT,
    source_snapshot_id TEXT,
    confidence_level TEXT NOT NULL,
    FOREIGN KEY (channel_id, source_strategy_id) REFERENCES strategy_master(channel_id, source_strategy_id),
    FOREIGN KEY (source_snapshot_id) REFERENCES raw_snapshot(snapshot_id)
);

CREATE TABLE IF NOT EXISTS strategy_rebalance_fund_delta (
    rebalance_event_id TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    fund_name TEXT,
    before_weight REAL,
    after_weight REAL,
    weight_delta REAL,
    action_type TEXT NOT NULL,
    PRIMARY KEY (rebalance_event_id, fund_code),
    FOREIGN KEY (rebalance_event_id) REFERENCES strategy_rebalance_event(rebalance_event_id)
);

CREATE TABLE IF NOT EXISTS fund_public_dim (
    fund_code TEXT PRIMARY KEY,
    fund_name TEXT NOT NULL,
    fund_company TEXT,
    fund_type TEXT,
    tracking_index TEXT,
    theme_tags TEXT,
    latest_nav REAL,
    latest_nav_date TEXT,
    status TEXT,
    source TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fund_snapshot_strategy_date
ON strategy_fund_snapshot(channel_id, source_strategy_id, position_date);

CREATE INDEX IF NOT EXISTS idx_rebalance_strategy_date
ON strategy_rebalance_event(channel_id, source_strategy_id, rebalance_date);

