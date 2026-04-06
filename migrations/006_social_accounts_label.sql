-- Add custom label for social account aliases (multi-channel support)
ALTER TABLE social_accounts
ADD COLUMN IF NOT EXISTS account_label VARCHAR(255);
