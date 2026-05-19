import asyncio
from sqlalchemy import Column, Integer, String
from vorte.modules.database import DatabaseModule, VorteModel

class Author(VorteModel):
    __tablename__ = "authors"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)

async def main():
    db = DatabaseModule(url="sqlite+aiosqlite:///:memory:")
    # We must mock register and initialize
    db._url = "sqlite+aiosqlite:///:memory:"
    class MockApp: pass
    db.register(MockApp())
    
    await db.on_startup()
    try:
        res = await db.find_all(Author)
        print("Success:", res)
    except Exception as e:
        print("Error:", repr(e))

if __name__ == "__main__":
    asyncio.run(main())
