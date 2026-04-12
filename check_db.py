import asyncio
from app.database import async_session
from sqlalchemy import text

async def main():
    async with async_session() as db:
        r = await db.execute(text("SELECT id, name, time_utc, timezone, frequency, is_active FROM auto_schedules"))
        for row in r:
            print(dict(row._mapping))
        r2 = await db.execute(text("SELECT id, theme, status, position FROM auto_schedule_themes ORDER BY auto_schedule_id, position"))
        for row in r2:
            print(dict(row._mapping))

asyncio.run(main())
