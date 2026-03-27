-- Levita Video: Initial Database Migration
-- Run against the existing Levita PostgreSQL database

-- Video status enum
DO $$ BEGIN
    CREATE TYPE video_status AS ENUM ('pending', 'generating_scenes', 'generating_clips', 'rendering', 'completed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE publish_status AS ENUM ('pending', 'uploading', 'published', 'failed', 'scheduled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE platform_type AS ENUM ('youtube', 'tiktok', 'instagram');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Video Projects
CREATE TABLE IF NOT EXISTS video_projects (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT DEFAULT '',
    tags JSONB DEFAULT '[]'::jsonb,
    style_prompt TEXT DEFAULT '',
    aspect_ratio VARCHAR(10) DEFAULT '16:9',
    status video_status DEFAULT 'pending',
    error_message TEXT,
    progress INTEGER DEFAULT 0,
    track_title VARCHAR(500),
    track_artist VARCHAR(500),
    track_duration REAL,
    lyrics_text TEXT,
    lyrics_words JSONB,
    audio_path TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_video_projects_user ON video_projects(user_id);

-- Video Scenes
CREATE TABLE IF NOT EXISTS video_scenes (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES video_projects(id) ON DELETE CASCADE,
    scene_index INTEGER NOT NULL,
    scene_type VARCHAR(20) DEFAULT 'image',
    prompt TEXT,
    image_path TEXT,
    clip_path TEXT,
    start_time REAL,
    end_time REAL,
    lyrics_segment TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Video Renders
CREATE TABLE IF NOT EXISTS video_renders (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES video_projects(id) ON DELETE CASCADE,
    format VARCHAR(10) DEFAULT '16:9',
    file_path TEXT,
    file_size INTEGER,
    thumbnail_path TEXT,
    duration REAL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Social Accounts (OAuth tokens)
CREATE TABLE IF NOT EXISTS social_accounts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform platform_type NOT NULL,
    platform_user_id VARCHAR(255),
    platform_username VARCHAR(255),
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_expires_at TIMESTAMP,
    extra_data JSONB DEFAULT '{}'::jsonb,
    connected_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_social_accounts_user ON social_accounts(user_id);

-- Publish Jobs
CREATE TABLE IF NOT EXISTS publish_jobs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    render_id INTEGER NOT NULL REFERENCES video_renders(id),
    platform platform_type NOT NULL,
    social_account_id INTEGER NOT NULL REFERENCES social_accounts(id),
    status publish_status DEFAULT 'pending',
    title VARCHAR(500),
    description TEXT,
    tags JSONB DEFAULT '[]'::jsonb,
    scheduled_at TIMESTAMP,
    published_at TIMESTAMP,
    platform_post_id VARCHAR(255),
    platform_url TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_user ON publish_jobs(user_id);

-- Publish Schedules
CREATE TABLE IF NOT EXISTS publish_schedules (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform platform_type NOT NULL,
    social_account_id INTEGER NOT NULL REFERENCES social_accounts(id),
    frequency VARCHAR(20) DEFAULT 'daily',
    time_utc VARCHAR(5) DEFAULT '14:00',
    day_of_week INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    queue JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_publish_schedules_user ON publish_schedules(user_id);
