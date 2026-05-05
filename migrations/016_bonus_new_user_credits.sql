-- Increase default credits for new users and grant a one-time +50 bonus to existing accounts.

ALTER TABLE auth_users
ALTER COLUMN credits SET DEFAULT 100;

CREATE TABLE IF NOT EXISTS system_credit_grants (
    id SERIAL PRIMARY KEY,
    grant_key VARCHAR(120) NOT NULL,
    user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    credits INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (grant_key, user_id)
);

WITH granted AS (
    INSERT INTO system_credit_grants (grant_key, user_id, credits)
    SELECT 'system_bonus_existing_accounts_20260504', u.id, 50
    FROM auth_users AS u
    ON CONFLICT (grant_key, user_id) DO NOTHING
    RETURNING user_id, credits
)
UPDATE auth_users AS u
SET credits = u.credits + granted.credits
FROM granted
WHERE u.id = granted.user_id;