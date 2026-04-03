-- Add is_karaoke flag to video_projects for single-image karaoke mode
ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS is_karaoke BOOLEAN NOT NULL DEFAULT FALSE;
