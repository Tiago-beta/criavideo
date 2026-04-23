-- Auto channel pilot: autonomous create+publish+analyze growth loop per YouTube account
CREATE TABLE IF NOT EXISTS auto_channel_pilots (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    social_account_id INTEGER NOT NULL UNIQUE REFERENCES social_accounts(id) ON DELETE CASCADE,
    auto_schedule_id INTEGER REFERENCES auto_schedules(id) ON DELETE SET NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT false,
    analysis_interval_hours INTEGER NOT NULL DEFAULT 24,
    min_pending_themes INTEGER NOT NULL DEFAULT 5,
    themes_per_cycle INTEGER NOT NULL DEFAULT 4,
    last_analysis_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_error TEXT,
    last_summary JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_channel_pilots_user ON auto_channel_pilots(user_id);
CREATE INDEX IF NOT EXISTS idx_auto_channel_pilots_enabled ON auto_channel_pilots(is_enabled);
