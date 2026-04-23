-- Pilot music cycle expansion: dual schedules (long + shorts) and cycle tracking
ALTER TABLE auto_channel_pilots
    ADD COLUMN IF NOT EXISTS long_schedule_id INTEGER REFERENCES auto_schedules(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS shorts_schedule_id INTEGER REFERENCES auto_schedules(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS channel_mode TEXT NOT NULL DEFAULT 'auto',
    ADD COLUMN IF NOT EXISTS short_mix_mode TEXT NOT NULL DEFAULT 'realistic_all',
    ADD COLUMN IF NOT EXISTS shorts_per_cycle INTEGER NOT NULL DEFAULT 3;

CREATE TABLE IF NOT EXISTS auto_pilot_cycle_runs (
    id SERIAL PRIMARY KEY,
    pilot_id INTEGER NOT NULL REFERENCES auto_channel_pilots(id) ON DELETE CASCADE,
    cycle_key VARCHAR(120) NOT NULL UNIQUE,
    base_theme TEXT NOT NULL,
    long_theme_id INTEGER REFERENCES auto_schedule_themes(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    planned_shorts INTEGER NOT NULL DEFAULT 3,
    completed_shorts INTEGER NOT NULL DEFAULT 0,
    short_mix_mode TEXT NOT NULL DEFAULT 'realistic_all',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_pilot_cycle_runs_pilot ON auto_pilot_cycle_runs(pilot_id);
CREATE INDEX IF NOT EXISTS idx_auto_pilot_cycle_runs_status ON auto_pilot_cycle_runs(status);
CREATE INDEX IF NOT EXISTS idx_auto_pilot_cycle_runs_created ON auto_pilot_cycle_runs(created_at DESC);
