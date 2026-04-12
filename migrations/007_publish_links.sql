-- Add publish_links column to store user's social/important links for video descriptions
ALTER TABLE auth_users
ADD COLUMN IF NOT EXISTS publish_links TEXT DEFAULT '';
