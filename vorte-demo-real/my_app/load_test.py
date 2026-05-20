import asyncio
import aiohttp
import time

async def fetch(session, url):
    try:
        start = time.time()
        async with session.get(url) as response:
            await response.read()
            return time.time() - start, response.status
    except Exception as e:
        return 0, 500

async def bound_fetch(sem, session, url):
    async with sem:
        return await fetch(session, url)

async def load_test(url, concurrency, total_requests):
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as session:
        tasks = [asyncio.create_task(bound_fetch(sem, session, url)) for _ in range(total_requests)]
        
        start_time = time.time()
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        successes = sum(1 for r in results if r[1] == 200)
        failures = sum(1 for r in results if r[1] != 200)
        latencies = [r[0] for r in results if r[1] == 200]
        
        avg_latency = (sum(latencies) / len(latencies)) * 1000 if latencies else 0
        req_per_sec = total_requests / total_time
        
        print(f"--- Load Test Results for {url} ---")
        print(f"Total Requests: {total_requests}")
        print(f"Concurrency: {concurrency}")
        print(f"Total Time: {total_time:.2f}s")
        print(f"Req/Sec: {req_per_sec:.2f}")
        print(f"Success: {successes}")
        print(f"Failed: {failures}")
        print(f"Avg Latency: {avg_latency:.2f}ms")

async def run_all():
    print("[+] Waiting for server to spin up...")
    await asyncio.sleep(2)

    print("\n[+] Triggering Heavy DB Seeding...")
    async with aiohttp.ClientSession() as session:
        async with session.get('http://localhost:8000/seed-heavy') as resp:
            print("DB Seeded:", await resp.json())
            
    # 1. Background task queues
    print("\n[+] Triggering Background Tasks...")
    async with aiohttp.ClientSession() as session:
        async with session.post('http://localhost:8000/background') as resp:
            print("Background triggered:", await resp.json())
            
    # Wait a bit to let threads spin up
    await asyncio.sleep(0.5)

    # 2. Ping while background is running
    print("\n[+] Testing /ping while 500 background tasks are running...")
    await load_test('http://localhost:8000/ping', concurrency=100, total_requests=2000)
    
    # 3. Aggressive N+1 query test (Complex Schema)
    print("\n[+] Testing Deep N+1 Planner (/authors-deep)...")
    await load_test('http://localhost:8000/authors-deep', concurrency=50, total_requests=500)

if __name__ == "__main__":
    asyncio.run(run_all())
