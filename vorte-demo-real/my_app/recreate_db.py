import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

URL = "postgresql+asyncpg://vorte_main:123456@localhost/vorte"

async def main():
    engine = create_async_engine(URL, echo=True)
    try:
        async with engine.begin() as conn:
            print("[+] Dropping existing tables...")
            await conn.execute(text("DROP TABLE IF EXISTS reviews, books, authors CASCADE;"))
            
        print("[+] Creating new tables with indexes...")
        from main import AppBase
        async with engine.begin() as conn:
            await conn.run_sync(AppBase.metadata.create_all, checkfirst=True)
            
        print("[+] DB tables successfully recreated!")
    except Exception as e:
        print("[-] Error during recreation:", e)
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
