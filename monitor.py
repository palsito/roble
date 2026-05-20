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
        # Truco: Añadimos un timestamp para forzar una RECARGA DURA en el navegador
        # y evitar que Playwright se salte la carga por culpa de la almohadilla (#)
        timestamp = int(time.time())
        url_pag = f"{url}?t={timestamp}" if pagina == 1 else f"{url}?t={timestamp}#/page-{pagina}"
        
        print(f"    Cargando página {pagina}...")

        try:
            # wait_until="networkidle" obliga al bot a esperar a que la web deje de descargar cosas
            page.goto(url_pag, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)  # Le damos 3 segundos de margen a la web por si va lenta
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

        # Límite de seguridad
        if pagina > total or pagina > 1000:
            break

    return productos

def comparar_y_notificar(nombre_cat, productos_nuevos, productos_anteriores, ya_notificados=None):
    mensajes = []
    if ya_notificados is None:
        ya_notificados = set()

    # Límite para agrupar notificaciones (evita spam masivo en Telegram)
    LIMITE_DETALLE = 20

    # 1. Productos NUEVOS (filtrando los que ya se notificaron en otra categoría)
    nuevos = {k: v for k, v in productos_nuevos.items()
              if k not in productos_anteriores and v['nombre'] not in ya_notificados}
    if nuevos:
        # Registrar como ya notificados para las siguientes categorías
        for p in nuevos.values():
            ya_notificados.add(p['nombre'])

        if len(nuevos) <= LIMITE_DETALLE:
            lista = "\n".join(
                f"  • <a href='{p['url']}'>{p['nombre']}</a> — {p['precio']}"
                for p in nuevos.values()
            )
            mensajes.append(f"🆕 <b>Nuevos productos en {nombre_cat}</b>\n{lista}")
        else:
            # Demasiados → resumen compacto (probablemente la web se recuperó de un fallo)
            muestra = list(nuevos.values())[:5]
            lista_muestra = "\n".join(
                f"  • <a href='{p['url']}'>{p['nombre']}</a> — {p['precio']}"
                for p in muestra
            )
            mensajes.append(
                f"🆕 <b>{len(nuevos)} nuevos productos en {nombre_cat}</b>\n"
                f"(Mostrando 5 de {len(nuevos)}):\n{lista_muestra}\n"
                f"  ...y {len(nuevos) - 5} más"
            )



    # 3. Cambios de PRECIO y STOCK (filtrando ya notificados en otra categoría)
    cambios = []
    for k, prod_nuevo in productos_nuevos.items():
        if k in productos_anteriores:
            # Si ya se notificó este producto en otra categoría, saltar
            if prod_nuevo['nombre'] in ya_notificados:
                continue

            prod_ant = productos_anteriores[k]
            producto_tiene_cambio = False

            # Stock
            if not prod_ant.get("en_stock", True) and prod_nuevo["en_stock"]:
                cambios.append(f"  🟢 <b>¡VUELVE A HABER STOCK!</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>")
                producto_tiene_cambio = True

            # Precio
            p_ant = prod_ant.get("precio", "")
            p_nue = prod_nuevo.get("precio", "")
            if p_ant and p_nue and p_ant != p_nue:
                cambios.append(f"  💸 <b>CAMBIO PRECIO:</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>\n  {p_ant} → <b>{p_nue}</b>")
                producto_tiene_cambio = True

            # Marcar como notificado para que no se repita en otra categoría
            if producto_tiene_cambio:
                ya_notificados.add(prod_nuevo['nombre'])

    if cambios:
        if len(cambios) <= LIMITE_DETALLE:
            lista = "\n\n".join(cambios)
            mensajes.append(f"⚡ <b>Actualizaciones en {nombre_cat}</b>\n\n{lista}")
        else:
            lista = "\n\n".join(cambios[:10])
            mensajes.append(
                f"⚡ <b>{len(cambios)} actualizaciones en {nombre_cat}</b>\n"
                f"(Mostrando 10 de {len(cambios)}):\n\n{lista}\n\n"
                f"  ...y {len(cambios) - 10} más"
            )

    return mensajes


def enviar_telegram(mensaje):
    import requests as req
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Sin credenciales Telegram — volcando por consola:")
        print("─" * 60)
        print(mensaje)
        print("─" * 60)
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

    # Enviar cada bloque con control de Anti-Spam (Error 429)
    for i, msg in enumerate(mensajes_cortados):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        if TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)

        print(f"  📤 Enviando bloque {i+1}/{len(mensajes_cortados)} a Telegram...")

        max_reintentos = 3
        for intento in range(max_reintentos):
            try:
                r = req.post(url, json=payload, timeout=15)

                # Si Telegram nos bloquea temporalmente (Error 429)
                if r.status_code == 429:
                    espera = r.json().get("parameters", {}).get("retry_after", 5)
                    print(f"  ⏳ Telegram pide frenar. Esperando {espera} segundos...")
                    time.sleep(espera + 1)
                    continue  # Volvemos a intentar enviar el mismo bloque

                r.raise_for_status()
                print("  ✅ Enviado")
                break  # Éxito, salimos del bucle de reintentos

            except Exception as e:
                print(f"  ❌ Error Telegram: {e}")
                break  # Si es otro tipo de error, cancelamos el envío de este bloque
        
        # Pausa de 3.5 segundos entre bloques (límite de Telegram: 20 msgs / minuto)
        time.sleep(3.5)


def main():
    print(f"\n🕐 Monitor elrobleperfumado.com — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    estado_anterior = cargar_estado()
    estado_nuevo = {}
    todos_mensajes = []
    ya_notificados = set()  # Evita notificar el mismo producto en varias categorías

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

            # ── PROTECCIÓN ANTI-SCRAPING-FALLIDO ──────────────────────
            # Si la categoría antes tenía productos y ahora devuelve muchos menos
            # (menos del 80%), probablemente la web falló o nos bloqueó.
            # En ese caso, MANTENEMOS el estado anterior para no generar
            # falsas notificaciones de "nuevos" en la siguiente ejecución.
            if anteriores and len(productos) < len(anteriores) * 0.8:
                print(f"  ⚠️  PROTECCIÓN: Se esperaban ~{len(anteriores)} productos pero solo se obtuvieron {len(productos)}.")
                print(f"  ⚠️  Esto indica un fallo de la web, NO un cambio real. Se mantiene el estado anterior.")
                estado_nuevo[url] = anteriores  # Mantener estado anterior
                continue

            # ── PROTECCIÓN ANTI-RECUPERACIÓN ──────────────────────────
            # Si de repente aparecen muchos productos "nuevos" (más de 30),
            # es probable que el scrape ANTERIOR fue parcial y ahora se
            # recuperó. Actualizamos el estado SIN notificar.
            if anteriores:
                nuevos_detectados = set(productos.keys()) - set(anteriores.keys())
                if len(nuevos_detectados) > 30:
                    print(f"  ⚠️  PROTECCIÓN ANTI-RECUPERACIÓN: Se detectaron {len(nuevos_detectados)} productos 'nuevos'.")
                    print(f"  ⚠️  Probablemente el scrape anterior fue parcial. Se actualiza estado SIN notificar.")
                    estado_nuevo[url] = productos
                    continue

            estado_nuevo[url] = productos

            if anteriores:
                msgs = comparar_y_notificar(nombre, productos, anteriores, ya_notificados)
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
