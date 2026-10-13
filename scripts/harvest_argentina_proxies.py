# -*- coding: utf-8 -*-
import requests
import json

urls = [
    ('monosans_all', 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR.txt'),
    ('monosans_http', 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR/http.txt'),
    ('monosans_socks5', 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR/socks5.txt'),
    ('monosans_socks4', 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR/socks4.txt'),
    ('proxyscrape_http', 'https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&country=AR'),
    ('proxyscrape_socks5', 'https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&country=AR'),
    ('proxyscrape_socks4', 'https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks4&country=AR'),
    ('geonode_p1', 'https://proxylist.geonode.com/api/proxy-list?country=AR&limit=100&page=1&sort_by=lastChecked&sort_type=desc'),
    ('geonode_p2', 'https://proxylist.geonode.com/api/proxy-list?country=AR&limit=100&page=2&sort_by=lastChecked&sort_type=desc'),
    ('proxydownload_http', 'https://www.proxy-list.download/api/v1/get?type=http&country=AR'),
    ('proxydownload_https', 'https://www.proxy-list.download/api/v1/get?type=https&country=AR'),
    ('proxydownload_socks5', 'https://www.proxy-list.download/api/v1/get?type=socks5&country=AR'),
]

total = []
for name, u in urls:
    try:
        r = requests.get(u, timeout=6)
        if r.status_code == 200:
            count = 0
            if 'geonode' in u:
                for item in r.json().get('data', []):
                    ip = item.get('ip')
                    port = item.get('port')
                    protos = item.get('protocols', ['http'])
                    for p in protos:
                        total.append((p.lower(), f"{ip}:{port}", name))
                        count += 1
            else:
                proto_guess = "socks5" if "socks5" in name else ("socks4" if "socks4" in name else "http")
                for l in r.text.splitlines():
                    clean = l.strip()
                    if ':' in clean and not clean.startswith('#'):
                        total.append((proto_guess, clean, name))
                        count += 1
            print(f"[{name}] Obtenidos: {count}")
    except Exception as e:
        print(f"[{name}] Error: {e}")

unique = list(set((p, hp) for p, hp, _ in total))
print(f"\nTOTAL PROXIES ARGENTINOS UNICOS ENCONTRADOS: {len(unique)}")
for proto, hp in unique[:15]:
    print(f"  * {proto}://{hp}")
