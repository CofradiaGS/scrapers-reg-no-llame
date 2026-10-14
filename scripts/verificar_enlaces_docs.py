# -*- coding: utf-8 -*-
import os
import re
import urllib.parse
from pathlib import Path

# Normalizar paths de docs a la ruta actual del workspace
ws_path = Path(__file__).resolve().parent.parent
docs_dir = ws_path / "docs"

for md in docs_dir.rglob("*.md"):
    content = md.read_text(encoding="utf-8", errors="ignore")
    modified = False
    if "Users/Usuario" in content:
        content = content.replace("Users/Usuario", "Users/automatizacion.crm")
        modified = True
    if modified:
        md.write_text(content, encoding="utf-8")

broken = []
for md in docs_dir.rglob("*.md"):
    t = re.sub(r"```.*?```", "", md.read_text(encoding="utf-8", errors="ignore"), flags=re.DOTALL)
    for txt, l in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", t):
        if l.startswith("file:///"):
            clean_l = urllib.parse.unquote(l.replace("file:///", "")).split("#")[0].lstrip("/" if os.name == "nt" else "")
            p = Path(clean_l)
            if not p.exists():
                broken.append((md.name, txt, l))
        elif not l.startswith(("http", "#", "mailto:")):
            p = (md.parent / urllib.parse.unquote(l.split("#")[0])).resolve()
            if not p.exists():
                broken.append((md.name, txt, l))

print(f"Total enlaces analizados. Rotos encontrados: {len(broken)}")
if broken:
    for f, txt, l in broken:
        print(f"  [ROTO] {f}: {txt} -> {l}")
    exit(1)
