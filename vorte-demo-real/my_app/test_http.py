import httpx

def main():
    url = "http://127.0.0.1:8000/seed-heavy"
    print(f"Requesting {url}...")
    try:
        response = httpx.get(url)
        print("Status Code:", response.status_code)
        print("Headers:", dict(response.headers))
        print("Body:", response.text)
    except Exception as e:
        print("HTTP request failed:", e)

if __name__ == "__main__":
    main()
