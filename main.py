# main.py - BACKEND API per Price Alert SaaS

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from pydantic import BaseModel
from typing import Optional, List
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import uvicorn

# ============================================================
# CONFIGURAZIONE
# ============================================================

app = FastAPI()

# Permetti connessioni dal frontend (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connessione a Supabase (le variabili vengono da Railway)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================
# MODELLI DATI (cosa riceviamo dalle richieste)
# ============================================================

class ProductCreate(BaseModel):
    name: str
    asin: str
    target_price: Optional[float] = None

class ScrapeRequest(BaseModel):
    user_id: str
    product_ids: List[str]

# ============================================================
# FUNZIONE DI SCRAPING AMAZON
# ============================================================

def get_amazon_price(asin: str) -> Optional[float]:
    """
    Dato un ASIN (es. B0BDJWRK7S), restituisce il prezzo da Amazon.it
    """
    url = f"https://www.amazon.it/dp/{asin}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Cerca il prezzo in vari selettori (Amazon cambia spesso)
        selectors = [
            '.a-price-whole',
            '#priceblock_ourprice',
            '#priceblock_dealprice'
        ]
        
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                price_text = element.get_text(strip=True)
                # Pulisci il testo (togli €, spazi, ecc)
                price_text = price_text.replace('€', '').replace(',', '').replace('.', '').strip()
                try:
                    return float(price_text)
                except:
                    pass
        
        return None
        
    except Exception as e:
        print(f"Errore scraping {asin}: {e}")
        return None

# ============================================================
# API ENDPOINTS (gli URL che il frontend chiamerà)
# ============================================================

@app.get("/")
def root():
    return {"message": "Price Alert API", "status": "online"}

@app.get("/health")
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.post("/api/products")
def add_product(product: ProductCreate, user_id: str):
    """
    Aggiunge un prodotto da monitorare per un utente
    """
    # Verifica se l'ASIN è valido (scraping di test)
    test_price = get_amazon_price(product.asin)
    
    if test_price is None:
        raise HTTPException(400, f"ASIN {product.asin} non valido o prodotto non trovato")
    
    # Salva nel database
    data = {
        "user_id": user_id,
        "name": product.name,
        "asin": product.asin.upper(),
        "target_price": product.target_price,
        "current_price": test_price,
        "last_check": datetime.now().isoformat()
    }
    
    result = supabase.table("products").insert(data).execute()
    
    # Salva anche il primo prezzo nello storico
    supabase.table("price_history").insert({
        "product_id": result.data[0]["id"],
        "price": test_price,
        "checked_at": datetime.now().isoformat()
    }).execute()
    
    return {"success": True, "product": result.data[0]}

@app.get("/api/products/{user_id}")
def get_products(user_id: str):
    """
    Restituisce tutti i prodotti di un utente
    """
    result = supabase.table("products")\
        .select("*")\
        .eq("user_id", user_id)\
        .order("created_at", desc=True)\
        .execute()
    
    return {"products": result.data}

@app.post("/api/scrape")
def scrape_products(request: ScrapeRequest):
    """
    Aggiorna i prezzi di tutti i prodotti di un utente
    """
    results = []
    
    for product_id in request.product_ids:
        # Prendi il prodotto dal database
        product_result = supabase.table("products")\
            .select("*")\
            .eq("id", product_id)\
            .execute()
        
        if not product_result.data:
            continue
        
        product = product_result.data[0]
        asin = product["asin"]
        
        # Scraping prezzo attuale
        new_price = get_amazon_price(asin)
        
        if new_price:
            # Aggiorna current_price del prodotto
            supabase.table("products")\
                .update({
                    "current_price": new_price,
                    "last_check": datetime.now().isoformat()
                })\
                .eq("id", product_id)\
                .execute()
            
            # Salva nello storico
            supabase.table("price_history").insert({
                "product_id": product_id,
                "price": new_price,
                "checked_at": datetime.now().isoformat()
            }).execute()
            
            # Verifica se ha raggiunto target
            target = product.get("target_price")
            target_reached = target and new_price <= target
            
            results.append({
                "product_id": product_id,
                "name": product["name"],
                "old_price": product["current_price"],
                "new_price": new_price,
                "target_reached": target_reached
            })
    
    return {"scraped": len(results), "results": results}

@app.get("/api/history/{product_id}")
def get_history(product_id: str):
    """
    Restituisce lo storico prezzi di un prodotto
    """
    result = supabase.table("price_history")\
        .select("*")\
        .eq("product_id", product_id)\
        .order("checked_at", desc=False)\
        .limit(30)\
        .execute()
    
    # Formatta per il grafico
    history = [
        {"date": item["checked_at"][:10], "price": item["price"]}
        for item in result.data
    ]
    
    return {"history": history}

# ============================================================
# AVVIO DEL SERVER
# ============================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)