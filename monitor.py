#!/usr/bin/env python3
"""
Monitor de elrobleperfumado.com
Detecta nuevos productos y cambios de stock/precio
Notifica por Telegram
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import time
from datetime import datetime

# ─── CONFIGURACIÓN ────────────────────────────────────────────────
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
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
# ──────────────────────────────────────────────────────────────────

session = requests.Session()
session.headers.update(HEADERS)


def cargar_estado():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_estado(estado):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def parsear_productos(html):
    soup = BeautifulSoup(html, "html.parser")
    productos = {}

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


def scrape_categoria(url):
    productos = {}
    pagina = 1

    while True:
        url_pag = url if pagina == 1 else f"{url}#/page-{pagina}"

        try:
            r = session.get(url_pag, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f"  ⚠️  Error en página {pagina}: {e}")
            break

        nuevos = parsear_productos(r.text)

        if not nuevos:
            print(f"  ✅ Sin productos en página {pagina}, fin de categoría")
            break

        ids_nuevos = set(nuevos.keys()) - set(productos.keys())
        if not ids_nuevos:
            print(f"  ⚠️  La web repite productos en página {pagina}, fin")
            break

        productos.update(nuevos)
        print(f"    página {pagina}: {len(ids_nuevos)} nuevos (Total: {len(productos)})")

        pagina += 1
        time.sleep(1)

        if pagina > 50:
            print("  ⚠️  Límite de 50 páginas alcanzado")
            break

    return productos


def comparar_y_notificar(nombre_cat, productos_nuevos, productos_anteriores):
    mensajes = []

    nuevos = {k: v for k, v in productos_nuevos.items() if k not in productos_anteriores}
    if nuevos:
        lista = "\n".join(
            f"  • <a href='{p['url']}'>{p['nombre']}</a> — {p['precio']}"
            for p in list(nuevos.values())[:10]
        )
        extra = f"\n  <i>...y {len(nuevos)-10} más</i>" if len(nuevos) > 10 else ""
        mensajes.append(f"🆕 <b>Nuevos productos en {nombre_cat}</b>\n{lista}{extra}")

    eliminados = {k: v for k, v in productos_anteriores.items() if k not in productos_nuevos}
    if 0 < len(eliminados) < 20:
        lista = "\n".join(f"  • {p['nombre']}" for p in list(eliminados.values())[:5])
        extra = f"\n  <i>...y {len(eliminados)-5} más</i>" if len(eliminados) > 5 else ""
        mensajes.append(f"❌ <b>Eliminados en {nombre_cat}</b>\n{lista}{extra}")

    cambios = []
    for k, prod_nuevo in productos_nuevos.items():
        if k in productos_anteriores:
            prod_ant = productos_anteriores[k]

            if not prod_ant.get("en_stock", True) and prod_nuevo["en_stock"]:
                cambios.append(f"  🟢 <b>¡VUELVE A HABER STOCK!</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>")
            elif prod_ant.get("en_stock", True) and not prod_nuevo["en_stock"]:
                cambios.append(f"  🔴 <b>AGOTADO:</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>")

            p_ant = prod_ant.get("precio", "")
            p_nue = prod_nuevo.get("precio", "")
            if p_ant and p_nue and p_ant != p_nue:
                cambios.append(f"  💸 <b>CAMBIO PRECIO:</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>\n  {p_ant} → <b>{p_nue}</b>")

    if cambios:
        lista = "\n\n".join(cambios[:10])
        extra = f"\n\n  <i>...y {len(cambios)-10} más</i>" if len(cambios) > 10 else ""
        mensajes.append(f"⚡ <b>Actualizaciones en {nombre_cat}</b>\n\n{lista}{extra}")

    return mensajes


def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Sin credenciales Telegram")
        print(mensaje)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if TELEGRAM_THREAD_ID:
        payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)

    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print("  ✅ Enviado a Telegram")
    except Exception as e:
        print(f"  ❌ Error Telegram: {e}")


def main():
    print(f"\n🕐 Monitor elrobleperfumado.com — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    estado_anterior = cargar_estado()
    estado_nuevo = {}
    todos_mensajes = []

    for cat in CATEGORIAS:
        nombre = cat["nombre"]
        url = cat["url"]
        print(f"\n📦 Scrapeando {nombre}...")

        productos = scrape_categoria(url)
        anteriores = estado_anterior.get(url, {})
        print(f"  → {len(productos)} productos encontrados")

        estado_nuevo[url] = productos

        if anteriores:
            msgs = comparar_y_notificar(nombre, productos, anteriores)
            todos_mensajes.extend(msgs)
        else:
            print("  ℹ️  Primera ejecución, guardando estado inicial")

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
