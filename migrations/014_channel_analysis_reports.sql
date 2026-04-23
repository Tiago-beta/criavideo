CREATE TABLE IF NOT EXISTS channel_analysis_reports (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    social_account_id INTEGER REFERENCES social_accounts(id) ON DELETE SET NULL,
    platform VARCHAR(20) NOT NULL DEFAULT 'youtube',
    account_label VARCHAR(255) NOT NULL DEFAULT '',
    platform_username VARCHAR(255) NOT NULL DEFAULT '',
    channel_title VARCHAR(255) NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_channel_analysis_reports_user_created
    ON channel_analysis_reports(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_channel_analysis_reports_account
    ON channel_analysis_reports(social_account_id);
