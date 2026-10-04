import requests
from bs4 import BeautifulSoup
import re

dni = '10137476'
url = f"https://datuar.com/indext.php?busqueda={dni}"
r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

names = re.findall(r'data-nombre-completo="([^"]+)"', r.text)
edades = re.findall(r'data-edad="([^"]+)"', r.text)
cdus = re.findall(r'data-cdu="([^"]+)"', r.text)
generos = re.findall(r'data-genero="([^"]+)"', r.text)
provincias = re.findall(r'data-provincia="([^"]+)"', r.text)
ciudades = re.findall(r'data-ciudad="([^"]+)"', r.text)
municipios = re.findall(r'data-municipio="([^"]+)"', r.text)

print(f"=== DATUAR indext.php RESULT FOR DNI {dni} ===")
print("Names:     ", names)
print("Edades:    ", edades)
print("CDUs:      ", cdus)
print("Generos:   ", generos)
print("Provincias:", provincias)
print("Ciudades:  ", ciudades)
print("Municipios:", municipios)

# Also check rendered HTML cards
soup = BeautifulSoup(r.text, 'html.parser')
cards = soup.select('.item-resultado, .card, .tarjeta')
print(f"Cards found: {len(cards)}")
for c in cards[:2]:
    print("Card text:", c.get_text(" | ", strip=True))
