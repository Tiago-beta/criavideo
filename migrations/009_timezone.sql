-- Add timezone column to schedules
ALTER TABLE publish_schedules ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC';
ALTER TABLE auto_schedules ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC';
