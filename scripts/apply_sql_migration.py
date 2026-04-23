import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

from app.database import engine


async def apply_sql_file(sql_file: str) -> None:
    sql_path = Path(sql_file)
    if not sql_path.exists():
        raise FileNotFoundError(f"SQL migration file not found: {sql_file}")

    raw_sql = sql_path.read_text(encoding="utf-8")
    statements = [stmt.strip() for stmt in raw_sql.split(";") if stmt.strip()]

    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/apply_sql_migration.py <path-to-sql-file>")
        raise SystemExit(1)

    target_file = sys.argv[1]
    asyncio.run(apply_sql_file(target_file))
    print(f"Applied migration: {target_file}")
