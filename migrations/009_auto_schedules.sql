-- Auto-schedules: automated video creation + publishing
CREATE TABLE IF NOT EXISTS auto_schedules (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    name TEXT NOT NULL,
    video_type TEXT NOT NULL DEFAULT 'narration',       -- 'narration' | 'music'
    creation_mode TEXT NOT NULL DEFAULT 'auto',          -- 'auto' | 'manual'
    platform TEXT NOT NULL DEFAULT 'youtube',            -- 'youtube' | 'tiktok' | 'instagram'
    social_account_id INTEGER REFERENCES social_accounts(id),
    frequency TEXT NOT NULL DEFAULT 'daily',             -- 'daily' | 'weekly'
    time_utc TEXT NOT NULL DEFAULT '14:00',              -- HH:MM
    day_of_week INTEGER DEFAULT 0,                       -- 0=Mon..6=Sun (weekly only)
    default_settings JSONB DEFAULT '{}',                 -- tone, voice, style_prompt, duration_seconds, aspect_ratio, pause_level
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auto_schedule_themes (
    id SERIAL PRIMARY KEY,
    auto_schedule_id INTEGER NOT NULL REFERENCES auto_schedules(id) ON DELETE CASCADE,
    theme TEXT NOT NULL,
    custom_settings JSONB,                               -- per-theme overrides (manual mode)
    status TEXT NOT NULL DEFAULT 'pending',               -- 'pending' | 'processing' | 'completed' | 'failed'
    video_project_id INTEGER REFERENCES video_projects(id),
    error_message TEXT,
    position INTEGER NOT NULL DEFAULT 0,                  -- order in playlist (FIFO)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_schedules_user ON auto_schedules(user_id);
CREATE INDEX IF NOT EXISTS idx_auto_schedules_active ON auto_schedules(is_active);
CREATE INDEX IF NOT EXISTS idx_auto_schedule_themes_schedule ON auto_schedule_themes(auto_schedule_id);
CREATE INDEX IF NOT EXISTS idx_auto_schedule_themes_status ON auto_schedule_themes(status);
