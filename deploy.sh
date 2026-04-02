#!/bin/bash
# ═══════════════════════════════════════════════
# Levita Video — Deploy to VPS
# ═══════════════════════════════════════════════
set -e

PROJECT_DIR="/opt/levita-video"

echo "📁 Creating project directory..."
mkdir -p "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR/media"

cd "$PROJECT_DIR"

echo "🏗️ Building and starting container..."
docker compose down --remove-orphans 2>/dev/null || true
docker compose up -d --build

echo "⏳ Waiting for container startup..."
sleep 5

echo "📊 Running migrations (create tables)..."
docker exec levita-video python -c "
import asyncio
from app.database import engine
from app.models import Base

async def migrate():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('Tables created successfully')

asyncio.run(migrate())
"

echo "📊 Running SQL migrations (add new columns)..."
docker exec levita-video python -c "
import asyncio
from app.database import engine
from sqlalchemy import text

async def run_migrations():
    async with engine.begin() as conn:
        await conn.execute(text('ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS use_custom_images BOOLEAN DEFAULT FALSE'))
        await conn.execute(text('ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS enable_subtitles BOOLEAN DEFAULT TRUE'))
        await conn.execute(text('ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS zoom_images BOOLEAN DEFAULT TRUE'))
        await conn.execute(text('ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS image_display_seconds REAL DEFAULT 0'))
        await conn.execute(text('ALTER TABLE video_projects ADD COLUMN IF NOT EXISTS no_background_music BOOLEAN DEFAULT FALSE'))
        await conn.execute(text('ALTER TABLE video_scenes ADD COLUMN IF NOT EXISTS is_user_uploaded BOOLEAN DEFAULT FALSE'))
        # Image Bank table
        await conn.execute(text('''
            CREATE TABLE IF NOT EXISTS image_bank (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                style TEXT NOT NULL DEFAULT '',
                aspect_ratio VARCHAR(10) NOT NULL DEFAULT '16:9',
                prompt TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL,
                reuse_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        '''))
        await conn.execute(text('CREATE INDEX IF NOT EXISTS idx_image_bank_user_aspect ON image_bank (user_id, aspect_ratio)'))
        await conn.execute(text('CREATE INDEX IF NOT EXISTS idx_image_bank_tags ON image_bank USING GIN (tags)'))
        # Credits system
        await conn.execute(text('ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS credits INTEGER NOT NULL DEFAULT 50'))
        await conn.execute(text('''
            CREATE TABLE IF NOT EXISTS credit_purchases (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                credits INTEGER NOT NULL,
                amount REAL NOT NULL,
                type VARCHAR(20) NOT NULL DEFAULT 'pix',
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                reference VARCHAR(100) NOT NULL UNIQUE,
                mp_payment_id VARCHAR(100),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        '''))
        await conn.execute(text('CREATE INDEX IF NOT EXISTS idx_credit_purchases_user ON credit_purchases (user_id)'))
        await conn.execute(text('CREATE INDEX IF NOT EXISTS idx_credit_purchases_ref ON credit_purchases (reference)'))
    print('SQL migrations applied successfully')

asyncio.run(run_migrations())
"

echo "✅ Deploy complete!"
echo "🌐 Dashboard: http://$(hostname -I | awk '{print \$1}'):8002/video"
echo "📡 API: http://$(hostname -I | awk '{print \$1}'):8002/api/"
echo ""
docker compose logs --tail=20 levita-video
