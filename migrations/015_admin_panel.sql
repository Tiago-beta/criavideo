-- Admin panel support: credits history, plan control and app access analytics.

ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS credits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS plan VARCHAR(20) NOT NULL DEFAULT 'free';
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS credit_usage (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    credits INTEGER NOT NULL,
    action TEXT NOT NULL,
    job_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credit_usage_user_id ON credit_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_usage_created_at ON credit_usage(created_at DESC);

CREATE TABLE IF NOT EXISTS page_views (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES auth_users(id) ON DELETE SET NULL,
    page TEXT NOT NULL,
    source_app VARCHAR(30) NOT NULL DEFAULT 'criavideo',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE page_views ADD COLUMN IF NOT EXISTS source_app VARCHAR(30) NOT NULL DEFAULT 'criavideo';
CREATE INDEX IF NOT EXISTS idx_page_views_source_app ON page_views(source_app);
CREATE INDEX IF NOT EXISTS idx_page_views_page_created_at ON page_views(page, created_at DESC);