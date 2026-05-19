from typing import List
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship

from vorte import Vorte
from vorte.modules.database import DatabaseModule, VorteModel

# --- SQLAlchemy Models ---
class Book(VorteModel):
    __tablename__ = "books"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    author_id = Column(Integer, ForeignKey("authors.id"))

class Author(VorteModel):
    __tablename__ = "authors"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    books = relationship("Book")

# --- Pydantic Models ---
class BookResponse(BaseModel):
    id: int
    title: str

class AuthorResponse(BaseModel):
    id: int
    name: str
    books: List[BookResponse]  # Nested relation to trigger Auto-Query Planner

app = Vorte(auto_load=False)

# Register Database (using in-memory SQLite)
db = DatabaseModule(url="sqlite+aiosqlite:///:memory:")
app.register(db)

@app.get("/seed")
async def seed():
    print(">>> Seeding database...")
    # Seed data
    author = await db.create(Author, {"name": "J.R.R. Tolkien"})
    await db.create(Book, {"title": "The Hobbit", "author_id": author.id})
    await db.create(Book, {"title": "The Fellowship of the Ring", "author_id": author.id})
    print(">>> Seeding complete!")
    return {"status": "seeded"}

from vorte.core.router import VorteAPIRouter

router = VorteAPIRouter()

@router.get("/authors", response_model=List[AuthorResponse])
async def get_authors():
    # Because of our new QueryPlanner, VORTE will infer `books` from AuthorResponse
    # and automatically run a `selectinload` to prevent N+1 query loops!
    return await db.find_all(Author)

app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
