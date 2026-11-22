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


async def _cargar_pagina(ignorar_https: bool) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            context = await browser.new_context(ignore_https_errors=ignorar_https)
            page = await context.new_page()
            await page.goto(URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(3000)  # margen para que cargue el AJAX
            return await page.inner_text("body")
        finally:
            await browser.close()


async def obtener_texto_cartelera() -> str:
    """Reintenta hasta 3 veces ante fallos puntuales del sitio.

    Los dos primeros intentos validan el certificado HTTPS normalmente.
    Si el sitio de la facultad sigue con el certificado inválido, el último
    intento lo ignora (solo se lee una página pública, no se envía ningún dato).
    """
    intentos = 3
    for i in range(1, intentos + 1):
        ignorar = i == intentos
        try:
            if ignorar:
                print("Último intento: se ignoran errores de certificado HTTPS.")
            return await _cargar_pagina(ignorar)
        except Exception as e:
            print(f"Intento {i}/{intentos} falló: {str(e)[:200]}")
            if i == intentos:
                raise
            await asyncio.sleep(20 * i)


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


def enviar_whatsapp(texto: str) -> bool:
    phone = os.environ["PHONE_NUMBER"]
    apikey = os.environ["CALLMEBOT_APIKEY"]
    resp = requests.get(
        "https://api.callmebot.com/whatsapp.php",
        params={"phone": phone, "text": texto, "apikey": apikey},
        timeout=30,
    )
    print("CallMeBot respondió:", resp.status_code, resp.text[:200])
    # CallMeBot devuelve 200 y "Message queued" cuando realmente lo envía.
    # Cualquier otra cosa (ej. error de formato) no debe marcarse como enviado.
    return resp.status_code == 200 and "queued" in resp.text.lower()


def construir_mensaje(avisos_nuevos: list) -> str:
    partes = []
    for aviso in avisos_nuevos:
        partes.append(
            f"📢 {aviso['materia']}\n"
            f"🗓️ {aviso['fecha']}\n"
            f"📌 {aviso['titulo']}\n\n"
            f"{aviso['cuerpo'][:300]}\n"
            f"👤 {aviso['autor']}"
        )
    mensaje = "\n\n➖➖➖\n\n".join(partes)
    if len(mensaje) > 3000:
        mensaje = mensaje[:3000] + "\n\n(hay más avisos, recortado por espacio)"
    return mensaje


def main():
    texto = asyncio.run(obtener_texto_cartelera())
    avisos = parsear_avisos(texto)
    vistos = cargar_vistos()

    avisos_nuevos = []
    for aviso in avisos:
        if not materia_interesa(aviso["materia"]):
            continue
        if hash_aviso(aviso) in vistos:
            continue
        avisos_nuevos.append(aviso)

    if not avisos_nuevos:
        print("Sin novedades.")
        return

    mensaje = construir_mensaje(avisos_nuevos)
    if enviar_whatsapp(mensaje):
        for aviso in avisos_nuevos:
            vistos.add(hash_aviso(aviso))
        guardar_vistos(vistos)
        print(f"Listo. Avisos nuevos enviados en un solo mensaje: {len(avisos_nuevos)}")
    else:
        print("No se pudo enviar el mensaje. No se marca nada como visto (se reintentará en la próxima corrida).")


if __name__ == "__main__":
    main()
