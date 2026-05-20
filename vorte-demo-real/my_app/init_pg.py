"""One-shot script: verify Postgres connection, create tables, check counts."""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine

URL = "postgresql+asyncpg://vorte_main:123456@localhost/vorte"

async def main():
    engine = create_async_engine(URL, echo=False)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(__import__("sqlalchemy").text("SELECT version()"))
            print("Postgres OK:", result.scalar())
    except Exception as e:
        print("Connection FAILED:", e)
        return
    finally:
        await engine.dispose()

    # Now create tables
    from main import AppBase
    engine2 = create_async_engine(URL, echo=True)
    async with engine2.begin() as conn:
        await conn.run_sync(AppBase.metadata.create_all, checkfirst=True)
    await engine2.dispose()
    print("Tables created (or already exist).")

asyncio.run(main())
