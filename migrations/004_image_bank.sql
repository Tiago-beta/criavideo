-- Image Bank: reuse AI-generated images across projects via semantic tags
CREATE TABLE IF NOT EXISTS image_bank (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],  -- array of English keywords: {'sunset','ocean','warm','beach'}
    style TEXT NOT NULL DEFAULT '',                -- style_hint used during generation
    aspect_ratio VARCHAR(10) NOT NULL DEFAULT '16:9',
    prompt TEXT NOT NULL DEFAULT '',               -- original visual_prompt
    file_path TEXT NOT NULL,                       -- absolute path to the image file
    reuse_count INTEGER NOT NULL DEFAULT 0,        -- how many times this image was reused
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_image_bank_user_aspect ON image_bank (user_id, aspect_ratio);
CREATE INDEX IF NOT EXISTS idx_image_bank_tags ON image_bank USING GIN (tags);
