import asyncio
import aiohttp
import time

async def fetch(session, url):
    start = time.time()
    async with session.get(url) as response:
        await response.read()
        return time.time() - start

async def benchmark(url, concurrency, total_requests):
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as session:
        async def bound_fetch():
            async with sem:
                return await fetch(session, url)
        
        tasks = [asyncio.create_task(bound_fetch()) for _ in range(total_requests)]
        start_time = time.time()
        latencies = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        avg_latency = (sum(latencies) / len(latencies)) * 1000
        req_per_sec = total_requests / total_time
        return total_time, req_per_sec, avg_latency

async def main():
    print("[+] Warming up server...")
    await asyncio.sleep(1)
    
    print("[+] Triggering heavy DB seeding...")
    async with aiohttp.ClientSession() as session:
        async with session.get('http://localhost:8000/seed-heavy') as resp:
            print("    - Seed response:", await resp.json())
    
    concurrency = 20
    total_requests = 100
    
    print(f"\n[+] Running benchmark with concurrency={concurrency}, total_requests={total_requests}...")
    
    # Test Developer Mode
    print("[*] Benchmarking Developer Mode (ORM)...")
    dev_time, dev_rps, dev_lat = await benchmark('http://localhost:8000/authors-deep?mode=developer', concurrency, total_requests)
    print(f"    - Req/Sec: {dev_rps:.2f}")
    print(f"    - Avg Latency: {dev_lat:.2f}ms")
    print(f"    - Total Time: {dev_time:.2f}s")
    
    # Test Performance Mode
    print("[*] Benchmarking Performance Mode (Nested Aggregation)...")
    perf_time, perf_rps, perf_lat = await benchmark('http://localhost:8000/authors-deep?mode=performance', concurrency, total_requests)
    print(f"    - Req/Sec: {perf_rps:.2f}")
    print(f"    - Avg Latency: {perf_lat:.2f}ms")
    print(f"    - Total Time: {perf_time:.2f}s")
    
    speedup = perf_rps / dev_rps if dev_rps > 0 else 0
    print(f"\n[=] RESULTS COMPARISON [=]")
    print(f"Throughput Speedup: {speedup:.2f}x")
    print(f"Latency Reduction: {dev_lat - perf_lat:.2f}ms ({(dev_lat - perf_lat)/dev_lat*100:.1f}% faster)")

if __name__ == "__main__":
    asyncio.run(main())
