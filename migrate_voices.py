import asyncio
from app.database import engine
from app.models import Base

async def migrate():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tables OK")

asyncio.run(migrate())
