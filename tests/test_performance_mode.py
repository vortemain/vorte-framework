import pytest
from typing import List
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship, DeclarativeBase
from sqlalchemy import text
from fastapi import Response

from vorte import Vorte
from vorte.modules.database import DatabaseModule
from vorte.core.router import VorteAPIRouter
from vorte.core.serializer import FastSerializer
from vorte.testing import VorteTestClient

# --- Local Declarative Base for Tests ---
class TestAppBase(DeclarativeBase):
    pass

class TestAuthor(TestAppBase):
    __tablename__ = "test_authors"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    books = relationship("TestBook", back_populates="author")

class TestBook(TestAppBase):
    __tablename__ = "test_books"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    author_id = Column(Integer, ForeignKey("test_authors.id"))
    author = relationship("TestAuthor", back_populates="books")
    reviews = relationship("TestReview", back_populates="book")

class TestReview(TestAppBase):
    __tablename__ = "test_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(String, nullable=False)
    book_id = Column(Integer, ForeignKey("test_books.id"))
    book = relationship("TestBook", back_populates="reviews")


# --- Pydantic Response Models ---
class TestReviewResponse(BaseModel):
    id: int
    text: str
    model_config = {"from_attributes": True}

class TestBookResponse(BaseModel):
    id: int
    title: str
    reviews: List[TestReviewResponse]
    model_config = {"from_attributes": True}

class TestAuthorResponse(BaseModel):
    id: int
    name: str
    books: List[TestBookResponse]
    model_config = {"from_attributes": True}


@pytest.mark.asyncio
async def test_developer_vs_performance_mode():
    """Verify that Developer Mode and Performance Mode return identical structures."""
    app = Vorte(auto_load=False)
    
    # In-memory SQLite for self-contained test environment
    db = DatabaseModule(
        url="sqlite+aiosqlite:///:memory:",
        auto_create_tables=False,
    )
    app.register(db)
    
    router = VorteAPIRouter()
    
    @router.get("/test-authors", response_model=List[TestAuthorResponse])
    async def get_test_authors(mode: str = "developer"):
        if mode == "performance":
            is_postgres = db.connection.engine.url.drivername.startswith("postgresql")
            async with db.connection.session() as session:
                if is_postgres:
                    # In test we won't run PG since we're using in-memory SQLite,
                    # but we mock or cover the branches.
                    pass
                else:
                    # Stitching path (which runs on SQLite)
                    q_authors = db.get_cached_query("test_sqlite_authors", lambda: text("SELECT id, name FROM test_authors"))
                    q_books = db.get_cached_query("test_sqlite_books", lambda: text("SELECT id, title, author_id FROM test_books"))
                    q_reviews = db.get_cached_query("test_sqlite_reviews", lambda: text("SELECT id, text, book_id FROM test_reviews"))
                    
                    res_auth = await session.execute(q_authors)
                    authors = [{"id": r[0], "name": r[1], "books": []} for r in res_auth.fetchall()]
                    
                    res_books = await session.execute(q_books)
                    books_by_author = {}
                    for r in res_books.fetchall():
                        books_by_author.setdefault(r[2], []).append({"id": r[0], "title": r[1], "reviews": []})
                        
                    res_rev = await session.execute(q_reviews)
                    reviews_by_book = {}
                    for r in res_rev.fetchall():
                        reviews_by_book.setdefault(r[2], []).append({"id": r[0], "text": r[1]})
                        
                    for author in authors:
                        author["books"] = books_by_author.get(author["id"], [])
                        for book in author["books"]:
                            book["reviews"] = reviews_by_book.get(book["id"], [])
                            
                    raw_bytes = FastSerializer.dumps(authors)
                    return Response(content=raw_bytes, media_type="application/json")
                    
        return await db.find_all(TestAuthor)

    app.include_router(router)
    
    # Initialize and seed database
    async with db.connection.engine.begin() as conn:
        await conn.run_sync(TestAppBase.metadata.create_all)
        
    async with db.connection.session() as session:
        author = TestAuthor(name="Author 1")
        session.add(author)
        await session.flush()
        
        book = TestBook(title="Book 1", author_id=author.id)
        session.add(book)
        await session.flush()
        
        review = TestReview(text="Great book!", book_id=book.id)
        session.add(review)
        await session.flush()
        await session.commit()
        
    async with VorteTestClient(app) as client:
        # 1. Fetch via Developer Mode
        resp_dev = await client.get("/test-authors?mode=developer")
        assert resp_dev.status_code == 200
        data_dev = resp_dev.json_data
        
        # 2. Fetch via Performance Mode
        resp_perf = await client.get("/test-authors?mode=performance")
        assert resp_perf.status_code == 200
        data_perf = resp_perf.json_data
        
        # 3. Assert exact equivalence of structure
        assert isinstance(data_dev, list)
        assert isinstance(data_perf, list)
        assert len(data_dev) == 1
        assert len(data_perf) == 1
        
        dev_author = data_dev[0]
        perf_author = data_perf[0]
        
        assert dev_author["id"] == perf_author["id"]
        assert dev_author["name"] == perf_author["name"]
        assert len(dev_author["books"]) == len(perf_author["books"])
        assert dev_author["books"][0]["title"] == perf_author["books"][0]["title"]
        assert dev_author["books"][0]["reviews"][0]["text"] == perf_author["books"][0]["reviews"][0]["text"]
