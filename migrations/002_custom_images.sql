-- Migration: Add custom images and subtitle toggle support
-- Run against the existing PostgreSQL database

-- Add use_custom_images flag to video_projects
ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS use_custom_images BOOLEAN DEFAULT FALSE;

-- Add enable_subtitles toggle to video_projects
ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS enable_subtitles BOOLEAN DEFAULT TRUE;

-- Add is_user_uploaded flag to video_scenes
ALTER TABLE video_scenes ADD COLUMN IF NOT EXISTS is_user_uploaded BOOLEAN DEFAULT FALSE;
