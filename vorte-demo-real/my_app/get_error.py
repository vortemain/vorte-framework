import urllib.request
import urllib.error

try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/seed-heavy")
    print("OK:", r.read().decode())
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print("STATUS:", e.code)
    print("BODY:", body)
