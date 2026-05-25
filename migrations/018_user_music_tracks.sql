CREATE TABLE IF NOT EXISTS user_music_tracks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL DEFAULT 'tevoxi',
    source VARCHAR(50) NOT NULL DEFAULT 'inline',
    job_id VARCHAR(255) NOT NULL DEFAULT '',
    title VARCHAR(500) NOT NULL DEFAULT 'Sem titulo',
    lyrics_text TEXT NOT NULL DEFAULT '',
    duration DOUBLE PRECISION NOT NULL DEFAULT 0,
    language VARCHAR(20) NOT NULL DEFAULT 'pt-BR',
    mode VARCHAR(30) NOT NULL DEFAULT 'assistant',
    mood VARCHAR(100) NOT NULL DEFAULT '',
    vocalist VARCHAR(100) NOT NULL DEFAULT '',
    audio_url TEXT NOT NULL DEFAULT '',
    audio_path TEXT NOT NULL DEFAULT '',
    genres JSONB NOT NULL DEFAULT '[]'::jsonb,
    generation_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_music_tracks_user_id ON user_music_tracks(user_id);
CREATE INDEX IF NOT EXISTS idx_user_music_tracks_provider ON user_music_tracks(provider);
CREATE INDEX IF NOT EXISTS idx_user_music_tracks_source ON user_music_tracks(source);
CREATE INDEX IF NOT EXISTS idx_user_music_tracks_job_id ON user_music_tracks(job_id);
CREATE INDEX IF NOT EXISTS idx_user_music_tracks_created_at ON user_music_tracks(created_at DESC);