import requests
from bs4 import BeautifulSoup
import re

session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

print("=== 1. Fetching https://datuar.com/ ===")
r_home = session.get("https://datuar.com/", headers=headers, timeout=15)
print("Home Status:", r_home.status_code)
soup_home = BeautifulSoup(r_home.text, "html.parser")

forms = soup_home.find_all("form")
print(f"Found {len(forms)} forms:")
for f in forms:
    print("  Action:", f.get("action"), "Method:", f.get("method"))
    for inp in f.find_all(["input", "select", "button"]):
        print(f"    Input: name='{inp.get('name')}', type='{inp.get('type')}', value='{inp.get('value')}'")

scripts = [s.get("src") for s in soup_home.find_all("script") if s.get("src")]
print("\nExternal scripts:")
for s in scripts:
    print(" ", s)

inline_scripts = [s.text for s in soup_home.find_all("script") if not s.get("src") and s.text]
print(f"\nInline scripts count: {len(inline_scripts)}")
for idx, s in enumerate(inline_scripts):
    if any(k in s.lower() for k in ["ajax", "fetch", "busqueda", "url", "post", "get", "resultado"]):
        print(f"--- Inline script {idx} (excerpt) ---")
        lines = [line.strip() for line in s.splitlines() if line.strip()]
        for line in lines[:20]:
            print(" ", line)
