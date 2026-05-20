from typing import List
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, ForeignKey, delete, event
from sqlalchemy.orm import relationship, DeclarativeBase
import time

from vorte import Vorte
from vorte.modules.database import DatabaseModule
from vorte.core.router import VorteAPIRouter


# ---------------------------------------------------------------------------
# Own isolated declarative base — avoids any mapper-registry clash with
# Vorte's global Base singleton.
# ---------------------------------------------------------------------------
class AppBase(DeclarativeBase):
    pass


# --- SQLAlchemy Models ---
class Author(AppBase):
    __tablename__ = "authors"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    books = relationship("Book", back_populates="author")

class Book(AppBase):
    __tablename__ = "books"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    author_id = Column(Integer, ForeignKey("authors.id"), index=True)
    author = relationship("Author", back_populates="books")
    reviews = relationship("Review", back_populates="book")

class Review(AppBase):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(String, nullable=False)
    book_id = Column(Integer, ForeignKey("books.id"), index=True)
    book = relationship("Book", back_populates="reviews")


# --- Pydantic Response Models ---
class ReviewResponse(BaseModel):
    id: int
    text: str
    model_config = {"from_attributes": True}

class BookResponse(BaseModel):
    id: int
    title: str
    reviews: List[ReviewResponse]
    model_config = {"from_attributes": True}

class AuthorResponse(BaseModel):
    id: int
    name: str
    books: List[BookResponse]
    model_config = {"from_attributes": True}


# --- App Setup ---
app = Vorte(auto_load=False)

# PostgreSQL — vorte_main user, password 123456, database vorte.
db = DatabaseModule(
    url="postgresql+asyncpg://vorte_main:123456@localhost/vorte",
    pool_size=20,
    max_overflow=10,
    auto_create_tables=False,
)
app.register(db)

# Queue Module (memory driver)
from vorte.modules.queue.module import QueueModule
queue_mod = QueueModule(driver="memory")
app.register(queue_mod)

router = VorteAPIRouter()


# ---------------------------------------------------------------------------
# Startup hook — create tables from *our* metadata once at app startup
# ---------------------------------------------------------------------------
@app.fastapi.on_event("startup")
async def create_tables():
    async with db.connection.engine.begin() as conn:
        await conn.run_sync(AppBase.metadata.create_all, checkfirst=True)


# --- Routes ---

@app.get("/seed-heavy")
async def seed_heavy():
    """Seed 10 authors × 10 books × 10 reviews."""
    try:
        # Wipe in FK-safe order
        async with db.connection.session() as session:
            await session.execute(delete(Review))
            await session.execute(delete(Book))
            await session.execute(delete(Author))

        print(">>> Seeding…")
        for i in range(10):
            author = await db.create(Author, {"name": f"Author {i}"})
            for j in range(10):
                book = await db.create(
                    Book,
                    {"title": f"Book {j} by {author.name}", "author_id": author.id},
                )
                for k in range(10):
                    await db.create(
                        Review,
                        {"text": f"Review {k} for {book.title}", "book_id": book.id},
                    )
        print(">>> Done.")
        return {"status": "seeded", "counts": {"authors": 10, "books": 100, "reviews": 1000}}
    except Exception:
        import traceback, sys
        traceback.print_exc(file=sys.stderr)
        raise


from vorte.core.serializer import FastSerializer
from sqlalchemy import text
from fastapi import Response

@router.get("/authors-deep", response_model=List[AuthorResponse])
async def get_authors_deep(mode: str = "developer"):
    """Deep N+1 planner test with Developer vs Performance mode."""
    if mode == "performance":
        is_postgres = db.connection.engine.url.drivername.startswith("postgresql")
        async with db.connection.session() as session:
            if is_postgres:
                # Compiled, nested raw SQL query aggregated to JSON directly by PG
                query = db.get_cached_query("authors_deep_postgres_json", lambda: text("""
                    SELECT CAST(COALESCE(json_agg(json_build_object(
                        'id', a.id,
                        'name', a.name,
                        'books', COALESCE((
                            SELECT json_agg(json_build_object(
                                'id', b.id,
                                'title', b.title,
                                'reviews', COALESCE((
                                    SELECT json_agg(json_build_object(
                                        'id', r.id,
                                        'text', r.text
                                    ))
                                    FROM reviews r
                                    WHERE r.book_id = b.id
                                ), '[]'::json)
                            ))
                            FROM books b
                            WHERE b.author_id = a.id
                        ), '[]'::json)
                    )), '[]'::json) AS TEXT)
                    FROM authors a;
                """))
                result = await session.execute(query)
                json_str = result.scalar()
                # Return raw bytes directly (0% serialization or validation overhead)
                return Response(content=(json_str or "[]").encode("utf-8"), media_type="application/json")
            else:
                # Highly optimized SQLite/fallback O(N) stitching path
                q_authors = db.get_cached_query("authors_deep_sqlite_authors", lambda: text("SELECT id, name FROM authors"))
                q_books = db.get_cached_query("authors_deep_sqlite_books", lambda: text("SELECT id, title, author_id FROM books"))
                q_reviews = db.get_cached_query("authors_deep_sqlite_reviews", lambda: text("SELECT id, text, book_id FROM reviews"))
                
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

    # Developer Mode: Default SQLAlchemy ORM with look-ahead relationship planning
    return await db.find_all(Author)


# --- Background Tasks ---
def heavy_compute_task(task_id: int):
    from vorte.core.tracing import get_trace_id
    trace_id = get_trace_id()
    result = sum(i * i for i in range(500_000))
    time.sleep(0.01)
    if task_id % 50 == 0:
        print(f"[{trace_id}] Task {task_id} done: {result}")
    return result


from vorte.modules.queue.job import Job, register_job

@register_job
class HeavyComputeJob(Job):
    queue = "default"

    async def handle(self, task_id: int):
        from vorte.core.executor import VorteExecutor
        executor = VorteExecutor()
        await executor.run(heavy_compute_task, task_id)


@router.post("/background")
async def run_background():
    """Queue 500 heavy compute background tasks."""
    for i in range(500):
        await HeavyComputeJob.dispatch(task_id=i)
    return {"status": "queued", "tasks": 500}


@router.get("/ping")
async def ping():
    return {"status": "pong"}

from fastapi import WebSocket
from vorte import VorteSSEResponse

@router.get("/stream")
async def sse_stream():
    """Test SSE streaming bridge."""
    import asyncio
    async def event_generator():
        for i in range(5):
            yield {"event": "ping", "data": {"iteration": i}}
            await asyncio.sleep(0.1)
        yield {"event": "done", "data": "finished"}

    return VorteSSEResponse(event_generator())

@app.fastapi.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Test WebSocket upgrade bridge."""
    await websocket.accept()
    await websocket.send_json({"msg": "Welcome to Vorte WebSocket!"})
    for i in range(3):
        data = await websocket.receive_text()
        await websocket.send_text(f"Echo: {data}")
    await websocket.close()



app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, workers=4)
