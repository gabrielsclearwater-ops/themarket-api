import requests
from fastapi import FastAPI
from functools import lru_cache

app = FastAPI()

# -----------------------------
# CACHING (prevents rate limits)
# -----------------------------
@lru_cache(maxsize=256)
def cached_get(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()

# -----------------------------
# STOCK PRICE (Yahoo Quote API)
# -----------------------------
@app.get("/price/{ticker}")
def get_price(ticker: str):
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    try:
        data = cached_get(url)
        result = data["quoteResponse"]["result"]
        if not result:
            return {"error": "Invalid ticker"}
        price = result[0].get("regularMarketPrice")
        return {"ticker": ticker.upper(), "price": price}
    except Exception as e:
        return {"error": str(e)}

# -----------------------------
# CRYPTO PRICE (CoinGecko)
# -----------------------------
@app.get("/crypto/{symbol}")
def get_crypto(symbol: str):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd"
    try:
        data = cached_get(url)
        if symbol not in data:
            return {"error": "Invalid crypto symbol"}
        return {"symbol": symbol.upper(), "price": data[symbol]["usd"]}
    except Exception as e:
        return {"error": str(e)}

# -----------------------------
# FUTURES (Yahoo Quote API)
# Example: CL=F, ES=F, GC=F
# -----------------------------
@app.get("/futures/{symbol}")
def get_futures(symbol: str):
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
    try:
        data = cached_get(url)
        result = data["quoteResponse"]["result"]
        if not result:
            return {"error": "Invalid futures symbol"}
        price = result[0].get("regularMarketPrice")
        return {"symbol": symbol.upper(), "price": price}
    except Exception as e:
        return {"error": str(e)}

# -----------------------------
# INDEXES (Yahoo Quote API)
# Example: ^GSPC, ^NDX, ^DJI
# -----------------------------
@app.get("/index/{symbol}")
def get_index(symbol: str):
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
    try:
        data = cached_get(url)
        result = data["quoteResponse"]["result"]
        if not result:
            return {"error": "Invalid index symbol"}
        price = result[0].get("regularMarketPrice")
        return {"symbol": symbol.upper(), "price": price}
    except Exception as e:
        return {"error": str(e)}
