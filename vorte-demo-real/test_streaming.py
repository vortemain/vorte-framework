import asyncio
import httpx
import websockets

async def test_sse():
    print("Testing SSE (/stream)...")
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", "http://localhost:8000/stream") as r:
                print(f"Status: {r.status_code}")
                print(f"Headers: {r.headers}")
                async for chunk in r.aiter_text():
                    print("SSE CHUNK:", repr(chunk))
        print("SSE Test PASSED\n")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"SSE Test FAILED: {e}\n")

async def test_ws():
    print("Testing WebSocket (/ws)...")
    try:
        async with websockets.connect("ws://localhost:8000/ws") as ws:
            # Receive welcome message
            msg = await ws.recv()
            print("WS RECV:", msg)
            
            # Send and receive 3 echoes
            for i in range(3):
                await ws.send(f"Hello {i}")
                echo = await ws.recv()
                print("WS RECV:", echo)
                
        print("WebSocket Test PASSED\n")
    except Exception as e:
        print(f"WebSocket Test FAILED: {e}\n")

async def main():
    await test_sse()
    await test_ws()

if __name__ == "__main__":
    asyncio.run(main())
