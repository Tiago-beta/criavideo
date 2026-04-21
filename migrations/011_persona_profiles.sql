-- Persona profiles for realistic reference images
CREATE TABLE IF NOT EXISTS persona_profiles (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    persona_type VARCHAR(20) NOT NULL,
    name VARCHAR(255) NOT NULL,
    attributes JSONB DEFAULT '{}'::jsonb,
    prompt_text TEXT DEFAULT '',
    image_path TEXT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_persona_profiles_user_id ON persona_profiles(user_id);
CREATE INDEX IF NOT EXISTS ix_persona_profiles_persona_type ON persona_profiles(persona_type);
