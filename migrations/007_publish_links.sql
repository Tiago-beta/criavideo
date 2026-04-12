-- Add publish_links column to social_accounts for per-account social/important links
ALTER TABLE social_accounts
ADD COLUMN IF NOT EXISTS publish_links TEXT DEFAULT '';
