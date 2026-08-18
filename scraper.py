"""
Bot de Cartelera Virtual - Facultad de Informática UNLP
Revisa la cartelera general, filtra por las materias de interés,
y manda por WhatsApp (via CallMeBot) las novedades que no se vieron antes.
"""

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import requests
from playwright.async_api import async_playwright

URL = "https://gestiondocente.info.unlp.edu.ar/cartelera/"
SEEN_FILE = Path("data/seen.json")

# Materias a monitorear (coincidencia parcial, sin importar mayúsculas
# ni prefijos como "Redictado -" o sufijos entre paréntesis)
MATERIAS = [
    "algoritmos y programación i",
    "algoritmos y programación ii",
    "matemática a",
    "matemática b",
    "matemática c",
    "matemática d",
    "base de datos",
    "introducción a base de datos",
    "taller de introducción a la algorítmica",
    "taller de matemática",
    "taller de lenguajes",
    "fundamentos de arquitectura de computadoras",
    "ingeniería de software",
    "minería de datos",
    "visualización de grandes volúmenes de datos",
    "conceptos y aplicaciones de big data",
]

# Patrón: "Materia - dd/mm/aaaa hh:mm - Título...\n cuerpo... \nAutor: Nombre"
ENTRY_PATTERN = re.compile(
    r"(?P<materia>[^\n]+?)\s-\s(?P<fecha>\d{2}/\d{2}/\d{4}\s\d{2}:\d{2})\s-\s(?P<titulo>[^\n]+)\n"
    r"(?P<cuerpo>.*?)\nAutor:\s(?P<autor>[^\n]+)",
    re.DOTALL,
)


def materia_interesa(materia: str) -> bool:
    t = materia.lower()
    return any(m in t for m in MATERIAS)


async def obtener_texto_cartelera() -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)  # margen para que cargue el AJAX
        texto = await page.inner_text("body")
        await browser.close()
        return texto


def parsear_avisos(texto: str):
    avisos = []
    for m in ENTRY_PATTERN.finditer(texto):
        avisos.append(
            {
                "materia": m.group("materia").strip(),
                "fecha": m.group("fecha").strip(),
                "titulo": m.group("titulo").strip(),
                "cuerpo": m.group("cuerpo").strip(),
                "autor": m.group("autor").strip(),
            }
        )
    return avisos


def hash_aviso(aviso: dict) -> str:
    base = f"{aviso['materia']}|{aviso['fecha']}|{aviso['titulo']}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def cargar_vistos() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def guardar_vistos(vistos: set):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(
        json.dumps(sorted(vistos), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def enviar_whatsapp(texto: str):
    phone = os.environ["PHONE_NUMBER"]
    apikey = os.environ["CALLMEBOT_APIKEY"]
    resp = requests.get(
        "https://api.callmebot.com/whatsapp.php",
        params={"phone": phone, "text": texto, "apikey": apikey},
        timeout=30,
    )
    print("CallMeBot respondió:", resp.status_code, resp.text[:200])


def main():
    texto = asyncio.run(obtener_texto_cartelera())
    avisos = parsear_avisos(texto)
    vistos = cargar_vistos()
    nuevos = 0

    for aviso in avisos:
        if not materia_interesa(aviso["materia"]):
            continue
        h = hash_aviso(aviso)
        if h in vistos:
            continue

        mensaje = (
            f"📢 {aviso['materia']}\n"
            f"🗓️ {aviso['fecha']}\n"
            f"📌 {aviso['titulo']}\n\n"
            f"{aviso['cuerpo'][:500]}\n\n"
            f"👤 {aviso['autor']}"
        )
        enviar_whatsapp(mensaje)
        vistos.add(h)
        nuevos += 1

    guardar_vistos(vistos)
    print(f"Listo. Avisos nuevos enviados: {nuevos}")


if __name__ == "__main__":
    main()
