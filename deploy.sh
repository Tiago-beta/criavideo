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

echo "✅ Deploy complete!"
echo "🌐 Dashboard: http://$(hostname -I | awk '{print \$1}'):8002/video"
echo "📡 API: http://$(hostname -I | awk '{print \$1}'):8002/api/"
echo ""
docker compose logs --tail=20 levita-video
