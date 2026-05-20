import asyncio
from fastapi.testclient import TestClient
from main import app

def main():
    print("Initializing TestClient (this triggers lifespan/startup)...")
    try:
        # TestClient context manager triggers startup
        with TestClient(app.fastapi) as client:
            print("Lifespan startup executed successfully!")
            print("Sending GET to /seed-heavy...")
            response = client.get("/seed-heavy")
            print("Response Status:", response.status_code)
            print("Response Text:", response.text)
    except Exception as e:
        print("Exception caught:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
