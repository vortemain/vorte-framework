import asyncio
import httpx
import random
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text, delete
from main import AppBase, Author, Book, Review

URL = "postgresql+asyncpg://vorte_main:123456@localhost/vorte"

# Realistic review templates to generate large volumes of user reviews
REVIEW_TEMPLATES = [
    "An absolute masterpiece of computer science literature. Highly recommended!",
    "The concepts are explained with extreme clarity and practical details.",
    "A must-read for any software engineer looking to improve their architecture skills.",
    "The exercises and examples are challenging but incredibly rewarding.",
    "The author does a brilliant job of breaking down complex topics into digestible pieces.",
    "Some sections are a bit dense, but the overall content is invaluable for senior roles.",
    "Highly recommended for intermediate and advanced developers alike.",
    "Great examples, although some of the code snippets could be updated to modern styles.",
    "I keep this book on my desk as a constant reference guide for database internals.",
    "This completely changed the way I think about concurrent algorithms and performance.",
    "A bit repetitive in the middle chapters, but the core thesis is extremely strong.",
    "The coverage of indexing, indexing models, and query optimization is second to none.",
    "Excellent diagrams and illustrations that make complex distributed systems easy to grasp.",
    "A timeless classic that remains deeply relevant even decades after its first release.",
    "The writing style is surprisingly engaging and keeps you hooked page after page.",
    "A highly practical guide with real-world scenarios and production-tested patterns."
]

# Fallback dataset representing real software engineering classics in case Open Library is offline
FALLBACK_DATA = [
    {"author": "Donald Knuth", "books": [
        "The Art of Computer Programming, Vol 1",
        "The Art of Computer Programming, Vol 2",
        "The Art of Computer Programming, Vol 3",
        "The Art of Computer Programming, Vol 4"
    ]},
    {"author": "Martin Fowler", "books": [
        "Refactoring: Improving the Design of Existing Code",
        "Patterns of Enterprise Application Architecture",
        "NoSQL Distilled"
    ]},
    {"author": "Robert C. Martin", "books": [
        "Clean Code: A Handbook of Agile Software Craftsmanship",
        "The Clean Coder: A Code of Conduct for Professional Programmers",
        "Clean Architecture: A Craftsman's Guide to Software Structure"
    ]},
    {"author": "Alex Xu", "books": [
        "System Design Interview – An insider's guide",
        "System Design Interview – An insider's guide, Volume 2"
    ]},
    {"author": "Thomas H. Cormen", "books": [
        "Introduction to Algorithms, Third Edition",
        "Introduction to Algorithms, Fourth Edition"
    ]},
    {"author": "Designing Data-Intensive Applications", "books": [
        "Designing Data-Intensive Applications: The Big Ideas Behind Reliable, Scalable, and Maintainable Systems"
    ]},
    {"author": "Erich Gamma", "books": [
        "Design Patterns: Elements of Reusable Object-Oriented Software"
    ]},
    {"author": "Dave Thomas", "books": [
        "The Pragmatic Programmer: Your Journey to Mastery"
    ]},
    {"author": "Jon Bentley", "books": [
        "Programming Pearls"
    ]},
    {"author": "Frederick P. Brooks Jr.", "books": [
        "The Mythical Man-Month: Essays on Software Engineering"
    ]}
]

async def fetch_real_books_from_api():
    """Fetch real books/authors from Open Library search API using httpx."""
    print("[+] Attempting to fetch real-world book data from Open Library API...")
    url = "https://openlibrary.org/search.json?q=programming+computer+science&limit=100"
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                docs = data.get("docs", [])
                
                # Group books by author
                author_books_map = {}
                for doc in docs:
                    title = doc.get("title")
                    authors = doc.get("author_name")
                    if title and authors:
                        primary_author = authors[0]
                        author_books_map.setdefault(primary_author, set()).add(title)
                
                # Format into list of dicts
                api_data = []
                for author, books in author_books_map.items():
                    api_data.append({
                        "author": author,
                        "books": list(books)
                    })
                
                print(f"[+] Successfully fetched {len(docs)} books from API, grouped into {len(api_data)} unique authors.")
                return api_data
            else:
                print(f"[-] API responded with status {response.status_code}. Using fallback local dataset.")
    except Exception as e:
        print(f"[-] Failed to fetch from Open Library API ({e}). Using fallback local dataset.")
    
    return FALLBACK_DATA

async def main():
    engine = create_async_engine(URL, echo=False)
    
    # 1. Fetch real-world data
    real_data = await fetch_real_books_from_api()
    if not real_data:
        real_data = FALLBACK_DATA
        
    print(f"\n[+] Preparing to seed database with {len(real_data)} authors...")

    try:
        async with engine.begin() as conn:
            print("[+] Dropping existing table contents safely (truncating with CASCADE)...")
            await conn.execute(text("TRUNCATE TABLE reviews, books, authors RESTART IDENTITY CASCADE;"))
            
        async with engine.connect() as conn:
            # Seed authors and books
            author_count = 0
            book_count = 0
            review_count = 0
            
            for item in real_data:
                author_name = item["author"]
                book_titles = item["books"]
                
                # Insert Author
                res_author = await conn.execute(
                    text("INSERT INTO authors (name) VALUES (:name) RETURNING id"),
                    {"name": author_name}
                )
                author_id = res_author.scalar()
                author_count += 1
                
                for title in book_titles:
                    # Insert Book
                    res_book = await conn.execute(
                        text("INSERT INTO books (title, author_id) VALUES (:title, :author_id) RETURNING id"),
                        {"title": title, "author_id": author_id}
                    )
                    book_id = res_book.scalar()
                    book_count += 1
                    
                    # Generate random number of reviews (e.g., between 8 and 15 reviews per book for a massive dataset)
                    num_reviews = random.randint(8, 15)
                    selected_reviews = random.sample(REVIEW_TEMPLATES, min(num_reviews, len(REVIEW_TEMPLATES)))
                    
                    for review_text in selected_reviews:
                        # Append variation to make each review unique
                        unique_text = f"{review_text} (User-{random.randint(100, 999)})"
                        await conn.execute(
                            text("INSERT INTO reviews (text, book_id) VALUES (:text, :book_id)"),
                            {"text": unique_text, "book_id": book_id}
                        )
                        review_count += 1
                        
            await conn.commit()
            
            print(f"\n[=] DATABASE SEEDING COMPLETED SUCCESSFULY [=]")
            print(f"    - Unique Authors Seeded: {author_count}")
            print(f"    - Real Books Seeded: {book_count}")
            print(f"    - Real-World Reviews Generated: {review_count}")
            print(f"    - Total DB Rows: {author_count + book_count + review_count}")
            print("============================================")
            
    except Exception as e:
        print("[-] Seeding failed:", e)
        import traceback
        traceback.print_exc()
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
