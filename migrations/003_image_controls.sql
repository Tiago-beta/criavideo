-- Migration: Add image zoom and image display timing controls
-- Run against the existing PostgreSQL database

ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS zoom_images BOOLEAN DEFAULT TRUE;
ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS image_display_seconds REAL DEFAULT 0;
