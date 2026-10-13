import requests
from bs4 import BeautifulSoup
import re

dni = '10137476'
for endpoint in ['indext.php', 'index.php', 'index2.php', 'buscar.php']:
    url = f"https://datuar.com/{endpoint}?busqueda={dni}"
    r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    names = re.findall(r'data-nombre-completo="([^"]+)"', r.text)
    edades = re.findall(r'data-edad="([^"]+)"', r.text)
    cdus = re.findall(r'data-cdu="([^"]+)"', r.text)
    print(f"=== {endpoint} (Status {r.status_code}, Len {len(r.text)}) ===")
    print(f"  Names: {names}")
    print(f"  Edades: {edades}")
    print(f"  CDUs: {cdus}")
    if "ZARA" in r.text.upper() or "GUILLERMO" in r.text.upper():
        print("  FOUND ZARA/GUILLERMO IN BODY!")
