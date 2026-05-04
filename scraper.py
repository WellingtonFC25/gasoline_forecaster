"""
GasBuddy NYC Gas Price Scraper
================================
Scraper automatico de precios de gasolina para Nueva York
Se ejecuta diariamente via GitHub Actions a las 17:00 hora de NY

INSTRUCCIONES:
- Reemplaza este archivo con tu codigo real del scraper
- Asegurate de que guarda los datos en la carpeta data/
- Formato de salida recomendado: data/gas_prices_NYC.csv
"""

import os
import csv
import datetime
import requests
from bs4 import BeautifulSoup
import pandas as pd

# ============================================================
# CONFIGURACION
# ============================================================
OUTPUT_DIR = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "gas_prices_NYC.csv")
TODAY = datetime.date.today().strftime("%Y-%m-%d")


def ensure_data_dir():
    """Crea la carpeta data/ si no existe."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def scrape_gasbuddy_nyc():
    """
    Funcion principal del scraper.
    REEMPLAZA ESTE CODIGO con tu scraper real de GasBuddy.
    """
    print(f"[{TODAY}] Iniciando scraper de precios de gasolina NYC...")
    
    # === PON TU CODIGO DE SCRAPING AQUI ===
    # Ejemplo de estructura esperada:
    # prices = [
    #     {"date": TODAY, "station": "BP - 5th Ave", "price": 3.45, "type": "Regular"},
    #     {"date": TODAY, "station": "Shell - Broadway", "price": 3.52, "type": "Regular"},
    # ]
    
    # PLACEHOLDER - reemplazar con scraper real
    prices = []
    print("AVISO: Reemplaza este archivo con tu scraper real de GasBuddy")
    return prices


def save_to_csv(prices):
    """Guarda los precios en CSV."""
    if not prices:
        print("No hay datos para guardar.")
        return
    scraper.py
    ensure_data_dir()
    file_exists = os.path.isfile(OUTPUT_FILE)
    
    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        fieldnames = ["date", "station", "price", "type"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerows(prices)
    
    print(f"Guardados {len(prices)} registros en {OUTPUT_FILE}")


if __name__ == "__main__":
    ensure_data_dir()
    prices = scrape_gasbuddy_nyc()
    save_to_csv(prices)
    print("Scraper completado.")
