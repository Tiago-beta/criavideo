-- Piloto automatico Shorts: pre-aprovacao tacita + persona de local + override de motor
-- 2026-05-28

ALTER TABLE auto_channel_pilots ADD COLUMN IF NOT EXISTS engine_id VARCHAR(40) NOT NULL DEFAULT 'mega15';
ALTER TABLE auto_channel_pilots ADD COLUMN IF NOT EXISTS engine_duration_seconds INTEGER NOT NULL DEFAULT 10;
ALTER TABLE auto_channel_pilots ADD COLUMN IF NOT EXISTS auto_approval_window_minutes INTEGER NOT NULL DEFAULT 60;
ALTER TABLE auto_channel_pilots ADD COLUMN IF NOT EXISTS location_persona_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE auto_channel_pilots ADD COLUMN IF NOT EXISTS shorts_only BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE auto_schedule_themes ADD COLUMN IF NOT EXISTS approval_status VARCHAR(20);
ALTER TABLE auto_schedule_themes ADD COLUMN IF NOT EXISTS approval_deadline_at TIMESTAMP;
ALTER TABLE auto_schedule_themes ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP;
ALTER TABLE auto_schedule_themes ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMP;
ALTER TABLE auto_schedule_themes ADD COLUMN IF NOT EXISTS rejection_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE auto_schedule_themes ADD COLUMN IF NOT EXISTS preview_prompt TEXT NOT NULL DEFAULT '';
ALTER TABLE auto_schedule_themes ADD COLUMN IF NOT EXISTS preview_image_url TEXT NOT NULL DEFAULT '';
ALTER TABLE auto_schedule_themes ADD COLUMN IF NOT EXISTS preview_plan JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_auto_schedule_themes_approval_status ON auto_schedule_themes(approval_status);
CREATE INDEX IF NOT EXISTS idx_auto_schedule_themes_approval_deadline ON auto_schedule_themes(approval_deadline_at);
