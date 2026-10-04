import requests

session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

for script_name in ["/js/functions.js", "/js/app.js"]:
    url = f"https://datuar.com{script_name}"
    resp = session.get(url, headers=headers)
    print(f"=== {script_name} (Status: {resp.status_code}, Len: {len(resp.text)}) ===")
    lines = resp.text.splitlines()
    for l in lines:
        if any(k in l.lower() for k in ["ajax", "url", "post", ".php", "endpoint", "busqueda", "turnstile", "indext", "index2"]):
            print(" ", l.strip()[:140])
