import asyncio
from app.database import async_session
from sqlalchemy import text

async def main():
    async with async_session() as db:
        await db.execute(text("UPDATE auto_schedule_themes SET status='pending', error_message=NULL WHERE id=3"))
        await db.commit()
        print("Theme 3 reset to pending")

asyncio.run(main())
