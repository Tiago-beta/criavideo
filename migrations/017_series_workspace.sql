-- Series workspace foundation: parent series entity, episode mapping, chat threads and messages.

CREATE TABLE IF NOT EXISTS video_series (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL DEFAULT 'series',
    title VARCHAR(500) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    aspect_ratio VARCHAR(10) NOT NULL DEFAULT '16:9',
    language VARCHAR(20) NOT NULL DEFAULT 'pt-BR',
    target_duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    episode_count INTEGER NOT NULL DEFAULT 0,
    cover_image_path TEXT NOT NULL DEFAULT '',
    default_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    workspace_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_series_user_id ON video_series(user_id);
CREATE INDEX IF NOT EXISTS idx_video_series_kind ON video_series(kind);
CREATE INDEX IF NOT EXISTS idx_video_series_status ON video_series(status);

CREATE TABLE IF NOT EXISTS video_series_episodes (
    id SERIAL PRIMARY KEY,
    series_id INTEGER NOT NULL REFERENCES video_series(id) ON DELETE CASCADE,
    video_project_id INTEGER REFERENCES video_projects(id) ON DELETE SET NULL,
    season_number INTEGER NOT NULL DEFAULT 1,
    episode_number INTEGER NOT NULL DEFAULT 1,
    title VARCHAR(500) NOT NULL,
    synopsis TEXT NOT NULL DEFAULT '',
    script_text TEXT NOT NULL DEFAULT '',
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    storyboard JSONB NOT NULL DEFAULT '[]'::jsonb,
    timeline_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    selected_persona_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_series_episodes_series_id ON video_series_episodes(series_id);
CREATE INDEX IF NOT EXISTS idx_video_series_episodes_video_project_id ON video_series_episodes(video_project_id);
CREATE INDEX IF NOT EXISTS idx_video_series_episodes_status ON video_series_episodes(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_video_series_episodes_series_episode_order
    ON video_series_episodes(series_id, season_number, episode_number);

CREATE TABLE IF NOT EXISTS video_series_chat_threads (
    id SERIAL PRIMARY KEY,
    series_id INTEGER NOT NULL REFERENCES video_series(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL DEFAULT 'Novo bate-papo',
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_series_chat_threads_series_id ON video_series_chat_threads(series_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_video_series_chat_threads_default_per_series
    ON video_series_chat_threads(series_id)
    WHERE is_default = TRUE;

CREATE TABLE IF NOT EXISTS video_series_chat_messages (
    id SERIAL PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES video_series_chat_threads(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'assistant',
    content TEXT NOT NULL DEFAULT '',
    actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(30) NOT NULL DEFAULT 'completed',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_series_chat_messages_thread_id ON video_series_chat_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_video_series_chat_messages_role ON video_series_chat_messages(role);
CREATE INDEX IF NOT EXISTS idx_video_series_chat_messages_created_at ON video_series_chat_messages(created_at DESC);