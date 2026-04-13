-- Realistic video generation via Seedance 2.0
ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS is_realistic BOOLEAN DEFAULT false;
