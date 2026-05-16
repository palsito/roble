#!/usr/bin/env python3
"""
Monitor de elrobleperfumado.com
Usa Playwright para evadir el bloqueo 403
Sin límites de avisos y con protección anti-crash
"""

import json
import os
import time
import re
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")

BASE_URL = "https://elrobleperfumado.com"

CATEGORIAS = [
    {"nombre": "💎 Perfumes Nicho",          "url": f"{BASE_URL}/perfumes-nicho"},
    {"nombre": "💀 Perfumes Descatalogados",  "url": f"{BASE_URL}/perfumes-descatalogados"},
    {"nombre": "🔥 Ofertones",                "url": f"{BASE_URL}/ofertones"},
    {"nombre": "👨 Perfumes Hombre",          "url": f"{BASE_URL}/perfumes-hombre"},
    {"nombre": "👩 Perfumes Mujer",           "url": f"{BASE_URL}/perfumes-mujer"},
]

STATE_FILE = "estado_productos_roble.json"


def cargar_estado():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"  ⚠️ Archivo de estado corrupto ignorado ({e}). Empezando de cero.")
            return {}
    return {}


def guardar_estado(estado):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def parsear_productos(html):
    soup = BeautifulSoup(html, "html.parser")
    productos = {}

    # CORREGIDO: Cambiamos product_list.select por soup.select
    for art in soup.select("li.ajax_block_product"):
        link = art.select_one("a.product-name")
        if not link:
            continue

        nombre = link.get_text(strip=True)
        href = link.get("href", "")
        if not nombre or not href:
            continue

        producto_id = href.rstrip("/").split("/")[-1].replace(".html", "")

        precio_elem = art.select_one("span.price.product-price")
        precio = precio_elem.get_text(strip=True) if precio_elem else "Sin precio"

        stock_elem = art.select_one("span.instock")
        en_stock = stock_elem is not None

        productos[producto_id] = {
            "nombre": nombre,
            "precio": precio,
            "url": href,
            "en_stock": en_stock,
        }

    return productos


def obtener_total_paginas(html):
    """Busca el número total de páginas en la paginación."""
    soup = BeautifulSoup(html, "html.parser")
    nums = []
    for a in soup.select("ul.pagination li a"):
        txt = a.get_text(strip=True)
        if txt.isdigit():
            nums.append(int(txt))
    return max(nums) if nums else 1


def scrape_categoria(page, url):
    productos = {}
    pagina = 1

    while True:
        url_pag = url if pagina == 1 else f"{url}#/page-{pagina}"
        print(f"    Cargando página {pagina}...")

        try:
            page.goto(url_pag, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)  # esperar JS
        except Exception as e:
            print(f"  ⚠️  Error en página {pagina}: {e}")
            break

        html = page.content()
        nuevos = parsear_productos(html)

        if not nuevos:
            print(f"  ✅ Sin productos en página {pagina}, fin de categoría")
            break

        ids_nuevos = set(nuevos.keys()) - set(productos.keys())
        if not ids_nuevos:
            print(f"  ⚠️  La web repite productos en página {pagina}, fin")
            break

        # En la primera página obtenemos el total de páginas
        if pagina == 1:
            total = obtener_total_paginas(html)
            print(f"    Total páginas detectadas: {total}")

        productos.update(nuevos)
        print(f"    página {pagina}: {len(ids_nuevos)} nuevos (Total: {len(productos)})")

        if pagina == 1 and total == 1:
            break

        pagina += 1
        time.sleep(1)

        # Aumentamos el límite de seguridad de 50 a 1000 por si hay muchas páginas
        if pagina > total or pagina > 1000:
            break

    return productos


def comparar_y_notificar(nombre_cat, productos_nuevos, productos_anteriores):
    mensajes = []

    # 1. Productos NUEVOS (Sin límites)
    nuevos = {k: v for k, v in productos_nuevos.items() if k not in productos_anteriores}
    if nuevos:
        lista = "\n".join(
            f"  • <a href='{p['url']}'>{p['nombre']}</a> — {p['precio']}"
            for p in nuevos.values()
        )
        mensajes.append(f"🆕 <b>Nuevos productos en {nombre_cat}</b>\n{lista}")

    # 2. Productos ELIMINADOS (Sin límites)
    eliminados = {k: v for k, v in productos_anteriores.items() if k not in productos_nuevos}
    if eliminados:
        lista = "\n".join(f"  • {p['nombre']}" for p in eliminados.values())
        mensajes.append(f"❌ <b>Eliminados en {nombre_cat}</b>\n{lista}")

    # 3. Cambios de PRECIO y STOCK (Sin límites)
    cambios = []
    for k, prod_nuevo in productos_nuevos.items():
        if k in productos_anteriores:
            prod_ant = productos_anteriores[k]

            # Stock
            if not prod_ant.get("en_stock", True) and prod_nuevo["en_stock"]:
                cambios.append(f"  🟢 <b>¡VUELVE A HABER STOCK!</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>")
            elif prod_ant.get("en_stock", True) and not prod_nuevo["en_stock"]:
                cambios.append(f"  🔴 <b>AGOTADO:</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>")

            # Precio
            p_ant = prod_ant.get("precio", "")
            p_nue = prod_nuevo.get("precio", "")
            if p_ant and p_nue and p_ant != p_nue:
                cambios.append(f"  💸 <b>CAMBIO PRECIO:</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>\n  {p_ant} → <b>{p_nue}</b>")

    if cambios:
        lista = "\n\n".join(cambios)
        mensajes.append(f"⚡ <b>Actualizaciones en {nombre_cat}</b>\n\n{lista}")

    return mensajes


def enviar_telegram(mensaje):
    import requests as req
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Sin credenciales Telegram")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Límite seguro de Telegram
    limite_caracteres = 4000
    mensajes_cortados = []
    
    # Lógica para dividir mensajes largos sin romper HTML
    if len(mensaje) <= limite_caracteres:
        mensajes_cortados.append(mensaje)
    else:
        lineas = mensaje.split('\n')
        bloque_actual = ""
        for linea in lineas:
            if len(bloque_actual) + len(linea) + 1 > limite_caracteres:
                mensajes_cortados.append(bloque_actual.strip())
                bloque_actual = linea + "\n"
            else:
                bloque_actual += linea + "\n"
        if bloque_actual:
            mensajes_cortados.append(bloque_actual.strip())

    # Enviar cada bloque
    for i, msg in enumerate(mensajes_cortados):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        if TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)

        try:
            r = req.post(url, json=payload, timeout=10)
            r.raise_for_status()
            print(f"  ✅ Enviado bloque {i+1}/{len(mensajes_cortados)} a Telegram")
        except Exception as e:
            print(f"  ❌ Error Telegram: {e}")
        
        # Pausa de 1 segundo entre bloques
        time.sleep(1)


def main():
    print(f"\n🕐 Monitor elrobleperfumado.com — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    estado_anterior = cargar_estado()
    estado_nuevo = {}
    todos_mensajes = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-ES",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        for cat in CATEGORIAS:
            nombre = cat["nombre"]
            url = cat["url"]
            print(f"\n📦 Scrapeando {nombre}...")

            productos = scrape_categoria(page, url)
            anteriores = estado_anterior.get(url, {})
            print(f"  → {len(productos)} productos encontrados")

            estado_nuevo[url] = productos

            if anteriores:
                msgs = comparar_y_notificar(nombre, productos, anteriores)
                todos_mensajes.extend(msgs)
            else:
                print("  ℹ️  Primera ejecución, guardando estado inicial")

        browser.close()

    if todos_mensajes:
        print(f"\n📣 {len(todos_mensajes)} notificaciones")
        for msg in todos_mensajes:
            enviar_telegram(msg)
    else:
        print("\n✅ Sin cambios detectados")

    guardar_estado(estado_nuevo)
    print("\n💾 Estado guardado\n")


if __name__ == "__main__":
    main()
